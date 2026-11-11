"""24/7 single-machine supervisor. HTTP 200 means the process is up, not that trading is allowed."""

from __future__ import annotations

import fcntl
import json
import logging
import os
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

from .config import Settings
from .engine import Engine
from .market import MarketData
from .state import OrderJournal
from .telemetry import log_event, send_alert
from .threecommas import IntegrationError, ThreeCommas
from .vibe import VibeResearch

STOP = threading.Event()
HEALTH = {
    "alive": True,
    "dependencies_healthy": False,
    "market_data_fresh": False,
    "execution_permitted": False,
    "mode": "observe",
    "last_cycle_ok": None,
    "reason": "starting",
}


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path not in {"/health", "/live", "/ready"}:
            self.send_error(404)
            return
        if path == "/ready":
            code = 200 if HEALTH.get("dependencies_healthy") and HEALTH.get("last_cycle_ok") else 503
        else:
            code = 200
        body = json.dumps(HEALTH, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


def run_health(server: ThreadingHTTPServer):
    server.serve_forever()


def start_vibe() -> subprocess.Popen | None:
    if os.getenv("WARP_MANAGE_VIBE", "1") != "1":
        return None
    vibe_env = os.environ.copy()
    for name in (
        "THREECOMMAS_API_KEY",
        "THREECOMMAS_API_SECRET",
        "THREECOMMAS_PROFILE_CODE",
        "WARP_LIVE_EXECUTION_ACK",
        "WARP_ALERT_WEBHOOK_URL",
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
    if journal is None or not journal.claim_alert(event):
        return
    log_event(event, detail=detail, mode=cfg.mode)
    if cfg.alert_webhook:
        send_alert(cfg.alert_webhook, event, detail)


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cfg = Settings.from_env()
    HEALTH["mode"] = cfg.mode
    HEALTH["execution_permitted"] = False
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
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    threading.Thread(target=run_health, args=(server,), daemon=True).start()
    vibe_process = None
    broker = research = journal = market = None
    try:
        vibe_process = start_vibe()
        broker = ThreeCommas(cfg.api_url, cfg.api_key, cfg.api_secret)
        research = VibeResearch(cfg.vibe_url, cfg.vibe_key)
        market = MarketData(cfg.market_data_url, cfg)
        journal = OrderJournal(cfg.db_path)
        engine = Engine(cfg, broker, research, journal, market)
        log_event("worker_started", mode=cfg.mode, symbols=",".join(cfg.symbols))
        while not STOP.is_set():
            if vibe_process and vibe_process.poll() is not None:
                raise RuntimeError("Vibe API exited; supervisor restarting")
            try:
                engine.cycle()
                HEALTH["last_cycle_ok"] = True
                HEALTH["dependencies_healthy"] = engine.status.dependencies_healthy
                HEALTH["market_data_fresh"] = engine.status.market_data_fresh
                HEALTH["execution_permitted"] = engine.status.execution_permitted
                HEALTH["reason"] = engine.status.reason
                if engine.status.reason in {"ok", "observe_without_account"}:
                    journal.clear_alert()
                elif not engine.status.execution_permitted:
                    _notify(cfg, journal, engine.status.reason, "new entries are paused")
            except Exception as exc:
                HEALTH["last_cycle_ok"] = False
                HEALTH["dependencies_healthy"] = False
                HEALTH["execution_permitted"] = False
                HEALTH["reason"] = "cycle_blocked"
                detail = str(exc) if isinstance(exc, IntegrationError) else exc.__class__.__name__
                log_event("cycle_blocked", error=detail[:300])
                _notify(cfg, journal, "cycle_blocked", detail[:300])
            STOP.wait(cfg.cycle_seconds)
    finally:
        HEALTH["alive"] = False
        HEALTH["execution_permitted"] = False
        server.shutdown()
        if journal:
            journal.close()
        if market:
            market.close()
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
