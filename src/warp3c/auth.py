"""Dashboard session cookies. The API bearer token still works for automation."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

COOKIE = "warp_session"
MAX_AGE = 24 * 60 * 60
REVOKED_FILE = "sessions_revoked.json"
MAX_REVOKED = 4096


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


def _verified_expiry(token: str | None) -> int | None:
    """Expiry of a token whose format and HMAC are valid and which has not expired, else None."""
    secret = _secret()
    if not secret or not token or not isinstance(token, str) or token.count(".") != 2:
        return None
    expires, nonce, signature = token.split(".", 2)
    if not expires.isdigit() or not nonce:
        return None
    if int(expires) < int(time.time()):
        return None
    body = f"{expires}.{nonce}"
    expected = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
    if len(signature) != len(expected):
        return None
    if not hmac.compare_digest(signature, expected):
        return None
    return int(expires)


def _token_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def revocation_path() -> Path:
    return Path(os.getenv("WARP_DB_PATH", "/data/warp.db")).parent / REVOKED_FILE


class _StoreError(Exception):
    pass


_MEMORY: dict[str, int] = {}
_CACHE: dict = {"stamp": None, "entries": {}}


def _reset_state() -> None:
    """Drop in-process revocation state. Used to simulate a restart: the file is the source of truth."""
    with _LOCK:
        _MEMORY.clear()
        _CACHE["stamp"] = None
        _CACHE["entries"] = {}


def _load(path: Path) -> dict[str, int]:
    """Read the revocation file. A missing file is an empty store. Anything unreadable raises _StoreError."""
    try:
        info = path.stat()
    except FileNotFoundError:
        return {}
    except OSError as error:
        raise _StoreError("revocation store unreadable") from error
    stamp = (str(path), info.st_ino, info.st_mtime_ns, info.st_size)
    if _CACHE["stamp"] == stamp:
        return _CACHE["entries"]
    try:
        data = json.loads(path.read_text())
        revoked = data["revoked"]
        if data.get("version") != 1 or not isinstance(revoked, dict):
            raise ValueError("shape")
        entries: dict[str, int] = {}
        for key, expiry in revoked.items():
            if not isinstance(key, str) or len(key) != 64 or type(expiry) is not int:
                raise ValueError("entry")
            entries[key] = expiry
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise _StoreError("revocation store corrupt") from error
    _CACHE["stamp"] = stamp
    _CACHE["entries"] = entries
    return entries


def _write(path: Path, entries: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    payload = json.dumps({"version": 1, "revoked": entries}, sort_keys=True)
    try:
        with open(temp, "w") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except OSError:
        try:
            temp.unlink()
        except OSError:
            pass
        raise


def revoke_session(token: str | None) -> bool:
    """Revoke exactly the presented token. Forged, malformed or expired tokens are ignored, so the
    store only grows from tokens this server signed. Returns True when the token is now revoked on disk."""
    expiry = _verified_expiry(token)
    if expiry is None:
        return False
    key = _token_key(token)
    now = int(time.time())
    with _LOCK:
        _MEMORY[key] = expiry
        for stale in [k for k, v in _MEMORY.items() if v < now]:
            del _MEMORY[stale]
        path = revocation_path()
        try:
            entries = {k: v for k, v in _load(path).items() if v >= now}
            entries[key] = expiry
            while len(entries) > MAX_REVOKED:
                del entries[min(entries, key=entries.get)]
            _write(path, entries)
        except (_StoreError, OSError):
            return False
        _CACHE["stamp"] = None
    return True


def session_valid(token: str | None) -> bool:
    if _verified_expiry(token) is None:
        return False
    key = _token_key(token)
    with _LOCK:
        if key in _MEMORY:
            return False
        try:
            entries = _load(revocation_path())
        except _StoreError:
            return False
    return key not in entries


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
