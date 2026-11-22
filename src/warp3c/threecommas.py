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


_PAGE_LIMIT = 100
_MAX_PAGES = 50


def _as_int(value: object, label: str) -> int:
    if type(value) is not int:
        raise IntegrationError(f"{label} is missing or not an integer", uncertain=True)
    return value


def _parse_page(result: object, *, requested_page: int, requested_limit: int) -> tuple[list[dict], dict[str, int]]:
    if not isinstance(result, dict):
        raise IntegrationError("Unrecognized paged response", uncertain=True)
    pagination = result.get("pagination")
    items = result.get("items")
    if not isinstance(pagination, dict) or not isinstance(items, list):
        raise IntegrationError("Paged response missing items or pagination", uncertain=True)
    if any(not isinstance(item, dict) for item in items):
        raise IntegrationError("Invalid paged item", uncertain=True)
    meta = {
        "page": _as_int(pagination.get("page"), "pagination.page"),
        "limit": _as_int(pagination.get("limit"), "pagination.limit"),
        "total": _as_int(pagination.get("total"), "pagination.total"),
        "pages": _as_int(pagination.get("pages"), "pagination.pages"),
    }
    if meta["page"] != requested_page:
        raise IntegrationError("Misordered or unexpected pagination cursor", uncertain=True)
    if meta["limit"] != requested_limit:
        raise IntegrationError("pagination.limit does not match the request", uncertain=True)
    if meta["page"] < 1 or meta["limit"] < 1 or meta["total"] < 0 or meta["pages"] < 0:
        raise IntegrationError("Pagination metadata is inconsistent", uncertain=True)
    return items, meta


