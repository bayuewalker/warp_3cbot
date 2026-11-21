"""24/7 single-machine supervisor. HTTP 200 means the process is up, not that trading is allowed."""

from __future__ import annotations

import fcntl
import logging
import os
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

from .config import Settings
from .engine import Engine
from .httpapi import dispatch
from .market import MarketData
from .notify import flush_alerts
from .operator import HEALTH, publish
from .schedule import Clocks
from .state import OrderJournal
from .supervisor import ResearchGate, once
from .telemetry import log_event, send_alert, send_telegram
from .threecommas import IntegrationError, ThreeCommas
from .vibe import VibeResearch

STOP = threading.Event()


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?", 1)[0] == "/api/stream":
            self._stream()
            return
        self._serve("GET", b"")

    def _stream(self) -> None:
        from .httpapi import authorized
        from .live import BUS, iter_server_events

        header = self.headers.get("Authorization")
        cookie = self.headers.get("Cookie")
        if not authorized(header, cookie):
            payload = b'{"error":"unauthorized"}'
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        inbox = BUS.subscribe()
        if inbox is None:
            payload = b'{"error":"too_many_streams"}'
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        stop = threading.Event()
        try:
            for chunk in iter_server_events(inbox, BUS, stop, lambda: authorized(header, cookie)):
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        finally:
            BUS.unsubscribe(inbox)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if 0 < length <= 100_000 else b""
        self._serve("POST", body)

    def _serve(self, method: str, body: bytes) -> None:
        status, headers, payload = dispatch(method, self.path, self.headers, body)
        self.send_response(status)
        for key, value in headers:
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        return


def run_health(server: ThreadingHTTPServer):
    server.serve_forever()


def _refresh_live(engine, market, cfg: Settings, live_at: dict) -> None:
    """Price and screener refresh on the worker thread. Account reads stay off this thread."""
    from .live import BUS, PRICE_SECONDS, SCREEN_SECONDS, poll_prices

    now = time.monotonic()
    if market is not None and now - live_at["price"] >= PRICE_SECONDS:
        symbols = list(dict.fromkeys([*(getattr(engine, "universe", ()) or ()), *cfg.symbols]))[:12]
        try:
            rows = poll_prices(market.http, symbols)
            BUS.note_prices(rows, None if rows or not symbols else "price_feed_unavailable")
        except Exception as exc:
            BUS.note_prices({}, type(exc).__name__)
        live_at["price"] = now
    if engine is not None and engine.screener is not None and now - live_at["screen"] >= SCREEN_SECONDS:
        engine._refresh_screener()
        publish(cfg, engine.journal, engine)
        live_at["screen"] = now
    if engine is not None and getattr(engine, "accounts_at", "") not in {"", live_at.get("accounts_at")}:
        publish(cfg, engine.journal, engine)
        live_at["accounts_at"] = engine.accounts_at


def _build_research(cfg: Settings):
    from .llm import LlmAnalyst
    from .screener import Screener

    screener = Screener(cfg.screener_url, cfg.market_data_url)
    analyst = LlmAnalyst(
        nvidia_key=cfg.nvidia_api_key,
        nvidia_model=cfg.nvidia_model,
        openrouter_key=cfg.openrouter_api_key,
        openrouter_model=cfg.openrouter_model,
        timeout=cfg.llm_timeout,
        daily_calls=cfg.llm_daily_calls,
        daily_usd=cfg.llm_daily_usd,
        usd_per_call=cfg.llm_usd_per_call,
    )
    return screener, analyst


def _start_http(port: int):
    if os.getenv("WARP_HTTP", "stdlib") == "fastapi":
        import uvicorn

        from .webapi import create_app

        config = uvicorn.Config(create_app(), host="0.0.0.0", port=port, log_level="info")
        server = uvicorn.Server(config)
        threading.Thread(target=server.run, daemon=True).start()
        return server
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    threading.Thread(target=run_health, args=(server,), daemon=True).start()
    return server


def _stop_http(server) -> None:
    if isinstance(server, ThreadingHTTPServer):
        server.shutdown()
        server.server_close()
        return
    server.should_exit = True


def start_vibe() -> subprocess.Popen | None:
    if os.getenv("WARP_MANAGE_VIBE", "0") != "1":
        return None
    vibe_env = os.environ.copy()
    for name in (
        "THREECOMMAS_API_KEY",
        "THREECOMMAS_API_SECRET",
        "THREECOMMAS_PROFILE_CODE",
        "WARP_LIVE_EXECUTION_ACK",
        "WARP_ALERT_WEBHOOK_URL",
        "WARP_DASHBOARD_PASSWORD",
        "WARP_SESSION_SECRET",
        "NVIDIA_API_KEY",
        "OPENROUTER_API_KEY",
    ):
        vibe_env.pop(name, None)
    vibe_env["VIBE_TRADING_ENABLE_SHELL_TOOLS"] = "0"
    proc = subprocess.Popen(["vibe-trading", "serve", "--port", "8899"], env=vibe_env)
    for _ in range(60):
        if proc.poll() is not None:
            raise RuntimeError("Vibe-Trading API exited during startup")
        try:
            with httpx.Client(timeout=2) as client:
                if client.get("http://127.0.0.1:8899/health").status_code == 200:
                    return proc
        except httpx.HTTPError:
            pass
        time.sleep(2)
    proc.terminate()
    raise RuntimeError("Vibe-Trading health endpoint unavailable after startup")


