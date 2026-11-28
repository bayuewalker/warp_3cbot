"""Single-writer cycle. A model signal is not an order."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
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
from .llm import warn_budget_short
from .market import MarketData, MarketDataError
from .models import Signal, positive
from .notify import channel_configured, enqueue_incident
from .ownership import warp_name
from .qualify import qualify
from .risk import (
    GlobalRisk,
    is_warp_strategy,
    net_base_qty,
    pair_symbols,
    realized_pnl,
    unfilled_quote,
)
from .screener import ScreenerError, ScreenerFilters, select_symbols
from .signalcard import block_why, blocked_card, halt_off_card, halt_on_card, spot_card
from .state import OrderJournal
from .telemetry import log_event
from .threecommas import IntegrationError, StrategyNotFound, ThreeCommas
from .universe import excluded_symbol
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


def _deal_closed_detail(
    row: dict,
    *,
    mark=None,
    min_notional=None,
    dust_usdt=Decimal("1"),
) -> str:
    """Realized quote P&L and fee. No account code and no wallet balance."""
    status = str(row.get("status") or "")
    pnl = "unknown"
    fee = "unknown"
    try:
        amount, paid = realized_pnl(
            row, mark=mark, min_notional=min_notional, dust_usdt=dust_usdt
        )
    except IntegrationError:
        pass
    else:
        pnl = format(amount, "f")
        fee = format(paid, "f")
    return f"realized {pnl} fee {fee} status {status}"[:400]


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
        if analyst is not None and hasattr(analyst, "bind_journal"):
            analyst.bind_journal(journal)
        warn_budget_short(cfg.research_seconds, cfg.llm_daily_calls)
        self.guard = GlobalRisk(cfg)
        self.research_status = {
            "state": "ok",
            "error_code": None,
            "last_ok_at": None,
            "last_failed_at": None,
            "next_attempt_at": None,
            "consecutive_failures": 0,
        }
        self.status = CycleStatus()
        self.universe = cfg.symbols
        self.evidence = {}
        self.screener_hits: list = []
        self.screener_error: str | None = None
        self.screener_source = ""
        self.screener_fetched_at = ""
        self.non_warp_future_so: Decimal | None = None
        self.accounts_at = ""
        self.accounts_error: str | None = None
        self.positions_at = ""
        self.positions_error: str | None = None
        self.last_positions: list[dict] = []
        self.position_book: dict | None = None
        self.position_book_error: str | None = None
        self.position_book_due = 0.0
        self.positions_ok_at: str | None = None
        self._book_strategy_error: str | None = None
        self.last_decision = None
        self.last_opportunities: list[dict] = []
        self.last_accounts: list = []
        self.last_aegis: list[dict] = []
        self._trade_book: dict | None = None
        self._trade_book_error: str | None = None
        self._discovery_block: str | None = None
        self._halt_present: bool | None = None
        # The worker flush runs every loop. This is how that loop sees HALT edges.
        self.journal.note_halt = self._note_halt

    def cycle(self) -> int:
        self._note_halt()
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
        self._note_halt()
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
        self._note_halt()
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
            self.screener_fetched_at = getattr(self.screener, "fetched_at", "")
            self.screener_error = None
        except (ScreenerError, ValueError) as exc:
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
            chosen = tuple(
                symbol
                for symbol in self.cfg.symbols
                if symbol not in self.cfg.deny_symbols and not excluded_symbol(symbol)
            )
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
        posts: list[tuple[Signal, dict]] = []
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
            card["evidence_as_of"] = (
                enriched.evidence.as_of.astimezone(timezone.utc).isoformat()
                if enriched.evidence is not None and enriched.evidence.as_of.tzinfo is not None
                else None
            )
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
            posts.append((gated, card))
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
            self._announce_research(posts, None, None, now, planned_as="buy")
            return 0

        prices, price_problem = self._price_map(account, positions, now)
        if price_problem:
            self.status.dependencies_healthy = True
            self.status.execution_permitted = False
            self.status.risk_permitted = False
            self.status.reason = "halt" if paused else price_problem
            self._record(notes)
            log_event("entries_paused", mode=self.cfg.mode, reason=self.status.reason)
            self._announce_research(posts, None, None, now, planned_as="buy")
            return 0

        since = self._shared_since(now)
        day_start, peak = self._shared_anchors(now)
        scope = "sleeve" if self.cfg.shared_account else "account"
        decision = self.guard.assess(
            account,
            positions,
            deduped,
            prices=prices,
            marks=self.journal.equity_marks(scope=scope),
            now=now,
            evidence=self.evidence,
            extra_commitments=self._untracked(positions),
            journaled=self._journaled(positions),
            shared_since=since,
            realized_since=self._realized_since(since),
            day_start=day_start,
            peak=peak,
            dust_marks=self._dust_marks(),
        )
        self.last_decision = decision
        if decision.equity is not None:
            self.journal.record_equity(decision.equity, decision.equity_reliable, now=now, scope=scope)
            if decision.equity_reliable:
                self._remember_shared_equity(decision.equity, now)
        self.status.dependencies_healthy = True
        self.status.risk_permitted = decision.execution_permitted
        self.status.execution_permitted = bool(
            submit and decision.execution_permitted and self.cfg.mode != "observe" and not paused
        )
        self.status.reason = decision.reason
        if decision.reason in {"drawdown", "daily_loss"}:
            enqueue_incident(
                self.journal,
                f"breaker:{decision.reason}",
                decision.reason,
                now=now,
                channel=channel_configured(self.cfg),
            )
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
            self._announce_research(posts, planned, decision.reason, now, planned_as="buy")
            return 0

        self._announce_research(posts, planned, decision.reason, now, planned_as="later")

        opened = 0
        self.last_aegis = []
        symbols = [str(plan.symbol) for plan in decision.orders]
        self._load_trade_book(symbols, now)
        for plan in decision.orders:
            if self.journal.recently_submitted(plan.symbol):
                notes.append((stamp, plan.symbol, "broker", "duplicate_cooldown", "skipped"))
                log_event("duplicate_skipped", symbol=plan.symbol)
                self._queue_spot(posts, plan.symbol, now, state="BUY")
                continue
            if opened >= 1:
                started = False
                for later in decision.orders:
                    if later.symbol == plan.symbol:
                        started = True
                    if started:
                        self._queue_spot(posts, later.symbol, now, state="BUY")
                break
            if self._halted():
                self.status.reason = "halt"
                self.status.execution_permitted = False
                notes.append((stamp, plan.symbol, "broker", "halt", "not_submitted"))
                break
            if self.cfg.mode == "observe":
                notes.append((stamp, plan.symbol, "broker", "observe", "not_submitted"))
                self._queue_spot(posts, plan.symbol, now, state="BUY")
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
                self._queue_spot(posts, plan.symbol, now, state="BUY")
                continue
            if getattr(account, "market", "spot") not in {"", "spot"}:
                notes.append((stamp, plan.symbol, "broker", "spot_only", "not_submitted"))
                self.status.execution_permitted = False
                self._queue_spot(posts, plan.symbol, now, state="BUY")
                continue
            if self._trade_book_error:
                self.last_aegis.append(
                    {"symbol": plan.symbol, "status": "WAIT_DATA_INCOMPLETE", "reasons": [self._trade_book_error], "warnings": []}
                )
                notes.append((stamp, plan.symbol, "broker", "WAIT_DATA_INCOMPLETE", "not_submitted"))
                self.status.execution_permitted = False
                self._queue_spot(posts, plan.symbol, now, state="BLOCKED", why="market_data")
                continue
            book = self._approve(account, plan, now)
            self._record_risk_decision(plan, account, book, now)
            self._remember_aegis(plan.symbol, book, account)
            if book.decision != "APPROVED" or book.ladder is None:
                notes.append((stamp, plan.symbol, "broker", ",".join(book.reasons) or book.decision, "not_submitted"))
                self.status.execution_permitted = False
                self._queue_spot(
                    posts, plan.symbol, now, state="BLOCKED", why=book.reasons[0] if book.reasons else ""
                )
                continue
            self._approved_ladder = book.ladder
            client_id = allocate_client_id(self.journal, plan.symbol, template, now)
            if client_id is None:
                notes.append((stamp, plan.symbol, "broker", "duplicate_client", "skipped"))
                log_event("duplicate_skipped", symbol=plan.symbol)
                self._queue_spot(posts, plan.symbol, now, state="BUY")
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
            enqueue_incident(
                self.journal,
                f"order:{plan.symbol}:{client_id}",
                f"{self.cfg.mode} {template.kind} {plan.symbol} {broker_id}",
                now=now,
                channel=channel_configured(self.cfg),
            )
            notes.append((stamp, plan.symbol, "broker", "confirmed", broker_id))
            log_event("strategy_confirmed", symbol=plan.symbol, strategy_id=broker_id)
            opened += 1
        self.status.opened = opened
        self._record(notes)
        return opened

    def _announce_research(self, posts, planned, reason, now, *, planned_as: str) -> None:
        """One card per research symbol. A halt stays the single global card."""
        if self._halted() or not posts:
            return
        for signal, _card in posts:
            in_plan = planned is not None and signal.symbol in planned
            if signal.action == "BUY" and in_plan and planned_as != "buy":
                continue
            if signal.action == "BUY" and planned is not None and not in_plan:
                self._queue_spot(posts, signal.symbol, now, state="BLOCKED", why=reason or "")
                continue
            self._queue_spot(posts, signal.symbol, now, state=signal.action)

    def _queue_spot(self, posts, symbol: str, now, *, state: str, why: str = "") -> None:
        if self._halted():
            return
        found = next(((signal, card) for signal, card in posts if signal.symbol == symbol), None)
        if found is None:
            return
        signal, card = found
        evidence = signal.evidence
        if state == "BLOCKED":
            text = blocked_card(
                symbol=symbol,
                confidence=signal.confidence,
                why=block_why(why),
                was="BUY",
            )
            label = "BLOCKED"
        else:
            action = state if state in {"BUY", "WAIT", "SKIP"} else "WAIT"
            text = spot_card(
                symbol=symbol,
                action=action,
                confidence=signal.confidence,
                thesis=str(card.get("thesis") or signal.thesis),
                regime=None if evidence is None else evidence.regime,
                rsi=None if evidence is None else evidence.rsi,
                entry=card.get("entry"),
                stop=card.get("stop"),
                take=card.get("take"),
                pair_cap=self.cfg.max_pair if action == "BUY" else None,
                model_action=None if card.get("model_action") is None else str(card.get("model_action")),
                backtest_passed=card.get("backtest_passed") if isinstance(card.get("backtest_passed"), bool) else None,
                evidence_passed=None if evidence is None else evidence.passed,
            )
            label = action
        enqueue_incident(
            self.journal,
            f"signal:{symbol}:{label}",
            text,
            now=now,
            channel=channel_configured(self.cfg),
        )

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
                "commitment": format(
                    self._approved_ladder.commitment if getattr(self, "_approved_ladder", None) else plan.full_commitment,
                    "f",
                ),
                "created_at": stamp,
                "updated_at": stamp,
            }
        )
        approved = getattr(self, "_approved_ladder", None)
        if approved is not None:
            self.journal.update_warp_bot(
                warp_id,
                ladder=json.dumps(
                    {
                        "commitment": format(approved.commitment, "f"),
                        "stop_loss": format(approved.stop_loss, "f"),
                        "stop_price": format(approved.stop_price, "f"),
                        "step": format(approved.step, "f"),
                        "safety_orders": approved.safety_orders,
                        "scale": format(approved.scale, "f"),
                    }
                ),
                updated_at=stamp,
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
        if isinstance(result, dict) and not readback_ok(result, exits, getattr(self, "_approved_ladder", None)):
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
            raise IntegrationError("3Commas read-back does not match the approved order", uncertain=True)
        return result

    def _journaled(self, positions) -> frozenset[str] | None:
        """Live WARP client ids the journal knows. Only shared mode uses this."""
        if not self.cfg.shared_account:
            return None
        return frozenset(
            str(row["clientId"])
            for row in positions
            if is_warp_strategy(row)
            and self.journal.submission_state(str(row["clientId"])) in {"confirmed", "unknown"}
        )

    def _shared_since(self, now: datetime) -> datetime | None:
        """Start of the sleeve-equity series. Whole-account marks before it are never compared."""
        key = "shared_account_since"
        raw = self.journal.get_setting(key)
        if not self.cfg.shared_account:
            # Kept on purpose. Turning the flag off must not drop the peak or the realized ledger.
            return None
        if not raw:
            self.journal.set_setting(key, now.astimezone(timezone.utc).isoformat())
            return now
        try:
            since = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise IntegrationError("Shared-account equity baseline unreadable; entries halted") from exc
        if since.tzinfo is None:
            raise IntegrationError("Shared-account equity baseline unreadable; entries halted")
        return since

    def _shared_anchors(self, now: datetime) -> tuple[Decimal | None, tuple[datetime, Decimal] | None]:
        """Persisted day-start equity and high-water mark. Corrupt values halt; nothing is reset."""
        if not self.cfg.shared_account:
            return None, None
        day_start = None
        raw_day = self.journal.get_setting("shared_day_start")
        if raw_day:
            try:
                day, amount = raw_day.split("|", 1)
                value = Decimal(amount)
                if not value.is_finite() or value <= 0:
                    raise ValueError
                if day == now.astimezone(timezone.utc).date().isoformat():
                    day_start = value
            except (ValueError, InvalidOperation, ArithmeticError) as exc:
                raise IntegrationError("Shared-account day-start equity unreadable; entries halted") from exc
        peak = None
        raw_peak = self.journal.get_setting("shared_equity_peak")
        if raw_peak:
            try:
                stamp, amount = raw_peak.split("|", 1)
                moment = datetime.fromisoformat(stamp)
                value = Decimal(amount)
                if moment.tzinfo is None or not value.is_finite() or value <= 0:
                    raise ValueError
                peak = (moment, value)
            except (ValueError, InvalidOperation, ArithmeticError) as exc:
                raise IntegrationError("Shared-account equity peak unreadable; entries halted") from exc
        return day_start, peak

    def _remember_shared_equity(self, equity: Decimal, now: datetime) -> None:
        if not self.cfg.shared_account or not equity.is_finite() or equity <= 0:
            return
        moment = now.astimezone(timezone.utc)
        day_key = "shared_day_start"
        stored = self.journal.get_setting(day_key) or ""
        if not stored.startswith(moment.date().isoformat() + "|"):
            self.journal.set_setting(day_key, moment.date().isoformat() + "|" + format(equity, "f"))
        _day_start, peak = self._shared_anchors(moment)
        if peak is None or equity > peak[1]:
            self.journal.set_setting("shared_equity_peak", moment.isoformat() + "|" + format(equity, "f"))

    def _close_events(self) -> list[dict]:
        events = []
        for row in self._realized_rows():
            amount = row["realized_usdt"]
            if amount < 0:
                kind = "stop"
            elif amount > 0:
                kind = "tp"
            else:
                continue
            events.append({"at": row["closed_at"], "kind": kind, "symbol": row["symbol"]})
        return events

    def _realized_rows(self) -> list[dict]:
        try:
            return self.journal.realized_pnl()
        except ValueError as exc:
            raise IntegrationError("Realized P&L unreadable; entries halted") from exc

    def _realized_since(self, since: datetime | None) -> Decimal:
        if not self.cfg.shared_account:
            return Decimal("0")
        if since is None:
            raise IntegrationError("Shared-account equity baseline missing; entries halted")
        retired = {"closed", "canceled", "cancelled", "panic_exited", "failed", "unlinked", "deleted"}
        rows = self._realized_rows()
        booked = {row["client_id"] for row in rows}
        for bot in self.journal.warp_bots():
            client_id = str(bot.get("client_id") or "")
            if bot.get("status") in retired and client_id and client_id not in booked:
                raise IntegrationError("Retired WARP strategy has no realized P&L; entries halted")
        return sum((row["realized_usdt"] for row in rows if row["closed_at"] >= since), Decimal("0"))

    def _book_realized(self, bot: dict, row: dict) -> None:
        client_id = str(bot.get("client_id") or "")
        if not client_id:
            raise IntegrationError("Realized P&L unreadable; entries halted")
        symbol = str(row.get("pair") or bot.get("symbol") or "")
        mark, notional = self._mark_for_dust(symbol)
        amount, fee = realized_pnl(
            row, mark=mark, min_notional=notional, dust_usdt=self.cfg.dust_usdt
        )
        try:
            self.journal.record_realized(client_id, symbol, datetime.now(timezone.utc), amount, fee)
        except ValueError as exc:
            raise IntegrationError("Realized P&L unreadable; entries halted") from exc

    def _mark_for_dust(self, symbol: str) -> tuple[Decimal | None, Decimal | None]:
        """Fresh public mark and minNotional from the cached scan. No network call."""
        item = self.evidence.get(symbol)
        if item is None or item.as_of.tzinfo is None:
            return None, None
        age = (datetime.now(timezone.utc) - item.as_of.astimezone(timezone.utc)).total_seconds()
        if age < -60 or age > self.cfg.max_account_age:
            return None, None
        if not item.last_price.is_finite() or item.last_price <= 0:
            return None, None
        notional = item.min_notional if item.min_notional is not None and item.min_notional > 0 else None
        return item.last_price, notional

    def _dust_marks(self) -> dict[str, tuple[Decimal, Decimal]]:
        marks: dict[str, tuple[Decimal, Decimal]] = {}
        for symbol in self.evidence:
            mark, notional = self._mark_for_dust(symbol)
            if mark is not None and notional is not None:
                marks[symbol] = (mark, notional)
        return marks

    def _loss_context(self) -> dict:
        """Trade-level halts see the sleeve. Strict mode passes nothing, so approve() is unchanged."""
        if not self.cfg.shared_account:
            return {}
        now = datetime.now(timezone.utc)
        day_start, peak = self._shared_anchors(now)
        decision = self.last_decision
        equity = decision.equity if decision is not None and decision.equity_reliable else None
        return {
            "events": self._close_events(),
            "day_start": day_start,
            "peak": None if peak is None else peak[1],
            "equity": equity,
        }

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
        shared = self.cfg.shared_account
        for row in positions:
            if not isinstance(row, dict):
                continue
            if shared and not is_warp_strategy(row):
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
        if account is not None and not shared:
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
                if self.cfg.shared_account:
                    self._book_realized(bot, row)
                self.journal.update_warp_bot(
                    bot["warp_id"],
                    status="closed",
                    updated_at=datetime.now(timezone.utc).isoformat(),
                )
                symbol = str(bot.get("symbol") or "")
                client_id = str(bot.get("client_id") or "")
                dust_symbol = str(row.get("pair") or bot.get("symbol") or "")
                mark, notional = self._mark_for_dust(dust_symbol)
                enqueue_incident(
                    self.journal,
                    f"deal_closed:{symbol}:{client_id}",
                    _deal_closed_detail(
                        row, mark=mark, min_notional=notional, dust_usdt=self.cfg.dust_usdt
                    ),
                    now=datetime.now(timezone.utc),
                    channel=channel_configured(self.cfg),
                )

    def _note_halt(self) -> None:
        """Compare the HALT file with the previous worker observation."""
        paused = self._halted()
        previous = self._halt_present
        self._halt_present = paused
        if previous == paused or (previous is None and not paused):
            return
        enqueue_incident(
            self.journal,
            "halt_on" if paused else "halt_off",
            halt_on_card() if paused else halt_off_card(),
            now=datetime.now(timezone.utc),
            channel=channel_configured(self.cfg),
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
        if evidence is None or evidence.min_qty is None or evidence.step_size is None:
            from .capital import Decision

            self._last_exposure = None
            return Decision("WAIT_DATA_INCOMPLETE", ["lot_size"])
        bots = [row for row in self.journal.warp_bots(account.code) if row.get("status") in {"pending", "unknown", "confirmed", "active", "paused"}]
        prices = {symbol: item.last_price for symbol, item in self.evidence.items()}
        exposure = self._warp_exposure(bots, prices)
        if exposure is None:
            from .capital import Decision

            self._last_exposure = None
            return Decision("WAIT_DATA_INCOMPLETE", ["open_risk"])
        budget = plan.budget if plan.budget > 0 else plan.full_commitment
        self._last_exposure = exposure
        return approve(
            policy=policy,
            account_usdt=account.total_usdt,
            free_usdt=account.free_usdt,
            warp_owned=exposure["owned"],
            warp_committed=exposure["committed"],
            warp_pending_so=exposure["pending"],
            non_warp_spent=account.used_usdt,
            non_warp_future_so=self._foreign_future_so(),
            open_deals=len(bots),
            open_stop_risk=exposure["stop"],
            open_symbols=[str(row.get("symbol")) for row in bots],
            price=evidence.last_price,
            minimum=evidence.min_notional,
            model_buy=True,
            score=score,
            min_score=self._template().min_score,
            exits=_exits(self._template()),
            max_commitment=budget,
            max_base=self.cfg.max_base,
            symbol=plan.symbol,
            now=now,
            tape=tape,
            corr=corr,
            quote_volume=gate["quote_volume"] if gate else evidence.quote_volume,
            spread=None if gate is None else gate.get("spread"),
            change_24h=None if gate is None else gate.get("change_24h"),
            listed_days=None if gate is None else gate.get("listed_days"),
            require_listing=gate is not None,
            tradable=True if gate is None else bool(gate.get("tradable")),
            on_binance_spot=True if gate is None else bool(gate.get("on_binance_spot")),
            atr_1h_pct=None if gate is None else gate.get("atr_1h_pct"),
            stale=self._entry_stale(evidence, gate, now),
            mismatched_account=self._account_mismatch(account),
            min_qty=evidence.min_qty,
            step_size=evidence.step_size,
            open_book=exposure["book"],
            **self._loss_context(),
        )

    def _entry_stale(self, evidence, gate, now: datetime) -> bool:
        """Entry signal stays within WARP_MAX_ENTRY_SIGNAL_SECONDS. The live quote stays within 30s."""
        if evidence is None or evidence.as_of.tzinfo is None:
            return True
        age = (now - evidence.as_of.astimezone(timezone.utc)).total_seconds()
        if age < -60 or age > self.cfg.max_entry_signal_seconds:
            return True
        if gate is None:
            return age > 30
        return gate.get("quote_age") is None or gate["quote_age"] > 30

    def _account_mismatch(self, account) -> bool:
        exchange = (account.exchange_code or "BINANCE").upper()
        if account.market not in {"", "spot"} or exchange != "BINANCE":
            return True
        target = self._target()
        if not target:
            return False
        if target.get("code") and str(target["code"]) != account.code:
            return True
        if target.get("exchange_code") and str(target["exchange_code"]).upper() != exchange:
            return True
        if target.get("market") and str(target["market"]) != "spot":
            return True
        return False

    def _warp_exposure(self, bots: list[dict], prices: dict) -> dict | None:
        """Open WARP risk from the stored ladder and the live row. Missing data is None, not zero."""
        if not bots:
            zero = Decimal("0")
            return {"stop": zero, "pending": zero, "owned": zero, "committed": zero, "book": ()}
        by_client = {
            str(row.get("clientId")): row
            for row in self.last_positions
            if isinstance(row, dict) and row.get("clientId")
        }
        stop = pending = owned = committed = Decimal("0")
        book: list[tuple[Decimal, Decimal]] = []
        for bot in bots:
            raw = bot.get("ladder")
            if not raw:
                return None
            try:
                stored = json.loads(raw)
                stored_stop = Decimal(str(stored["stop_loss"]))
                stored_commit = Decimal(str(stored["commitment"]))
                stop_price = Decimal(str(stored["stop_price"]))
            except (KeyError, TypeError, ValueError, ArithmeticError):
                return None
            row = by_client.get(str(bot.get("client_id") or ""))
            if row is None:
                return None
            try:
                pending += unfilled_quote(row, self.cfg.fee_rate)
                qty = net_base_qty(row)
                base = pair_symbols(row).get("base")
            except (IntegrationError, ValueError):
                return None
            deal_stop = stored_stop
            if qty > 0:
                price = prices.get(f"{base}USDT") if base else None
                if price is None or price <= 0:
                    return None
                owned += qty * price
                if row.get("totalEntryCost") is None or row.get("totalEntryAmount") in (None, "", "0", 0):
                    return None
                try:
                    filled_cost = positive(row["totalEntryCost"], "entry cost")
                    fill = stop_price * (Decimal("1") - self.cfg.stop_slippage)
                except (ValueError, ArithmeticError):
                    return None
                if fill <= 0:
                    return None
                deal_stop = max(stored_stop, max(Decimal("0"), filled_cost - qty * fill))
            stop += deal_stop
            committed += stored_commit
            book.append((stored_commit, deal_stop))
        return {"stop": stop, "pending": pending, "owned": owned, "committed": committed, "book": tuple(book)}

    def _record_risk_decision(self, plan, account, book, now: datetime) -> None:
        evidence = self.evidence.get(plan.symbol)
        as_of = "none"
        if evidence is not None and evidence.as_of.tzinfo is not None:
            as_of = evidence.as_of.astimezone(timezone.utc).isoformat()
        signal_id = f"{plan.symbol}|{as_of}|{self.screener_fetched_at or 'engine'}"
        fetched = account.fetched_at.isoformat() if account.fetched_at.tzinfo is not None else "naive"
        digest = hashlib.sha256(
            f"{account.code}|{account.free_usdt}|{account.used_usdt}|{fetched}".encode()
        ).hexdigest()
        ladder = book.ladder
        nav = book.nav if book.nav else Decimal("0")
        stop = ladder.stop_loss if ladder is not None else Decimal("0")
        prior = getattr(self, "_last_exposure", None)
        open_after = stop + (prior["stop"] if prior else Decimal("0"))
        self.journal.record_risk_decision(
            {
                "recorded_at": now.astimezone(timezone.utc).isoformat(),
                "signal_id": signal_id,
                "risk_decision_id": secrets.token_hex(8),
                "account_hash": digest,
                "nav": format(nav, "f"),
                "free_usdt": format(account.free_usdt, "f"),
                "reserved_so": "0" if ladder is None else format(max(Decimal("0"), ladder.commitment - ladder.base), "f"),
                "commitment": "0" if ladder is None else format(ladder.commitment, "f"),
                "ladder": "" if ladder is None else format(ladder.commitment, "f"),
                "stop_usdt": format(stop, "f"),
                "stop_pct": "0" if nav <= 0 else format(stop / nav, "f"),
                "open_stop_after": format(open_after, "f"),
                "decision": book.decision,
                "reasons": json.dumps(list(book.reasons)),
            },
            keep=self.cfg.risk_decisions_keep,
        )

    def _read_non_warp_future_so(self) -> Decimal | None:
        """Read-only reserve for the sleeve display. None means the read failed."""
        if not self.cfg.api_key or not self.cfg.api_secret:
            return Decimal("0")
        if not hasattr(self.broker, "list_dca_bots"):
            return None
        try:
            rows = self.broker.list_dca_bots()
        except IntegrationError:
            return None
        if not isinstance(rows, list):
            return None
        total = Decimal("0")
        for row in rows:
            if not isinstance(row, dict) or str(row.get("name") or "").startswith("WARP-"):
                continue
            text = str(row.get("maxRequiredAmount") or "")
            number = "".join(ch for ch in text if ch.isdigit() or ch == ".")
            if number:
                total += Decimal(number)
        return total

    def _foreign_future_so(self) -> Decimal:
        if not hasattr(self.broker, "list_dca_bots"):
            return Decimal("0")
        try:
            rows = self.broker.list_dca_bots()
        except IntegrationError:
            if self.cfg.shared_account:
                raise
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

    def apply_account_list(self, listed) -> None:
        self.last_accounts = list(listed)
        self.accounts_at = datetime.now(timezone.utc).isoformat()
        self.accounts_error = getattr(listed, "error", None)

    def apply_account_error(self, exc: IntegrationError) -> None:
        self.accounts_error = str(exc)[:200]
        self.accounts_at = datetime.now(timezone.utc).isoformat()

    def _remember_accounts(self) -> None:
        if not self.cfg.api_key or not self.cfg.api_secret or not hasattr(self.broker, "list_accounts"):
            self.accounts_error = "3Commas credentials are not configured"
            self.accounts_at = datetime.now(timezone.utc).isoformat()
            return
        try:
            self.apply_account_list(self.broker.list_accounts())
        except IntegrationError as exc:
            self.apply_account_error(exc)

    def refresh_readonly(self) -> None:
        """GET account profiles and live strategies. No create, update, or delete."""
        self._remember_accounts()
        if not self.cfg.api_key or not self.cfg.api_secret or not hasattr(self.broker, "get_live"):
            self.positions_error = "3Commas credentials are not configured"
            self.positions_at = datetime.now(timezone.utc).isoformat()
            self.position_book_error = "3Commas credentials are not configured"
            return
        try:
            self.last_positions = self.broker.get_live()
            self.positions_at = datetime.now(timezone.utc).isoformat()
            self.positions_ok_at = self.positions_at
            self.positions_error = None
        except IntegrationError as exc:
            self.positions_error = str(exc)[:200]
            self.positions_at = datetime.now(timezone.utc).isoformat()
        self.non_warp_future_so = self._read_non_warp_future_so()
        # A change in the SmartTrade read state rebuilds the book now, so a failure is not hidden for a cycle.
        self.refresh_position_book(force=self.positions_error != self._book_strategy_error)

    def refresh_position_book(self, *, force: bool = False) -> None:
        """GET open positions for every bot on each account, at most once per POSITIONS_SECONDS."""
        from .book import POSITIONS_SECONDS, collect

        now = time.monotonic()
        if not force and now < self.position_book_due:
            return
        self.position_book_due = now + POSITIONS_SECONDS
        if not hasattr(self.broker, "get_bot_positions"):
            self.position_book_error = "positions_unsupported"
            return

        def is_warp(client_id: str) -> bool:
            try:
                return self.journal.submission_state(client_id) is not None
            except Exception:
                return False

        strategy_error = self.positions_error
        self._book_strategy_error = strategy_error
        try:
            self.position_book = collect(
                self.broker,
                strategies=self.last_positions,
                warp_client=is_warp,
                owned_bots=self.journal.warp_bots(),
                strategies_as_of=self.positions_ok_at,
                strategy_error=strategy_error,
            )
            self.position_book_error = "strategies_stale" if strategy_error else None
        except (IntegrationError, sqlite3.Error) as exc:
            self.position_book_error = str(exc)[:200]

    def _evaluate(self, now: datetime) -> None:
        price_at = getattr(self.market, "price_at", None)
        if price_at is None:
            return
        evaluate_due(self.journal, price_at, now, limit=20)
