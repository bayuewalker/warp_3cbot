"""Single-writer cycle. A model signal is not an order."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .accounts import execution_allowed
from .bots import (
    BotTemplate,
    allocate_client_id,
    build_dca_bot_payload,
    build_grid_payload,
    build_strategy_dca_payload,
    execution_fractions,
)
from .capital import (
    Exits,
    Policy,
    apply_ladder,
    approve,
    outbound_matches,
    readback_ok,
    score_candidate,
)
from .config import Settings
from .forward import evaluate_due
from .levels import derive_levels
from .market import MarketData, MarketDataError
from .models import Signal
from .ownership import warp_name
from .qualify import qualify
from .risk import GlobalRisk, pair_symbols
from .screener import ScreenerError, ScreenerFilters, select_symbols
from .state import OrderJournal
from .telemetry import log_event
from .threecommas import IntegrationError, StrategyNotFound, ThreeCommas
from .vibe import ResearchError, VibeResearch


@dataclass
class CycleStatus:
    opened: int = 0
    execution_permitted: bool = False
    risk_permitted: bool = False
    market_data_fresh: bool = False
    dependencies_healthy: bool = False
    reason: str = "not_run"


def _exits(template) -> Exits:
    return Exits(template.stop_enabled, template.take_profit_enabled, template.trailing_enabled)


def _broker_id_or_none(result: dict) -> str | None:
    if not isinstance(result, dict):
        return None
    for key in ("strategyId", "code", "botCode", "id"):
        value = result.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _broker_id(result: dict) -> str:
    found = _broker_id_or_none(result)
    if found:
        return found
    raise IntegrationError("3Commas returned no confirmed bot id", uncertain=True)


class Engine:
    def __init__(
        self,
        cfg: Settings,
        broker: ThreeCommas,
        research: VibeResearch,
        journal: OrderJournal,
        market: MarketData,
        *,
        screener=None,
        analyst=None,
    ):
        self.cfg = cfg
        self.broker = broker
        self.research = research
        self.journal = journal
        self.market = market
        self.screener = screener
        self.analyst = analyst
        self.guard = GlobalRisk(cfg)
        self.status = CycleStatus()
        self.universe = cfg.symbols
        self.evidence = {}
        self.screener_hits: list = []
        self.screener_error: str | None = None
        self.screener_source = ""
        self.last_positions: list[dict] = []
        self.last_decision = None
        self.last_opportunities: list[dict] = []
        self.last_accounts: list = []
        self.last_aegis: list[dict] = []
        self._trade_book: dict | None = None
        self._trade_book_error: str | None = None
        self._discovery_block: str | None = None

    def cycle(self) -> int:
        self.status = CycleStatus()
        try:
            now = datetime.now(timezone.utc)
            self._remember_accounts()
            account, positions = self._load_account()
            self.refresh_market(now)
            signals = self._research()
            opened = self._submit(account, positions, signals, now, submit=True)
            self._evaluate(now)
        except (IntegrationError, MarketDataError, ResearchError) as exc:
            self._mark_blocked(exc)
            raise
        self._heartbeat()
        return opened

    def monitor(self) -> int:
        """Reconcile and re-score risk. Pause does not skip this. It does not submit."""
        self.status = CycleStatus()
        try:
            now = datetime.now(timezone.utc)
            account, positions = self._load_account()
            self._mark_fresh(now)
            opened = self._submit(account, positions, [], now, submit=False)
        except (IntegrationError, MarketDataError, ResearchError) as exc:
            self._mark_blocked(exc)
            raise
        self._heartbeat()
        return opened

    def execute_cached(self, signals: list[Signal], now: datetime | None = None) -> int:
        """Submit only after research has finished. Still goes through the risk engine."""
        moment = now or datetime.now(timezone.utc)
        try:
            account, positions = self._load_account()
            opened = self._submit(account, positions, signals, moment, submit=True)
        except (IntegrationError, MarketDataError, ResearchError) as exc:
            self._mark_blocked(exc)
            raise
        self._heartbeat()
        return opened

    def refresh_market(self, now: datetime) -> None:
        self._refresh_screener()
        self.universe = self._select_universe(now)
        self.evidence = self.market.scan(self.universe, now=now) if self.universe else {}
        self._mark_fresh(now)

    def _refresh_screener(self) -> None:
        if self.screener is None:
            self.screener_hits = []
            self.screener_source = ""
            return
        try:
            self.screener_hits = self.screener.scan(self._filters())
            self.screener_source = getattr(self.screener, "source", "")
            self.screener_error = None
        except (ScreenerError, ValueError) as exc:
            self.screener_hits = []
            self.screener_source = ""
            self.screener_error = str(exc)[:200]

    def _filters(self) -> ScreenerFilters:
        raw = self.journal.get_setting("screener_filters")
        if not raw:
            return ScreenerFilters.from_settings(self.cfg)
        return ScreenerFilters.from_mapping(json.loads(raw))

    def collect_research(self) -> list[Signal]:
        """Called from the research thread. Must not touch the broker or the journal."""
        return self._research()

    def _select_universe(self, now: datetime) -> tuple[str, ...]:
        self._discovery_block = None
        if self.cfg.discovery == "screener":
            if self.screener is None or self.screener_error:
                self._discovery_block = "screener_unavailable"
                self._record([(now.isoformat(), "*", "universe", "screener_unavailable", "fail_closed")])
                return ()
            selected = select_symbols(
                self.screener_hits,
                allow=set(self.cfg.discovery_allow),
                deny=set(self.cfg.deny_symbols),
                limit=self.cfg.max_candidates,
            )
            if not selected:
                self._discovery_block = "no_eligible_market"
                self._record([(now.isoformat(), "*", "universe", "no_eligible_market", "fail_closed")])
                return ()
            return selected
        if self.cfg.discovery != "auto":
            # The global denylist is a safety policy, not an auto-discovery option.
            # Static mode must never bypass it (including through old saved presets).
            chosen = tuple(symbol for symbol in self.cfg.symbols if symbol not in self.cfg.deny_symbols)
            if not chosen:
                self._discovery_block = "no_eligible_market"
                self._record([(now.isoformat(), "*", "universe", "no_eligible_market", "fail_closed")])
            return chosen
        try:
            selected, rejected = self.market.discover(now=now)
        except MarketDataError:
            self._discovery_block = "discovery_unavailable"
            self._record([(now.isoformat(), "*", "universe", "discovery_unavailable", "fail_closed")])
            return ()
        self._record(
            [(now.isoformat(), symbol, "universe", reason, "rejected") for symbol, reason in rejected]
        )
        if not selected:
            self._discovery_block = "no_eligible_market"
            self._record([(now.isoformat(), "*", "universe", "no_eligible_market", "fail_closed")])
            return ()
        return selected

    def _research(self) -> list[Signal]:
        if not self.universe:
            return []
        analyst = self.analyst
        if self.cfg.analysis in {"llm", "auto"} and analyst is not None and analyst.configured():
            return analyst.research(self.universe, self.evidence, self.cfg.max_signal_age)
        if self.cfg.analysis == "llm":
            raise ResearchError("LLM analysis is not configured")
        return self.research.research(self.universe, self.cfg.max_signal_age)

    def _submit(self, account, positions, signals, now, *, submit: bool) -> int:
        deduped: list[Signal] = []
        seen: set[str] = set()
        notes: list[tuple[str, str, str, str, str]] = []
        replace_cards = submit or bool(signals)
        if replace_cards:
            self.last_opportunities = []
        template = self._template()
        stamp = now.astimezone(timezone.utc).isoformat()
        for signal in signals:
            if signal.symbol in seen:
                notes.append((stamp, signal.symbol, "research", "duplicate", "rejected"))
                continue
            seen.add(signal.symbol)
            enriched = replace(signal, evidence=self.evidence.get(signal.symbol))
            candles = getattr(self.market, "last_candles", {}).get(signal.symbol)
            gated, card = qualify(enriched, enriched.evidence, candles, self.cfg, template)
            # Timestamp/provenance let the dashboard flag old research instead of
            # presenting a retained card as an actionable live opportunity.
            card["as_of"] = gated.as_of.astimezone(timezone.utc).isoformat()
            card["source"] = gated.source
            if enriched.evidence is not None:
                card["score"] = score_candidate(
                    regime=enriched.evidence.regime,
                    rsi=enriched.evidence.rsi,
                    quote_volume=enriched.evidence.quote_volume,
                    spread=None,
                    change_24h=None,
                    tradable=enriched.evidence.tradable,
                    volatility=enriched.evidence.volatility,
                    policy=Policy.from_settings(self.cfg),
                    weights=template.weight_map(),
                ).to_dict()
                card["min_score"] = template.min_score
                card["score_warning"] = template.min_score < 80
                card["weights"] = template.weight_map()
            self.last_opportunities.append(card)
            if enriched.evidence is not None and enriched.evidence.last_price > 0:
                self.journal.record_verdict(
                    gated.symbol, gated.action, gated.confidence, enriched.evidence.last_price, now
                )
            deduped.append(gated)
            notes.append((stamp, signal.symbol, "research", gated.action, "accepted"))
            if gated.action != signal.action:
                notes.append((stamp, signal.symbol, "backtest", card.get("backtest_reason", ""), "rejected"))
        if replace_cards:
            self.last_opportunities.sort(key=lambda card: Decimal(card.get("blended") or "0"), reverse=True)
        for symbol, item in self.evidence.items():
            notes.append((stamp, symbol, "market", item.reason, "passed" if item.passed else "rejected"))
        self._mark_fresh(now)
        paused = self._halted()
        if account is None:
            self.status.dependencies_healthy = True
            self.status.execution_permitted = False
            self.status.risk_permitted = False
            self.status.reason = "halt" if paused else "observe_without_account"
            self._record(notes)
            log_event("halt" if paused else "observe", signals=len(deduped), fresh=self.status.market_data_fresh)
            return 0

        prices, price_problem = self._price_map(account, positions, now)
        if price_problem:
            self.status.dependencies_healthy = True
            self.status.execution_permitted = False
            self.status.risk_permitted = False
            self.status.reason = "halt" if paused else price_problem
            self._record(notes)
            log_event("entries_paused", mode=self.cfg.mode, reason=self.status.reason)
            return 0

        decision = self.guard.assess(
            account,
            positions,
            deduped,
            prices=prices,
            marks=self.journal.equity_marks(),
            now=now,
            evidence=self.evidence,
            extra_commitments=self._untracked(positions),
        )
        self.last_decision = decision
        if decision.equity is not None:
            self.journal.record_equity(decision.equity, decision.equity_reliable, now=now)
        self.status.dependencies_healthy = True
        self.status.risk_permitted = decision.execution_permitted
        self.status.execution_permitted = bool(
            submit and decision.execution_permitted and self.cfg.mode != "observe" and not paused
        )
        self.status.reason = decision.reason
        if self._discovery_block and decision.execution_permitted:
            self.status.reason = self._discovery_block
            self.status.risk_permitted = False
            self.status.execution_permitted = False
        if paused:
            self.status.reason = "halt"
            self.status.execution_permitted = False
        planned = {plan.symbol for plan in decision.orders}
        for signal in deduped:
            if signal.symbol in planned:
                notes.append((stamp, signal.symbol, "risk", decision.reason, "planned"))
            elif signal.action == "BUY":
                notes.append((stamp, signal.symbol, "risk", decision.reason, "rejected"))
        if paused or self._discovery_block or not decision.execution_permitted or not submit:
            self._record(notes)
            if paused:
                log_event("halt", mode=self.cfg.mode)
            elif not self.status.risk_permitted:
                log_event("entries_paused", mode=self.cfg.mode, reason=self.status.reason)
            return 0

        opened = 0
        self.last_aegis = []
        symbols = [str(plan.symbol) for plan in decision.orders]
        self._load_trade_book(symbols, now)
        for plan in decision.orders:
            if self.journal.recently_submitted(plan.symbol):
                notes.append((stamp, plan.symbol, "broker", "duplicate_cooldown", "skipped"))
                log_event("duplicate_skipped", symbol=plan.symbol)
                continue
            if opened >= 1:
                break
            if self._halted():
                self.status.reason = "halt"
                self.status.execution_permitted = False
                notes.append((stamp, plan.symbol, "broker", "halt", "not_submitted"))
                break
            if self.cfg.mode == "observe":
                notes.append((stamp, plan.symbol, "broker", "observe", "not_submitted"))
                log_event(
                    "observe_candidate",
                    symbol=plan.symbol,
                    base=format(plan.base_amount, "f"),
                    commitment=format(plan.full_commitment, "f"),
                )
                opened += 1
                continue
            template = self._template()
            allowed, why = execution_allowed(self.cfg.mode, account.demo, self.cfg.live_ack)
            if not allowed:
                notes.append((stamp, plan.symbol, "broker", why, "not_submitted"))
                self.status.execution_permitted = False
                continue
            if getattr(account, "market", "spot") not in {"", "spot"}:
                notes.append((stamp, plan.symbol, "broker", "spot_only", "not_submitted"))
                self.status.execution_permitted = False
                continue
            if self._trade_book_error:
                self.last_aegis.append(
                    {"symbol": plan.symbol, "status": "WAIT_DATA_INCOMPLETE", "reasons": [self._trade_book_error], "warnings": []}
                )
                notes.append((stamp, plan.symbol, "broker", "WAIT_DATA_INCOMPLETE", "not_submitted"))
                self.status.execution_permitted = False
                continue
            book = self._approve(account, plan, now)
            self._remember_aegis(plan.symbol, book, account)
            if book.decision != "APPROVED" or book.ladder is None:
                notes.append((stamp, plan.symbol, "broker", ",".join(book.reasons) or book.decision, "not_submitted"))
                self.status.execution_permitted = False
                continue
            self._approved_ladder = book.ladder
            client_id = allocate_client_id(self.journal, plan.symbol, template, now)
            if client_id is None:
                notes.append((stamp, plan.symbol, "broker", "duplicate_client", "skipped"))
                log_event("duplicate_skipped", symbol=plan.symbol)
                continue
            try:
                result = self._place(plan, client_id, template, account)
            except IntegrationError as exc:
                notes.append((stamp, plan.symbol, "broker", "rejected" if not exc.uncertain else "unknown", "halted"))
                self._record(notes)
                if not exc.uncertain:
                    self.journal.reject(client_id)
                if getattr(self, "_pending_warp", None) and not getattr(self, "_kept_order", False):
                    self.journal.update_warp_bot(
                        self._pending_warp,
                        status="rejected" if not exc.uncertain else "unknown",
                        updated_at=datetime.now(timezone.utc).isoformat(),
                    )
                self._kept_order = False
                raise
            broker_id = _broker_id(result)
            self.journal.confirm(client_id, broker_id)
            if getattr(self, "_pending_warp", None):
                self.journal.update_warp_bot(
                    self._pending_warp,
                    status="confirmed",
                    external_code=broker_id,
                    updated_at=datetime.now(timezone.utc).isoformat(),
                )
            self.journal.enqueue_alert(
                f"order:{plan.symbol}:{client_id}",
                f"{self.cfg.mode} {template.kind} {plan.symbol} {broker_id}",
                now=now,
            )
            notes.append((stamp, plan.symbol, "broker", "confirmed", broker_id))
            log_event("strategy_confirmed", symbol=plan.symbol, strategy_id=broker_id)
            opened += 1
        self.status.opened = opened
        self._record(notes)
        return opened

    def _template(self) -> BotTemplate:
        raw = self.journal.get_setting("bot_template")
        if not raw:
            return BotTemplate()
        return BotTemplate.from_mapping(json.loads(raw))

    def _build(self, plan, client_id: str, template: BotTemplate, account, name: str) -> tuple[dict, dict]:
        if template.market != "spot":
            raise IntegrationError("WARP trades Binance spot only", uncertain=False)
        profile = account.code or self.cfg.profile_code
        exchange = account.exchange_code or self.cfg.exchange_code
        ladder = getattr(self, "_approved_ladder", None)
        if template.kind == "grid":
            stop_fraction, take_fraction = execution_fractions(self.cfg, template)
            levels = derive_levels(
                self.evidence.get(plan.symbol),
                self.cfg,
                stop_fraction=stop_fraction,
                take_fraction=take_fraction,
            )
            if levels is None:
                raise IntegrationError("Grid levels unavailable", uncertain=False)
            request, total = build_grid_payload(
                self.cfg, plan, levels, client_id, template, profile_code=profile, exchange_code=exchange
            )
            return request, {"product": "grid_bot", "commitment": format(total, "f"), "request": request}
        if template.product == "dca_bot":
            request = build_dca_bot_payload(
                self.cfg, plan, client_id, template, name=name, profile_code=profile, exchange_code=exchange
            )
            if ladder is not None:
                request = apply_ladder(request, ladder, _exits(template))
            return request, {
                "product": "dca_bot",
                "commitment": format(plan.full_commitment if ladder is None else ladder.commitment, "f"),
                "request": request,
            }
        request = build_strategy_dca_payload(self.cfg, plan, client_id, template)
        if ladder is not None:
            request = apply_ladder(request, ladder, _exits(template))
        return request, request

    def _place(self, plan, client_id: str, template: BotTemplate, account) -> dict:
        warp_id = secrets.token_hex(4)
        name = warp_name(warp_id)
        self._pending_warp = warp_id
        request, envelope = self._build(plan, client_id, template, account, name)
        stamp = datetime.now(timezone.utc).isoformat()
        self.journal.insert_warp_bot(
            {
                "warp_id": warp_id,
                "name": name,
                "client_id": client_id,
                "external_code": None,
                "kind": template.kind,
                "product": envelope["product"] if isinstance(envelope, dict) and envelope.get("product") else template.product,
                "account_code": account.code,
                "market": "spot",
                "symbol": plan.symbol,
                "status": "pending",
                "commitment": format(plan.full_commitment, "f"),
                "created_at": stamp,
                "updated_at": stamp,
            }
        )
        self.journal.reserve(client_id, plan.symbol, envelope if isinstance(envelope, dict) else request)
        product = envelope.get("product") if isinstance(envelope, dict) else template.product
        exits = _exits(template)
        if product != "grid_bot" and not outbound_matches(request, exits):
            raise IntegrationError("Exit toggles do not match the order", uncertain=False)
        if product == "dca_bot":
            result = self.broker.create_dca_bot(request)
        elif product == "grid_bot":
            result = self.broker.create_grid_bot(request)
        else:
            result = self.broker.create_strategy(request)
        if isinstance(result, dict) and not readback_ok(result, exits):
            kept = _broker_id_or_none(result)
            if kept:
                self.journal.confirm(client_id, kept)
                self.journal.update_warp_bot(
                    warp_id,
                    status="confirmed",
                    external_code=kept,
                    updated_at=datetime.now(timezone.utc).isoformat(),
                )
                self._kept_order = True
            raise IntegrationError("3Commas read-back does not match the exit toggles", uncertain=True)
        return result

    def _untracked(self, positions) -> tuple[tuple[str, Decimal], ...]:
        live = {
            str(row.get("clientId"))
            for row in positions
            if isinstance(row, dict) and row.get("clientId")
        }
        rows: list[tuple[str, Decimal]] = []
        for row in self.journal.confirmed_submissions():
            if row["client_id"] in live:
                continue
            payload = row["payload"]
            if payload.get("product") not in {"grid_bot", "dca_bot"}:
                continue
            rows.append((str(row["symbol"]), Decimal(str(payload.get("commitment") or "0"))))
        return tuple(rows)

    def _load_account(self):
        target = self._target()
        code = str(target.get("code")) if target and target.get("code") else self.cfg.profile_code
        if self.cfg.api_key and self.cfg.api_secret and code:
            if target and target.get("market") not in {None, "spot"}:
                raise IntegrationError("WARP trades Binance spot only", uncertain=False)
            exchange = str(target.get("exchange_code") or self.cfg.exchange_code) if target else self.cfg.exchange_code
            account = self.broker.get_account(code, exchange)
            self._require_mode_matches(account.demo)
            positions = self.broker.get_live()
            self.last_positions = positions
            self._reconcile(positions)
            return account, positions
        if self.cfg.mode != "observe":
            raise IntegrationError("Trading requires configured credentials", uncertain=False)
        self.last_positions = []
        return None, []

    def _needed_symbols(self, account, positions) -> set[str]:
        needed: set[str] = set()
        for row in positions:
            if not isinstance(row, dict):
                continue
            pair = row.get("pair")
            if isinstance(pair, str) and pair.endswith("USDT"):
                needed.add(pair)
            try:
                base = pair_symbols(row).get("base")
            except IntegrationError:
                base = None
            if base:
                needed.add(f"{base}USDT")
        if account is not None:
            for asset, balance in account.holdings().items():
                if asset != "USDT" and balance.total > 0:
                    needed.add(f"{asset}USDT")
        return needed

    def _price_map(self, account, positions, now: datetime) -> tuple[dict, str | None]:
        """Never mark a held asset from a stale candle snapshot.

        Active positions and wallet assets need fresher quote marks than signal
        research. Fetch a new public quote if their cached market snapshot is
        older than the account freshness ceiling; fail closed on lookup errors.
        """
        prices = {symbol: item.last_price for symbol, item in self.evidence.items()}
        required = sorted(self._needed_symbols(account, positions))
        quote = getattr(self.market, "last_price", None)
        for symbol in required:
            item = self.evidence.get(symbol)
            age = None if item is None or item.as_of.tzinfo is None else (
                now - item.as_of.astimezone(timezone.utc)
            ).total_seconds()
            if (age is not None and -60 <= age <= self.cfg.max_account_age
                    and item.last_price.is_finite() and item.last_price > 0):
                continue
            if quote is None:
                return prices, "mark_unavailable"
            try:
                fresh = Decimal(str(quote(symbol)))
            except (MarketDataError, ValueError, TypeError):
                return prices, "mark_unavailable"
            if not fresh.is_finite() or fresh <= 0:
                return prices, "mark_unavailable"
            prices[symbol] = fresh
        return prices, None

    def _fresh(self, now: datetime) -> bool:
        if not self.evidence:
            return False
        limit = self.cfg.max_signal_age * 60
        return all(
            (now - item.as_of.astimezone(timezone.utc)).total_seconds() <= limit for item in self.evidence.values()
        )

    def _mark_fresh(self, now: datetime) -> None:
        self.status.market_data_fresh = self._fresh(now)

    def _mark_blocked(self, exc: Exception) -> None:
        self.status.reason = type(exc).__name__
        self.status.dependencies_healthy = False
        self.status.execution_permitted = False
        self.status.risk_permitted = False
        log_event("cycle_blocked", mode=self.cfg.mode, error=type(exc).__name__)

    def _heartbeat(self) -> None:
        self.journal.heartbeat(
            {
                "mode": self.cfg.mode,
                "reason": self.status.reason,
                "execution_permitted": self.status.execution_permitted,
                "risk_permitted": self.status.risk_permitted,
                "market_data_fresh": self.status.market_data_fresh,
            }
        )

    def _record(self, rows: list[tuple[str, str, str, str, str]]) -> None:
        if rows:
            self.journal.record_decisions(rows)

    def _require_mode_matches(self, demo: bool) -> None:
        if self.cfg.mode == "paper" and not demo:
            raise IntegrationError("Paper mode refuses a real-money 3Commas profile", uncertain=False)
        if self.cfg.mode == "live" and demo:
            raise IntegrationError("Live mode refuses a demo 3Commas profile", uncertain=False)

    def _reconcile(self, positions: list[dict]) -> None:
        found = {
            str(row.get("clientId")): str(row.get("strategyId"))
            for row in positions
            if row.get("clientId") and row.get("strategyId")
        }
        for client_id in list(self.journal.unresolved()):
            if client_id in found:
                continue
            try:
                strategy = self.broker.get_strategy(client_id)
            except StrategyNotFound:
                continue
            if strategy.get("strategyId"):
                found[client_id] = str(strategy["strategyId"])
        unresolved = self.journal.reconcile_ids(found)
        if unresolved:
            raise IntegrationError(f"{len(unresolved)} trade submissions unreconciled: entries halted")
        self._retire_finished(positions)

    def _retire_finished(self, positions: list[dict]) -> None:
        by_client = {str(row.get("clientId")): row for row in positions if row.get("clientId")}
        by_id = {str(row.get("strategyId")): row for row in positions if row.get("strategyId")}
        terminal = {"completed", "canceled", "cancelled", "panic_exited", "unlinked", "failed", "closed"}
        for bot in self.journal.warp_bots():
            if bot.get("status") not in {"confirmed", "active", "paused"}:
                continue
            row = by_client.get(str(bot.get("client_id") or "")) or by_id.get(str(bot.get("external_code") or ""))
            if row is None and bot.get("client_id") and hasattr(self.broker, "get_strategy"):
                try:
                    row = self.broker.get_strategy(bot["client_id"])
                except (StrategyNotFound, IntegrationError):
                    continue
            if isinstance(row, dict) and str(row.get("status") or "") in terminal:
                self.journal.update_warp_bot(
                    bot["warp_id"],
                    status="closed",
                    updated_at=datetime.now(timezone.utc).isoformat(),
                )

    def _halted(self) -> bool:
        return Path(self.cfg.db_path).parent.joinpath("HALT").exists()

    def _target(self) -> dict | None:
        raw = self.journal.get_setting("target_account")
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _load_trade_book(self, symbols: list[str], now: datetime) -> None:
        self._trade_book = None
        self._trade_book_error = None
        loader = getattr(self.market, "tradebook_context", None)
        if loader is None:
            return
        try:
            self._trade_book = loader(tuple(dict.fromkeys(symbols)), now=now)
        except (MarketDataError, ValueError, TypeError):
            self._trade_book_error = "market_data"

    def _remember_aegis(self, symbol: str, book, account) -> None:
        status = book.decision
        if status == "APPROVED":
            if self.cfg.mode == "live":
                status = "APPROVED_LIVE_PENDING_TRADE_DESK"
            elif self.cfg.mode == "paper" and getattr(account, "demo", False):
                status = "APPROVED_DEMO"
            else:
                status = "APPROVED_PAPER"
        self.last_aegis.append(
            {
                "symbol": symbol,
                "status": status,
                "reasons": list(book.reasons),
                "warnings": list(getattr(book, "warnings", [])),
                "nav": format(book.nav, "f"),
            }
        )

    def _approve(self, account, plan, now):
        evidence = self.evidence.get(plan.symbol)
        policy = Policy.from_settings(self.cfg)
        saved = self.journal.get_setting("trade_rules")
        if saved:
            from .capital import apply_rule_edits

            try:
                policy = apply_rule_edits(policy, json.loads(saved))
            except (ValueError, json.JSONDecodeError):
                from .capital import Decision

                return Decision("WAIT_DATA_INCOMPLETE", ["rules"])
        gate = None
        tape = None
        corr = None
        book = self._trade_book or {}
        if book:
            tape = book.get("tape")
            table = book.get("correlation") or {}
            def lookup(left: str, right: str, table=table) -> object:
                return table.get((left, right))

            corr = lookup
            gate = (book.get("gates") or {}).get(plan.symbol)
            if gate is None:
                from .capital import Decision

                return Decision("WAIT_DATA_INCOMPLETE", ["market_data"])
        score = None
        if evidence is not None:
            template = self._template()
            score = score_candidate(
                regime=evidence.regime,
                rsi=evidence.rsi,
                quote_volume=evidence.quote_volume,
                spread=None,
                change_24h=None,
                tradable=evidence.tradable,
                volatility=evidence.volatility,
                policy=policy,
                weights=template.weight_map(),
            )
        bots = [row for row in self.journal.warp_bots(account.code) if row.get("status") in {"pending", "unknown", "confirmed", "active", "paused"}]
        warp_committed = sum((Decimal(str(row.get("commitment") or "0")) for row in bots), Decimal("0"))
        return approve(
            policy=policy,
            account_usdt=account.total_usdt,
            free_usdt=account.free_usdt,
            warp_owned=Decimal("0"),
            warp_committed=warp_committed,
            warp_pending_so=Decimal("0"),
            non_warp_spent=account.used_usdt,
            non_warp_future_so=self._foreign_future_so(),
            open_deals=len(bots),
            open_stop_risk=Decimal("0"),
            open_symbols=[str(row.get("symbol")) for row in bots],
            price=evidence.last_price if evidence is not None else plan.base_amount,
            minimum=evidence.min_notional if evidence is not None else self.cfg.min_base,
            model_buy=True,
            score=score,
            min_score=self._template().min_score,
            exits=_exits(self._template()),
            max_commitment=plan.full_commitment,
            max_base=plan.base_amount,
            symbol=plan.symbol,
            now=now,
            tape=tape,
            corr=corr,
            quote_volume=gate["quote_volume"] if gate else (evidence.quote_volume if evidence is not None else policy.min_quote_volume),
            spread=None if gate is None else gate.get("spread"),
            change_24h=None if gate is None else gate.get("change_24h"),
            listed_days=None if gate is None else gate.get("listed_days"),
            require_listing=gate is not None,
            tradable=True if gate is None else bool(gate.get("tradable")),
            on_binance_spot=True if gate is None else bool(gate.get("on_binance_spot")),
            atr_1h_pct=None if gate is None else gate.get("atr_1h_pct"),
            stale=bool(gate and (gate.get("quote_age") is None or gate["quote_age"] > 30 or (evidence is not None and (now - evidence.as_of.astimezone(timezone.utc)).total_seconds() > 300))),
        )

    def _foreign_future_so(self) -> Decimal:
        if not hasattr(self.broker, "list_dca_bots"):
            return Decimal("0")
        try:
            rows = self.broker.list_dca_bots()
        except IntegrationError:
            return Decimal("0")
        total = Decimal("0")
        for row in rows:
            if str(row.get("name") or "").startswith("WARP-"):
                continue
            text = str(row.get("maxRequiredAmount") or "")
            number = "".join(ch for ch in text if ch.isdigit() or ch == ".")
            if number:
                total += Decimal(number)
        return total

    def _resolve_bot(self, result: dict, name: str, kind: str) -> dict:
        if _broker_id_or_none(result):
            return result
        listed = self.broker.list_dca_bots() if kind == "dca" else self.broker.list_grid_bots()
        matches = [row for row in listed if isinstance(row, dict) and row.get("name") == name]
        if len(matches) != 1 or not matches[0].get("code"):
            raise IntegrationError("3Commas returned no confirmed bot id", uncertain=True)
        return {"code": str(matches[0]["code"]), "name": name}

    def _remember_accounts(self) -> None:
        if not self.cfg.api_key or not self.cfg.api_secret or not hasattr(self.broker, "list_accounts"):
            return
        try:
            self.last_accounts = self.broker.list_accounts()
        except IntegrationError:
            return

    def _evaluate(self, now: datetime) -> None:
        price_at = getattr(self.market, "price_at", None)
        if price_at is None:
            return
        evaluate_due(self.journal, price_at, now, limit=20)
