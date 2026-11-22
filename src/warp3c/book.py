"""Read-only open positions for every bot on each 3Commas account.

Display only. Nothing here can place, change, or close an order. Rows from bots
WARP did not create are shown with an external badge and are never acted on.
A field 3Commas does not report stays None; it is not estimated or filled in,
except the current price and unrealized PnL, which may use the public Binance
ticker when 3Commas omits them. Those values carry their source.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from .ownership import PREFIX
from .threecommas import IntegrationError

POSITIONS_SECONDS = 60
MAX_POSITION_READS = 40
USD_QUOTES = {"USDT", "USDC", "FDUSD", "USD"}


def _dec(value: object, *, positive: bool = False) -> Decimal | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or (positive and number <= 0):
        return None
    return number


def _text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(value.normalize(), "f")
    return "0" if text in {"-0", ""} else text


def _count(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _iso(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).isoformat()


def _str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def bot_account(bot: dict) -> tuple[str | None, str | None]:
    for entry in bot.get("profiles") or []:
        profile = entry.get("profile") if isinstance(entry, dict) else None
        if isinstance(profile, dict) and _str(profile.get("code")):
            return profile["code"], _str(profile.get("name_of_account"))
    return None, None


def open_count(bot: dict) -> int | None:
    pnl = bot.get("unrealizedPnl")
    return _count(pnl.get("cnt")) if isinstance(pnl, dict) else None


def _summary_pnl(bot: dict | None, row_id: str | None) -> Decimal | None:
    if not bot or not row_id:
        return None
    pnl = bot.get("unrealizedPnl")
    summary = pnl.get("summary") if isinstance(pnl, dict) else None
    entry = summary.get(row_id) if isinstance(summary, dict) else None
    return _dec(entry.get("pnlFiat")) if isinstance(entry, dict) else None


def _side(bot: dict | None, kind: str) -> str | None:
    if not bot:
        return None
    key = "dcaTradingType" if kind == "dca" else "gridTradingType"
    value = bot.get(key)
    return value if value in {"long", "short"} else None


def _row_account(row: dict) -> tuple[str | None, str | None]:
    for item in row.get("profileStrategies") or []:
        profile = item.get("profile") if isinstance(item, dict) else None
        if isinstance(profile, dict) and _str(profile.get("code")):
            return profile["code"], _str(profile.get("name_of_account"))
    return None, None


def parse_position(row: dict, *, bot: dict | None, kind: str, owner: str | None = None) -> dict:
    """One cabinet position row. Missing or malformed fields become None."""
    row_id = _str(row.get("_id")) or _str(row.get("strategyId")) or _str(row.get("id"))
    code, name = _row_account(row)
    if code is None and bot:
        code, name = bot_account(bot)
    symbols = row.get("pairSymbols") if isinstance(row.get("pairSymbols"), dict) else {}
    base_asset = _str(symbols.get("ref"))
    quote_asset = _str(symbols.get("base"))
    pair = _str(row.get("pair"))
    if pair and "/" in pair and not (base_asset and quote_asset):
        base_asset, quote_asset = pair.split("/", 1)
    entered = _dec(row.get("totalEntryAmount"))
    exited = _dec(row.get("totalExitAmount"))
    # Unknown exits must not imply that the entire entry is still held.
    size = entered - exited if entered is not None and exited is not None and entered - exited > 0 else None
    entry_cost = _dec(row.get("totalEntryCost"))
    exit_cost = _dec(row.get("totalExitCost"))
    cost = entry_cost - exit_cost if entry_cost is not None and exit_cost is not None and entry_cost - exit_cost > 0 else None
    last = _dec(row.get("lastPrice"), positive=True)
    pnl_pct = _dec(row.get("currentPosition"))
    pnl_usd = _summary_pnl(bot, row_id)
    take_profits = []
    for item in row.get("takeProfits") or []:
        price = _dec(item.get("price"), positive=True) if isinstance(item, dict) else None
        if price is not None:
            take_profits.append(_text(price))
    bot_name = _str(bot.get("name")) if bot else _str(row.get("name"))
    if owner is None:
        owner = "warp" if bot_name and bot_name.startswith(PREFIX) else "external"
    return {
        "id": row_id,
        "account_code": code,
        "account_name": name,
        "kind": kind,
        "bot_name": bot_name,
        "bot_code": _str(bot.get("code")) if bot else None,
        "owner": owner,
        "pair": pair,
        "symbol": _str(row.get("pairCode")) or (pair.replace("/", "") if pair else None),
        "base_asset": base_asset,
        "quote_asset": quote_asset,
        "side": _side(bot, kind),
        "status": _str(row.get("status")),
        "paper": row.get("paperTrading") if isinstance(row.get("paperTrading"), bool) else None,
        "average_price": _text(_dec(row.get("price"), positive=True)),
        "size": _text(size),
        "cost": _text(cost),
        "current_price": _text(last),
        "current_price_source": "3commas" if last is not None else None,
        "pnl_usd": _text(pnl_usd),
        "pnl_usd_source": "3commas" if pnl_usd is not None else None,
        "pnl_pct": _text(pnl_pct),
        "pnl_pct_source": "3commas" if pnl_pct is not None else None,
        "safety_used": _count(row.get("extraOrderCompletedCount")),
        "safety_max": _count(row.get("extraOrderCount")),
        "take_profits": take_profits,
        "stop_price": _text(_dec(row.get("stopPrice"), positive=True)),
        "opened_at": _iso(row.get("enteredAt")) or _iso(row.get("createdAt")),
    }


def _strategy_symbols(row: dict) -> tuple[str | None, str | None]:
    """SmartTrade schema: pairSymbols.base is the traded asset, pairSymbols.quote the quote (see risk.pair_symbols)."""
    symbols = row.get("pairSymbols")
    if not isinstance(symbols, dict):
        exchange = row.get("exchange") if isinstance(row.get("exchange"), dict) else {}
        symbols = exchange.get("pairSymbols") if isinstance(exchange.get("pairSymbols"), dict) else {}
    return _str(symbols.get("base")), _str(symbols.get("quote"))


def parse_strategy(row: dict, *, owner: str, stale: bool = False) -> dict:
    """One SmartTrade from /open_api/strategies/live. Numeric fields match the cabinet rows;
    the symbol mapping does not, so it is read separately."""
    parsed = parse_position(row, bot=None, kind="strategy", owner=owner)
    base, quote = _strategy_symbols(row)
    raw_pair = _str(row.get("pair"))
    if raw_pair and "/" in raw_pair and not (base and quote):
        base, quote = raw_pair.split("/", 1)
    symbol = raw_pair.replace("/", "") if raw_pair else (f"{base}{quote}" if base and quote else None)
    parsed.update(
        {
            "base_asset": base,
            "quote_asset": quote,
            "pair": f"{base}/{quote}" if base and quote else raw_pair,
            "symbol": symbol,
            "bot_name": _str(row.get("name")) or _str(row.get("clientId")),
            "stale": stale,
        }
    )
    return parsed


def _bot_positions(raw: object) -> list[dict]:
    if not isinstance(raw, dict) or not isinstance(raw.get("rows"), list):
        raise IntegrationError("Unrecognized positions response", uncertain=True)
    return [row for row in raw["rows"] if isinstance(row, dict)]


def collect(
    broker,
    *,
    strategies: list[dict] | None = None,
    warp_client=None,
    strategies_as_of: str | None = None,
    strategy_error: str | None = None,
) -> dict:
    """GET every bot list, then GET positions for bots that report open positions."""
    rows: list[dict] = []
    errors: list[str] = []
    checked = 0
    reads = 0
    for kind, lister in (("dca", "list_dca_bots"), ("grid", "list_grid_bots")):
        try:
            bots = getattr(broker, lister)()
        except IntegrationError as exc:
            errors.append(f"{kind}_bots: {str(exc)[:120]}")
            continue
        for bot in bots:
            if not isinstance(bot, dict) or not _str(bot.get("code")):
                continue
            checked += 1
            count = open_count(bot)
            # cnt is the bot's own open-position count. Without it, only running bots are read.
            if count == 0 or (count is None and bot.get("status") not in {"active", "running"}):
                continue
            if reads >= MAX_POSITION_READS:
                errors.append("position_read_cap")
                break
            reads += 1
            try:
                found = _bot_positions(broker.get_bot_positions(kind, bot["code"]))
            except IntegrationError as exc:
                errors.append(f"{kind} {bot.get('name') or bot['code']}: {str(exc)[:120]}")
                continue
            rows.extend(parse_position(row, bot=bot, kind=kind) for row in found)
    stale = strategy_error is not None
    if stale:
        errors.append(f"strategies: {str(strategy_error)[:120]}")
    for row in strategies or []:
        if not isinstance(row, dict):
            continue
        client = _str(row.get("clientId"))
        owner = "warp" if client and warp_client is not None and warp_client(client) else "external"
        rows.append(parse_strategy(row, owner=owner, stale=stale))
    as_of = datetime.now(timezone.utc).isoformat()
    # Retained SmartTrades are only as fresh as the last read that succeeded.
    if stale and strategies and strategies_as_of:
        as_of = min(as_of, _iso(strategies_as_of) or as_of)
    return {
        "rows": rows,
        "errors": errors,
        "bots_checked": checked,
        "position_reads": reads,
        "as_of": as_of,
        "stale": stale,
    }


def _with_market(row: dict, prices: dict, fresh: bool) -> dict:
    row = dict(row)
    avg = _dec(row.get("average_price"), positive=True)
    size = _dec(row.get("size"), positive=True)
    current = _dec(row.get("current_price"), positive=True)
    if current is None and fresh and row.get("symbol") in prices:
        current = _dec((prices.get(row["symbol"]) or {}).get("last"), positive=True)
        if current is not None:
            row["current_price"] = _text(current)
            row["current_price_source"] = "binance"
    sign = {"long": 1, "short": -1}.get(row.get("side") or "")
    if row.get("pnl_pct") is None and avg and current and sign:
        row["pnl_pct"] = _text(((current / avg) - 1) * 100 * sign)
        row["pnl_pct_source"] = "computed"
    if row.get("pnl_usd") is None and avg and current and size and sign and row.get("quote_asset") in USD_QUOTES:
        row["pnl_usd"] = _text((current - avg) * size * sign)
        row["pnl_usd_source"] = "computed"
    return row


def positions_view(book: dict | None, *, prices: dict, prices_fresh: bool, error: str | None = None) -> dict:
    """Public payload for GET /api/account-positions and the positions stream event."""
    if not book:
        return {
            "positions": [],
            "by_account": {},
            "as_of": None,
            "error": error or "not_loaded",
            "errors": [],
            "stale": False,
            "cadence_seconds": POSITIONS_SECONDS,
            "readonly": True,
        }
    rows = [_with_market(row, prices, prices_fresh) for row in book.get("rows", [])]
    by_account: dict[str, int] = {}
    for row in rows:
        key = row.get("account_code") or "unknown"
        by_account[key] = by_account.get(key, 0) + 1
    return {
        "positions": rows,
        "by_account": by_account,
        "as_of": book.get("as_of"),
        "error": error,
        "errors": list(book.get("errors") or []),
        "bots_checked": book.get("bots_checked"),
        "stale": bool(book.get("stale")),
        "cadence_seconds": POSITIONS_SECONDS,
        "readonly": True,
    }