def _check_page_math(total: int, pages: int, limit: int) -> None:
    if total == 0:
        if pages not in (0, 1):
            raise IntegrationError("Empty list has an inconsistent page count", uncertain=True)
        return
    expected = (total + limit - 1) // limit
    if pages != expected:
        raise IntegrationError("pagination.pages does not match total", uncertain=True)
    if pages > _MAX_PAGES:
        raise IntegrationError("pagination.pages exceeds the safety cap", uncertain=True)


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
            if response.status_code == 204 or not response.content:
                return {}
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

    def _pages(self, path: str, *, identity: str, query: dict | None = None) -> list[dict]:
        """Official list envelope: {items, pagination: {page, limit, total, pages}}."""
        collected: list[dict] = []
        seen: set[str] = set()
        seen_pages: set[int] = set()
        expected_total: int | None = None
        expected_pages: int | None = None
        page = 1
        while page <= _MAX_PAGES:
            params = dict(query or {})
            params.update({"page": page, "limit": _PAGE_LIMIT})
            items, meta = _parse_page(
                self.request("GET", path, query=params),
                requested_page=page,
                requested_limit=_PAGE_LIMIT,
            )
            if expected_total is None:
                expected_total = meta["total"]
                expected_pages = meta["pages"]
                _check_page_math(expected_total, expected_pages, _PAGE_LIMIT)
            elif meta["total"] != expected_total or meta["pages"] != expected_pages:
                raise IntegrationError("Pagination metadata changed between pages", uncertain=True)
            if meta["page"] in seen_pages:
                raise IntegrationError("Duplicate pagination cursor", uncertain=True)
            seen_pages.add(meta["page"])
            if expected_pages == 0:
                if items or expected_total != 0:
                    raise IntegrationError("Empty page count returned items", uncertain=True)
                return []
            last = page == expected_pages
            if not last and len(items) != _PAGE_LIMIT:
                raise IntegrationError("Truncated intermediate page", uncertain=True)
            if last and len(items) > _PAGE_LIMIT:
                raise IntegrationError("Last page is longer than the limit", uncertain=True)
            for item in items:
                key = item.get(identity)
                if not isinstance(key, str) or not key:
                    raise IntegrationError("Paged item is missing its identity", uncertain=True)
                if key in seen:
                    raise IntegrationError("Duplicate paged item", uncertain=True)
                seen.add(key)
                collected.append(item)
            if last:
                if len(collected) != expected_total:
                    raise IntegrationError("Incomplete paged total", uncertain=True)
                if seen_pages != set(range(1, expected_pages + 1)):
                    raise IntegrationError("Pagination cursor did not cover every page", uncertain=True)
                return collected
            page += 1
            if expected_pages is not None and page > expected_pages:
                raise IntegrationError("Pagination cursor overran declared pages", uncertain=True)
        raise IntegrationError("Pagination exceeded the safety cap", uncertain=True)

    def get_profiles(self) -> list[dict]:
        return self._pages("/open_api/api_profiles", identity="code")

    def list_accounts(self) -> list:
        from .accounts import collect_profiles

        return collect_profiles(self.get_profiles())

    def get_account(self, profile_code: str, exchange_code: str = "BINANCE", *, market: str = "spot") -> Account:
        matches = [row for row in self.get_profiles() if row.get("code") == profile_code]
        if len(matches) != 1:
            raise IntegrationError("Expected exactly one configured 3Commas API profile", uncertain=True)
        profile = matches[0]
        if profile.get("status") != "active":
            raise IntegrationError("3Commas API profile is not active", uncertain=False)
        exchange = profile.get("exchange") or {}
        name = str(exchange.get("name", "")).upper()
        code = str(exchange.get("code") or "")
        if market != "spot" or code != exchange_code or "SPOT" not in name or "FUTURE" in name or "FUTURE" in code:
            raise IntegrationError("WARP trades Binance spot only", uncertain=False)
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
        return Account(
            profile_code,
            profile["demo"],
            usdt.free,
            usdt.used,
            usdt.total,
            assets,
            name=str(profile.get("name") or ""),
            exchange_code=code,
            exchange_name=str(exchange.get("name") or ""),
            market="spot",
            quote_asset="USDT",
            status=str(profile.get("status") or ""),
        )

    def get_live(self) -> list[dict]:
        return self._pages("/open_api/strategies/live", identity="strategyId")

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

    def create_dca_bot(self, payload: dict) -> dict:
        result = self.request("POST", "/open_api/dca_bots/create", payload)
        return _confirmed_bot(result)

    def post_dca_bot(self, payload: dict) -> dict:
        """Create call that may omit the new code. The caller resolves the bot by its WARP- name."""
        result = self.request("POST", "/open_api/dca_bots/create", payload)
        if not isinstance(result, dict):
            raise IntegrationError("3Commas returned no DCA bot body", uncertain=True)
        return result

    def create_grid_bot(self, payload: dict, *, market: str = "spot") -> dict:
        if market != "spot":
            raise ValueError("WARP trades Binance spot only")
        result = self.request("POST", "/open_api/grid_bots/create", payload, query={"gridMarket": "spot"})
        return _confirmed_bot(result)

    def list_dca_bots(self) -> list[dict]:
        return self._named_pages("/open_api/dca_bots", identity="code")

    def list_grid_bots(self) -> list[dict]:
        return self._named_pages("/open_api/grid_bots", identity="code")

    def get_bot_positions(self, kind: str, code: str) -> dict:
        """GET a bot's live positions (cabinet position view). Read-only."""
        if kind not in {"dca", "grid"}:
            raise ValueError("Unknown bot kind")
        result = self.request("GET", f"/open_api/{kind}_bots/{quote(code, safe='')}/positions")
        return result if isinstance(result, dict) else {}

    def get_dca_bot(self, code: str) -> dict:
        return self._one_bot("/open_api/dca_bots/" + quote(code, safe=""))

    def get_grid_bot(self, code: str) -> dict:
        return self._one_bot("/open_api/grid_bots/" + quote(code, safe=""))

    def pause_dca_bot(self, code: str) -> dict:
        return self._bot_write("POST", f"/open_api/dca_bots/{quote(code, safe='')}/switch-pause", {"stopCondition": "stop_only"})

    def resume_dca_bot(self, code: str) -> dict:
        return self._bot_write("POST", f"/open_api/dca_bots/{quote(code, safe='')}/switch-pause", {})

    def close_dca_positions(self, code: str) -> dict:
        return self._bot_write("POST", f"/open_api/dca_bots/{quote(code, safe='')}/close-all", {})

    def delete_dca_bot(self, code: str) -> dict:
        return self._bot_write("DELETE", f"/open_api/dca_bots/{quote(code, safe='')}/delete", None)

    def stop_grid_bot(self, code: str, *, close: bool) -> dict:
        condition = "stop_and_close_all" if close else "stop_only"
        return self._bot_write(
            "POST", f"/open_api/grid_bots/{quote(code, safe='')}/stop", {"stopCondition": condition}
        )

    def restart_grid_bot(self, code: str) -> dict:
        return self._bot_write("POST", f"/open_api/grid_bots/{quote(code, safe='')}/restart", {})

    def delete_grid_bot(self, code: str) -> dict:
        return self._bot_write("DELETE", f"/open_api/grid_bots/{quote(code, safe='')}/delete", None)

    def _one_bot(self, path: str) -> dict:
        result = self.request("GET", path)
        if not isinstance(result, dict) or not result.get("code"):
            raise IntegrationError("Bot lookup returned no code", uncertain=True)
        return result

    def _bot_write(self, method: str, path: str, payload: dict | None) -> dict:
        result = self.request(method, path, payload)
        return result if isinstance(result, dict) else {}

    def _named_pages(self, path: str, *, identity: str) -> list[dict]:
        """Cabinet lists use the official page envelope, or a bots/has_more page."""
        collected: list[dict] = []
        seen: set[str] = set()
        expected_total: int | None = None
        page = 1
        while page <= _MAX_PAGES:
            raw = self.request("GET", path, query={"page": page, "limit": _PAGE_LIMIT})
            items, more, total = _read_named_page(raw, identity=identity, page=page)
            if total is not None:
                if expected_total is None:
                    expected_total = total
                elif total != expected_total:
                    raise IntegrationError("Pagination metadata changed between pages", uncertain=True)
            for item in items:
                key = item.get(identity)
                if not isinstance(key, str) or not key or key in seen:
                    raise IntegrationError("Invalid bot page", uncertain=True)
                seen.add(key)
                collected.append(item)
            if not more:
                if expected_total is not None and len(collected) != expected_total:
                    raise IntegrationError("Incomplete bot list", uncertain=True)
                return collected
            page += 1
        raise IntegrationError("Bot list exceeded the safety cap", uncertain=True)


