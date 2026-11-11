"""3Commas V2 REST client with exact HMAC signing and no POST retries."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from decimal import Decimal
from urllib.parse import urlencode

import httpx

from .models import Account, positive


class IntegrationError(RuntimeError):
    """Remote data cannot be trusted; do not trade on this cycle."""


class ThreeCommas:
    def __init__(self, url: str, key: str, secret: str, *, transport=None):
        self.key = key
        self.secret = secret
        self.http = httpx.Client(base_url=url.rstrip("/"), timeout=20, transport=transport)

    def close(self) -> None:
        self.http.close()

    def signed_headers(self, method: str, path: str, body: str = "", *, timestamp=None) -> dict[str, str]:
        stamp = str(timestamp or int(time.time() * 1000))
        window = "60000"
        message = "\n".join([method.upper(), path, stamp, window, body])
        signature = base64.b64encode(hmac.new(
            self.secret.encode(), message.encode(), hashlib.sha256
        ).digest()).decode()
        return {
            "X-API-Key": self.key,
            "X-Signature": signature,
            "X-Timestamp": stamp,
            "X-Recv-Window": window,
        }

    def request(self, method: str, path: str, payload: dict | None = None, *, query=None):
        if not self.key or not self.secret:
            raise IntegrationError("3Commas credentials absent")
        if not path.startswith("/open_api/"):
            raise ValueError("Unexpected 3Commas API route")
        canonical_path = path + ("?" + urlencode(query) if query else "")
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False) if payload else ""
        headers = self.signed_headers(method, canonical_path, body)
        if payload is not None:
            headers["Content-Type"] = "application/json"
        # Deliberately never retry writes. A timeout could mean the trade was accepted.
        try:
            response = self.http.request(method, canonical_path, content=body.encode(), headers=headers)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise IntegrationError(f"3Commas {method} {path} failed; inspect reconciliation") from exc

    def get_profiles(self) -> list[dict]:
        result = self.request("GET", "/open_api/api_profiles")
        profiles = result if isinstance(result, list) else result.get("data") if isinstance(result, dict) else None
        if not isinstance(profiles, list):
            raise IntegrationError("Unrecognized API profile response")
        return profiles

    def get_account(self, profile_code: str, exchange_code: str = "BINANCE") -> Account:
        matches = [p for p in self.get_profiles() if p.get("code") == profile_code]
        if len(matches) != 1:
            raise IntegrationError("Expected exactly one configured 3Commas API profile")
        p = matches[0]
        if p.get("status") != "active":
            raise IntegrationError("3Commas API profile is not active")
        exchange = p.get("exchange") or {}
        if exchange.get("code") != exchange_code or "SPOT" not in str(exchange.get("name", "")).upper():
            raise IntegrationError("3Commas profile is not the expected spot exchange")
        if not isinstance(p.get("demo"), bool):
            raise IntegrationError("Account demo/live classification absent")
        assets = p.get("balance", {}).get("assets")
        usdt = assets.get("USDT") if isinstance(assets, dict) else None
        if not isinstance(usdt, dict):
            raise IntegrationError("USDT account balance unavailable")
        free, used, total = (positive(usdt.get(k), k) for k in ("free", "used", "total"))
        if abs((free + used) - total) > Decimal("0.05"):
            raise IntegrationError("USDT account balances are inconsistent")
        return Account(profile_code, p["demo"], free, used, total)

    def get_live(self) -> list[dict]:
        result = self.request("GET", "/open_api/strategies/live")
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and isinstance(result.get("data"), list):
            # Never silently accept incomplete pagination.
            if result.get("total") is not None and result["total"] > len(result["data"]):
                raise IntegrationError("Live strategies response truncated; pagination required")
            return result["data"]
        raise IntegrationError("Unrecognized live strategies response")

    def create_strategy(self, payload: dict) -> dict:
        result = self.request("POST", "/open_api/strategies/trade", payload)
        if not isinstance(result, dict) or not result.get("strategyId"):
            raise IntegrationError("3Commas returned no confirmed strategy ID")
        return result
