"""JSON logs and optional HTTPS alerts. Never include credentials or signed headers."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

LOG = logging.getLogger("warp3c")
_HIDDEN = {"api_key", "api_secret", "secret", "signature", "authorization", "token", "webhook"}
# Telegram puts the bot token in the path. httpx logs that path at INFO.
_BOT_PATH = re.compile(r"/bot[^/\s]+/")
_HTTP_LOGGERS = ("httpx", "httpcore")


class _RedactBotPath(logging.Filter):
    """Rewrite `/bot<token>/` to `/bot<redacted>/` before a record is formatted."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            _redact_record(record)
        except Exception:
            return True
        return True


def _redact_text(value: str) -> str:
    return _BOT_PATH.sub("/bot<redacted>/", value)


def _redact_arg(value: object) -> object:
    if isinstance(value, str):
        return _redact_text(value)
    rendered = str(value)
    redacted = _redact_text(rendered)
    if redacted != rendered:
        return redacted
    return value


def _redact_args(args: object) -> object:
    if isinstance(args, dict):
        return {key: _redact_arg(value) for key, value in args.items()}
    if isinstance(args, tuple):
        return tuple(_redact_arg(value) for value in args)
    return _redact_arg(args)


def _redact_record(record: logging.LogRecord) -> None:
    if isinstance(record.msg, str):
        record.msg = _redact_text(record.msg)
    if record.args:
        record.args = _redact_args(record.args)
    cached = getattr(record, "message", None)
    if isinstance(cached, str):
        record.message = _redact_text(cached)
    exc_text = getattr(record, "exc_text", None)
    if isinstance(exc_text, str):
        record.exc_text = _redact_text(exc_text)
    if record.exc_info:
        formatted = logging.Formatter().formatException(record.exc_info)
        if _BOT_PATH.search(formatted):
            record.exc_text = _redact_text(formatted)
            record.exc_info = None


_REDACT = _RedactBotPath()


def _http_logger(name: str) -> bool:
    return name == "httpx" or name.startswith("httpx.") or name == "httpcore" or name.startswith("httpcore.")


def _attach(logger: logging.Logger) -> None:
    if not any(isinstance(existing, _RedactBotPath) for existing in logger.filters):
        logger.addFilter(_REDACT)
    for handler in logger.handlers:
        if not any(isinstance(existing, _RedactBotPath) for existing in handler.filters):
            handler.addFilter(_REDACT)


def install_http_redaction() -> None:
    """Keep the bot token out of httpx and httpcore logs. Other hosts stay intact."""
    manager = logging.Logger.manager
    if not getattr(manager, "_warp_redact_wrapped", False):
        original = manager.getLogger

        def get_logger(name: str) -> logging.Logger:
            logger = original(name)
            if _http_logger(name):
                _attach(logger)
            return logger

        manager.getLogger = get_logger  # type: ignore[method-assign]
        manager._warp_redact_wrapped = True
    for name, logger in list(manager.loggerDict.items()):
        if isinstance(logger, logging.Logger) and _http_logger(name):
            _attach(logger)
    for name in _HTTP_LOGGERS:
        _attach(logging.getLogger(name))
    _attach(logging.getLogger())


def configure_logging() -> None:
    """The process log setup `main()` uses. INFO request lines stay, tokens do not."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    install_http_redaction()


install_http_redaction()


class TelegramResult:
    """One Telegram attempt. Truthy only when Telegram accepted the message."""

    __slots__ = ("ok", "retry_after", "http_status")

    def __init__(self, ok: bool, *, retry_after: int | None = None, http_status: int | None = None):
        self.ok = ok
        self.retry_after = retry_after
        self.http_status = http_status

    def __bool__(self) -> bool:
        return self.ok


def log_event(event: str, level: int = logging.INFO, **fields: object) -> None:
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **{key: value for key, value in fields.items() if key.lower() not in _HIDDEN},
    }
    LOG.log(level, "%s", json.dumps(payload, default=str, separators=(",", ":")))
    from .live import remember_event

    remember_event(payload)
    if event == "worker_started":
        _queue_worker_started()


def _queue_worker_started() -> None:
    """The worker log line is the start signal. The alert carries mode and the shared flag only."""
    from .notify import channel_configured, enqueue_incident
    from .operator import RUNTIME

    journal = RUNTIME.get("journal")
    cfg = RUNTIME.get("cfg")
    if journal is None or cfg is None:
        return
    mode = getattr(cfg, "mode", "")
    if mode not in {"observe", "paper", "live"}:
        mode = "unknown"
    shared = "true" if bool(getattr(cfg, "shared_account", False)) else "false"
    enqueue_incident(
        journal,
        "worker_started",
        f"mode={mode} shared_account={shared}",
        channel=channel_configured(cfg),
    )


def _telegram_retry_after(response: httpx.Response) -> int | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    params = payload.get("parameters")
    if not isinstance(params, dict):
        return None
    raw = params.get("retry_after")
    if type(raw) is bool or type(raw) not in {int, float}:
        return None
    if raw < 0:
        return None
    return min(3600, int(raw))


def send_telegram(token: str, chat_id: str, event: str, detail: str, *, transport=None) -> TelegramResult:
    """POST to Telegram. The bot token is not written to the log."""
    if not token or not chat_id:
        return TelegramResult(False)
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    text = f"{event}: {detail}"[:400]
    body = {"chat_id": chat_id, "text": text}
    try:
        with httpx.Client(transport=transport, timeout=5) as client:
            response = client.post(url, json=body)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        retry_after = _telegram_retry_after(exc.response) if status == 429 else None
        log_event("alert_failed", alert_event=event, channel="telegram", http_status=status)
        return TelegramResult(False, retry_after=retry_after, http_status=status)
    except httpx.HTTPError:
        log_event("alert_failed", alert_event=event, channel="telegram")
        return TelegramResult(False)
    return TelegramResult(True)


def send_alert(url: str, event: str, detail: str, *, transport=None) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        log_event("alert_skipped", reason="webhook_not_https")
        return False
    body = {"event": event, "detail": detail[:500], "ts": datetime.now(timezone.utc).isoformat()}
    try:
        with httpx.Client(transport=transport, timeout=5) as client:
            response = client.post(url, json=body)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        log_event("alert_failed", alert_event=event, channel="webhook", http_status=exc.response.status_code)
        return False
    except httpx.HTTPError:
        log_event("alert_failed", alert_event=event, channel="webhook")
        return False
    return True