def _read_named_page(raw: object, *, identity: str, page: int) -> tuple[list[dict], bool, int | None]:
    """Official items/pagination page, or a cabinet bots/has_more page."""
    if not isinstance(raw, dict):
        raise IntegrationError("Unrecognized bot list", uncertain=True)
    if isinstance(raw.get("items"), list) and isinstance(raw.get("pagination"), dict):
        items, meta = _parse_page(raw, requested_page=page, requested_limit=_PAGE_LIMIT)
        if page == 1:
            _check_page_math(meta["total"], meta["pages"], _PAGE_LIMIT)
        more = meta["pages"] > page
        return items, more, meta["total"]
    items = raw.get("bots")
    if not isinstance(items, list):
        items = raw.get("items") if isinstance(raw.get("items"), list) else None
    if not isinstance(items, list) or not isinstance(raw.get("has_more"), bool):
        raise IntegrationError("Unrecognized bot list", uncertain=True)
    if any(not isinstance(item, dict) or not item.get(identity) for item in items):
        raise IntegrationError("Invalid bot page", uncertain=True)
    return items, bool(raw["has_more"]), None


def _confirmed_bot(result: object) -> dict:
    if not isinstance(result, dict):
        raise IntegrationError("3Commas returned no confirmed bot id", uncertain=True)
    for key in ("strategyId", "code", "botCode", "id"):
        value = result.get(key)
        if isinstance(value, str) and value:
            return result
    raise IntegrationError("3Commas returned no confirmed bot id", uncertain=True)
