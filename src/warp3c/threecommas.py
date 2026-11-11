"""3Commas REST client. Writes are never retried."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from decimal import Decimal
from urllib.parse import quote, urlencode

import httpx

from .models import Account, AssetBalance, positive

_DECIMAL = re.compile(r'"__DEC__(-?(?:0|[1-9]\d*)(?:\.\d+)?)__"')


class IntegrationError(RuntimeError):
    """Remote state cannot be trusted for a new entry."""

    def __init__(self, message: str, *, uncertain: bool = True, status_code: int | None = None):
        super().__init__(message)
        self.uncertain = uncertain
        self.status_code = status_code


class StrategyNotFound(IntegrationError):
    def __init__(self, client_id: str):
        super().__init__(f"Strategy {client_id} was not found", uncertain=False, status_code=404)


def dump_payload(payload: dict) -> str:
    """Encode Decimals as exact JSON numbers so the signed body matches the wire body."""

    def default(value: object) -> str:
        if isinstance(value, Decimal):
            text = format(value, "f")
            if "." in text:
                text = text.rstrip("0").rstrip(".") or "0"
            return f"__DEC__{text}__"
        raise TypeError(f"Cannot encode {type(value).__name__}")

    raw = json.dumps(payload, default=default, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
    return _DECIMAL.sub(r"\1", raw)


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
        signature = base64.b64encode(
            hmac.new(self.secret.encode(), message.encode(), hashlib.sha256).digest()
        ).decode()
        return {
            "X-API-Key": self.key,
            "X-Signature": signature,
            "X-Timestamp": stamp,
            "X-Recv-Window": window,
        }

    def request(self, method: str, path: str, payload: dict | None = None, *, query=None):
        if not self.key or not self.secret:
            raise IntegrationError("3Commas credentials absent", uncertain=False)
        if not path.startswith("/open_api/"):
            raise ValueError("Unexpected 3Commas API route")
        canonical = path + ("?" + urlencode(sorted((query or {}).items())) if query else "")
        body = dump_payload(payload) if payload is not None else ""
        retry_get = method.upper() == "GET"
        last_error: Exception | None = None
        for attempt in range(3 if retry_get else 1):
            headers = self.signed_headers(method, canonical, body)
            if payload is not None:
                headers["Content-Type"] = "application/json"
            try:
                response = self.http.request(method, canonical, content=body.encode(), headers=headers)
            except httpx.TimeoutException as exc:
                last_error = exc
                if retry_get and attempt < 2:
                    time.sleep(0.2 * (attempt + 1))
                    continue
                raise IntegrationError(f"3Commas {method} {path} timed out", uncertain=True) from exc
            except httpx.TransportError as exc:
                last_error = exc
                if retry_get and attempt < 2:
                    time.sleep(0.2 * (attempt + 1))
                    continue
                raise IntegrationError(f"3Commas {method} {path} transport failed", uncertain=True) from exc
            if response.status_code == 429 and retry_get and attempt < 2:
                time.sleep(self._retry_delay(response))
                continue
            if response.status_code == 404 and method.upper() == "GET":
                raise StrategyNotFound(path.rsplit("/", 1)[-1])
            if response.status_code >= 400:
                uncertain = response.status_code == 429 or response.status_code >= 500
                raise IntegrationError(
                    f"3Commas {method} {path} failed with HTTP {response.status_code}",
                    uncertain=uncertain,
                    status_code=response.status_code,
                )
            try:
                return response.json()
            except ValueError as exc:
                raise IntegrationError(f"3Commas {method} {path} returned non-JSON", uncertain=True) from exc
        raise IntegrationError(f"3Commas {method} {path} failed", uncertain=True) from last_error

    @staticmethod
    def _retry_delay(response: httpx.Response) -> float:
        reset = response.headers.get("RateLimit-Reset")
        try:
            wait = max(0.0, int(reset) / 1000 - time.time()) if reset else 0.5
        except ValueError:
            wait = 0.5
        return min(wait, 1.5)

    def _pages(self, path: str, query: dict | None = None) -> list[dict]:
        page = 1
        collected: list[dict] = []
        for _ in range(50):
            params = dict(query or {})
            params.update({"page": page, "limit": 100})
            result = self.request("GET", path, query=params)
            if isinstance(result, list):
                if page != 1:
                    raise IntegrationError("Paged and bare list responses were mixed", uncertain=True)
                return result
            if not isinstance(result, dict) or not isinstance(result.get("data"), list):
                raise IntegrationError("Unrecognized paged response", uncertain=True)
            collected.extend(result["data"])
            has_more = result.get("has_more")
            if has_more is True:
                nxt = result.get("next_page")
                page = int(nxt) if nxt is not None else page + 1
                if page <= 1:
                    raise IntegrationError("Pagination cursor did not advance", uncertain=True)
                continue
            if has_more is False:
                return collected
            total = result.get("total")
            if total is not None and int(total) == len(collected):
                return collected
            if total is not None and int(total) > len(collected):
                page += 1
                continue
            raise IntegrationError("Cannot prove the remote list is complete", uncertain=True)
        raise IntegrationError("Pagination exceeded the safety cap", uncertain=True)

    def get_profiles(self) -> list[dict]:
        return self._pages("/open_api/api_profiles")

    def get_account(self, profile_code: str, exchange_code: str = "BINANCE") -> Account:
        matches = [row for row in self.get_profiles() if row.get("code") == profile_code]
        if len(matches) != 1:
            raise IntegrationError("Expected exactly one configured 3Commas API profile", uncertain=True)
        profile = matches[0]
        if profile.get("status") != "active":
            raise IntegrationError("3Commas API profile is not active", uncertain=False)
        exchange = profile.get("exchange") or {}
        name = str(exchange.get("name", "")).upper()
        if exchange.get("code") != exchange_code or "SPOT" not in name:
            raise IntegrationError("3Commas profile is not the expected spot exchange", uncertain=False)
        if not isinstance(profile.get("demo"), bool):
            raise IntegrationError("Account demo/live classification absent", uncertain=True)
        assets_raw = (profile.get("balance") or {}).get("assets") if isinstance(profile.get("balance"), dict) else None
        if not isinstance(assets_raw, dict) or "USDT" not in assets_raw:
            raise IntegrationError("USDT account balance unavailable", uncertain=True)
        assets: dict[str, AssetBalance] = {}
        for asset, row in assets_raw.items():
            if not isinstance(row, dict):
                raise IntegrationError("Asset balance row is incomplete", uncertain=True)
            free, used, total = (positive(row.get(key), key) for key in ("free", "used", "total"))
            tolerance = max(Decimal("0.05"), total * Decimal("0.0001"))
            if abs((free + used) - total) > tolerance:
                raise IntegrationError("Account balances are inconsistent", uncertain=True)
            assets[str(asset)] = AssetBalance(free, used, total)
        usdt = assets["USDT"]
        return Account(profile_code, profile["demo"], usdt.free, usdt.used, usdt.total, assets)

    def get_live(self) -> list[dict]:
        return self._pages("/open_api/strategies/live")

    def get_strategy(self, client_id: str) -> dict:
        result = self.request("GET", "/open_api/strategies/" + quote(client_id, safe=""))
        if not isinstance(result, dict) or not result.get("strategyId"):
            raise IntegrationError("Strategy lookup returned no id", uncertain=True)
        return result

    def create_strategy(self, payload: dict) -> dict:
        result = self.request("POST", "/open_api/strategies/trade", payload)
        if not isinstance(result, dict) or not result.get("strategyId"):
            raise IntegrationError("3Commas returned no confirmed strategy ID", uncertain=True)
        echoed = result.get("clientId")
        if echoed is not None and echoed != payload.get("clientId"):
            raise IntegrationError("3Commas echoed a different clientId", uncertain=True)
        return result
