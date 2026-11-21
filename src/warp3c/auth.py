"""Dashboard session cookies. The API bearer token still works for automation."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time

COOKIE = "warp_session"
MAX_AGE = 12 * 60 * 60


def _password() -> str:
    return os.getenv("WARP_DASHBOARD_PASSWORD", "")


def _secret() -> bytes:
    explicit = os.getenv("WARP_SESSION_SECRET", "")
    if len(explicit) >= 16:
        return explicit.encode()
    token = os.getenv("WARP_OPERATOR_TOKEN", "")
    if len(token) >= 16:
        return hmac.new(token.encode(), b"warp3c-session-v1", hashlib.sha256).digest()
    return b""


def password_configured() -> bool:
    return len(_password()) >= 12 and bool(_secret())


def password_matches(offered: str) -> bool:
    expected = _password()
    if len(expected) < 12 or not isinstance(offered, str):
        return False
    left = hashlib.sha256(offered.encode()).digest()
    right = hashlib.sha256(expected.encode()).digest()
    return hmac.compare_digest(left, right)


def issue_session() -> str:
    secret = _secret()
    if not secret:
        raise RuntimeError("Session secret is not configured")
    expires = int(time.time()) + MAX_AGE
    body = f"{expires}.{secrets.token_hex(8)}"
    signature = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def session_valid(token: str | None) -> bool:
    secret = _secret()
    if not secret or not token or token.count(".") != 2:
        return False
    expires, nonce, signature = token.split(".", 2)
    if not expires.isdigit() or not nonce:
        return False
    if int(expires) < int(time.time()):
        return False
    body = f"{expires}.{nonce}"
    expected = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
    if len(signature) != len(expected):
        return False
    return hmac.compare_digest(signature, expected)


def parse_cookies(header: str | None) -> dict[str, str]:
    found: dict[str, str] = {}
    if not header:
        return found
    for part in header.split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        found[name.strip()] = value.strip()
    return found


def cookie_header(token: str, *, clear: bool = False) -> str:
    secure = os.getenv("WARP_COOKIE_SECURE", "0") == "1"
    max_age = 0 if clear else MAX_AGE
    value = "" if clear else token
    parts = [
        f"{COOKIE}={value}",
        "HttpOnly",
        "SameSite=Strict",
        "Path=/",
        f"Max-Age={max_age}",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


_FAILURES = {"count": 0, "until": 0.0}
_LOCK = threading.Lock()


def login_blocked() -> bool:
    with _LOCK:
        return time.time() < _FAILURES["until"]


def note_login_failure() -> None:
    with _LOCK:
        _FAILURES["count"] += 1
        if _FAILURES["count"] >= 8:
            _FAILURES["until"] = time.time() + 60
            _FAILURES["count"] = 0


def note_login_success() -> None:
    with _LOCK:
        _FAILURES["count"] = 0
        _FAILURES["until"] = 0.0
