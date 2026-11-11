"""24/7 single-machine supervisor and health endpoint for Fly.io."""

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
from .state import OrderJournal
from .threecommas import ThreeCommas
from .vibe import VibeResearch

LOG = logging.getLogger("warp3c")
STOP = threading.Event()
HEALTH = {"alive": True, "ready": False, "last_cycle_ok": None}


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/health", "/live"):
            self.send_error(404)
            return
        body = json.dumps(HEALTH, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def run_health():
    port = int(os.getenv("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()


def start_vibe() -> subprocess.Popen | None:
    if os.getenv("WARP_MANAGE_VIBE", "1") != "1":
        return None
    # The model research process never receives the broker's write credentials.
    vibe_env = os.environ.copy()
    for name in ("THREECOMMAS_API_KEY", "THREECOMMAS_API_SECRET", "WARP_LIVE_EXECUTION_ACK"):
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


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = Settings.from_env()
    Path(cfg.db_path).parent.mkdir(parents=True, exist_ok=True)
    lock = open(Path(cfg.db_path).parent / "worker.lock", "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise RuntimeError("Another WARP worker already owns the data volume") from exc
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: STOP.set())
    threading.Thread(target=run_health, daemon=True).start()
    vibe_process = None
    broker = research = journal = None
    try:
        vibe_process = start_vibe()
        broker = ThreeCommas(cfg.api_url, cfg.api_key, cfg.api_secret)
        research = VibeResearch(cfg.vibe_url, cfg.vibe_key)
        journal = OrderJournal(cfg.db_path)
        engine = Engine(cfg, broker, research, journal)
        LOG.info("Worker started mode=%s symbols=%s", cfg.mode, ",".join(cfg.symbols))
        while not STOP.is_set():
            if vibe_process and vibe_process.poll() is not None:
                raise RuntimeError("Vibe API exited; supervisor restarting")
            try:
                engine.cycle()
                HEALTH["last_cycle_ok"] = True
                HEALTH["ready"] = bool(cfg.api_key and cfg.api_secret and cfg.profile_code)
            except Exception:
                HEALTH["ready"] = False
                HEALTH["last_cycle_ok"] = False
                LOG.exception("Cycle blocked; no further submissions on this cycle")
            STOP.wait(cfg.cycle_seconds)
    finally:
        HEALTH["alive"] = False
        if journal:
            journal.close()
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