def _notify(cfg: Settings, journal: OrderJournal | None, event: str, detail: str) -> None:
    if journal is None:
        return
    if journal.enqueue_alert(event, detail, now=datetime.now(timezone.utc)):
        log_event(event, detail=detail, mode=cfg.mode)


def _deliver(cfg: Settings, event: str, detail: str) -> bool:
    if not cfg.alert_webhook and not cfg.telegram_bot_token:
        return True
    ok = True
    if cfg.alert_webhook:
        ok = send_alert(cfg.alert_webhook, event, detail) and ok
    if cfg.telegram_bot_token:
        ok = send_telegram(cfg.telegram_bot_token, cfg.telegram_chat_id, event, detail) and ok
    return ok


def _flush(cfg: Settings, journal: OrderJournal | None) -> None:
    if journal is None:
        return
    flush_alerts(journal, lambda event, detail: _deliver(cfg, event, detail))


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cfg = Settings.from_env()
    HEALTH["mode"] = cfg.mode
    HEALTH["execution_permitted"] = False
    HEALTH["risk_permitted"] = False
    HEALTH["halt_path"] = str(Path(cfg.db_path).parent / "HALT")
    if cfg.mode != "observe":
        log_event("mode_warning", mode=cfg.mode, detail="process is not in observe mode")
    Path(cfg.db_path).parent.mkdir(parents=True, exist_ok=True)
    lock = open(Path(cfg.db_path).parent / "worker.lock", "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise RuntimeError("Another WARP worker already owns the data volume") from exc
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_args: STOP.set())
    port = int(os.getenv("PORT", "8080"))
    server = _start_http(port)
    vibe_process = None
    broker = research = journal = market = screener = analyst = None
    try:
        vibe_process = start_vibe()
        broker = ThreeCommas(cfg.api_url, cfg.api_key, cfg.api_secret)
        research = VibeResearch(cfg.vibe_url, cfg.vibe_key)
        market = MarketData(cfg.market_data_url, cfg)
        journal = OrderJournal(cfg.db_path)
        screener, analyst = _build_research(cfg)
        engine = Engine(cfg, broker, research, journal, market, screener=screener, analyst=analyst)
        from .live import ACCOUNT_SECONDS, DashboardReads

        accounts = DashboardReads(engine.refresh_readonly, ACCOUNT_SECONDS)
        accounts.start()
        publish(cfg, journal, engine)
        gate = ResearchGate()
        clocks = Clocks()
        live_at = {"price": 0.0, "screen": time.monotonic(), "accounts_at": ""}
        log_event("worker_started", mode=cfg.mode, symbols=",".join(cfg.symbols), discovery=cfg.discovery)
        while not STOP.is_set():
            if vibe_process and vibe_process.poll() is not None:
                raise RuntimeError("Vibe API exited; supervisor restarting")
            try:
                clocks = once(engine, gate, clocks)
                HEALTH["last_cycle_ok"] = engine.status.dependencies_healthy
                HEALTH["dependencies_healthy"] = engine.status.dependencies_healthy
                HEALTH["market_data_fresh"] = engine.status.market_data_fresh
                HEALTH["execution_permitted"] = engine.status.execution_permitted
                HEALTH["risk_permitted"] = engine.status.risk_permitted
                HEALTH["reason"] = engine.status.reason
                publish(cfg, journal, engine)
                if engine.status.reason in {"ok", "observe_without_account"}:
                    journal.clear_alert()
                elif not engine.status.risk_permitted:
                    _notify(cfg, journal, engine.status.reason, "new entries are paused")
            except Exception as exc:
                HEALTH["last_cycle_ok"] = False
                HEALTH["dependencies_healthy"] = False
                HEALTH["execution_permitted"] = False
                HEALTH["risk_permitted"] = False
                HEALTH["reason"] = "cycle_blocked"
                detail = str(exc) if isinstance(exc, IntegrationError) else exc.__class__.__name__
                log_event("cycle_blocked", error=detail[:300])
                _notify(cfg, journal, "cycle_blocked", detail[:300])
            finally:
                if journal is not None:
                    _flush(cfg, journal)
                _refresh_live(engine, market, cfg, live_at)
            STOP.wait(min(3, cfg.monitor_seconds))
    finally:
        HEALTH["alive"] = False
        HEALTH["execution_permitted"] = False
        if "accounts" in locals():
            accounts.stop()
        _stop_http(server)
        if journal:
            journal.close()
        if market:
            market.close()
        if screener:
            screener.close()
        if analyst:
            analyst.close()
        if research:
            research.close()
        if broker:
            broker.close()
        if vibe_process and vibe_process.poll() is None:
            vibe_process.terminate()
            try:
                vibe_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                vibe_process.kill()
        lock.close()


if __name__ == "__main__":
    main()
