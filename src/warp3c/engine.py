"""Single-writer cycle. A model signal is not an order."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings
from .market import MarketData, MarketDataError
from .models import Signal
from .risk import GlobalRisk, make_trade_payload
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


class Engine:
    def __init__(
        self,
        cfg: Settings,
        broker: ThreeCommas,
        research: VibeResearch,
        journal: OrderJournal,
        market: MarketData,
    ):
        self.cfg = cfg
        self.broker = broker
        self.research = research
        self.journal = journal
        self.market = market
        self.guard = GlobalRisk(cfg)
        self.status = CycleStatus()
        self.universe = cfg.symbols
        self.evidence = {}
        self.last_positions: list[dict] = []
        self.last_decision = None

    def cycle(self) -> int:
        self.status = CycleStatus()
        if self._halted():
            self._mark_halt()
            return 0
        try:
            now = datetime.now(timezone.utc)
            account, positions = self._load_account()
            self.refresh_market(now)
            signals = self._research()
            opened = self._submit(account, positions, signals, now, submit=True)
        except (IntegrationError, MarketDataError, ResearchError) as exc:
            self._mark_blocked(exc)
            raise
        self._heartbeat()
        return opened

    def monitor(self) -> int:
        """Reconcile and re-score risk. Does not call research and does not submit."""
        self.status = CycleStatus()
        if self._halted():
            self._mark_halt()
            return 0
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
        if self._halted():
            self._mark_halt()
            return 0
        try:
            account, positions = self._load_account()
            opened = self._submit(account, positions, signals, moment, submit=True)
        except (IntegrationError, MarketDataError, ResearchError) as exc:
            self._mark_blocked(exc)
            raise
        self._heartbeat()
        return opened

    def refresh_market(self, now: datetime) -> None:
        self.universe = self._select_universe(now)
        self.evidence = self.market.scan(self.universe, now=now) if self.universe else {}
        self._mark_fresh(now)

    def collect_research(self) -> list[Signal]:
        """Called from the research thread. Must not touch the broker or the journal."""
        return self._research()

    def _select_universe(self, now: datetime) -> tuple[str, ...]:
        if self.cfg.discovery != "auto":
            return self.cfg.symbols
        try:
            selected, rejected = self.market.discover(now=now)
        except MarketDataError:
            self._record(
                [(now.isoformat(), "*", "universe", "discovery_fallback_allowlist", "market_data")]
            )
            return self.cfg.symbols
        self._record(
            [(now.isoformat(), symbol, "universe", reason, "rejected") for symbol, reason in rejected]
        )
        if not selected:
            self._record([(now.isoformat(), "*", "universe", "no_eligible_market", "fallback_allowlist")])
            return self.cfg.symbols
        return selected

    def _research(self) -> list[Signal]:
        if not self.universe:
            return []
        return self.research.research(self.universe, self.cfg.max_signal_age)

    def _submit(self, account, positions, signals, now, *, submit: bool) -> int:
        deduped: list[Signal] = []
        seen: set[str] = set()
        notes: list[tuple[str, str, str, str, str]] = []
        stamp = now.astimezone(timezone.utc).isoformat()
        for signal in signals:
            if signal.symbol in seen:
                notes.append((stamp, signal.symbol, "research", "duplicate", "rejected"))
                continue
            seen.add(signal.symbol)
            deduped.append(replace(signal, evidence=self.evidence.get(signal.symbol)))
            notes.append((stamp, signal.symbol, "research", signal.action, "accepted"))
        for symbol, item in self.evidence.items():
            notes.append((stamp, symbol, "market", item.reason, "passed" if item.passed else "rejected"))
        self._mark_fresh(now)
        if account is None:
            self.status.dependencies_healthy = True
            self.status.execution_permitted = False
            self.status.risk_permitted = False
            self.status.reason = "observe_without_account"
            self._record(notes)
            log_event("observe", signals=len(deduped), fresh=self.status.market_data_fresh)
            return 0

        decision = self.guard.assess(
            account,
            positions,
            deduped,
            prices={symbol: item.last_price for symbol, item in self.evidence.items()},
            marks=self.journal.equity_marks(),
            now=now,
            evidence=self.evidence,
        )
        self.last_decision = decision
        if decision.equity is not None:
            self.journal.record_equity(decision.equity, decision.equity_reliable, now=now)
        self.status.dependencies_healthy = True
        self.status.risk_permitted = decision.execution_permitted
        self.status.execution_permitted = bool(submit and decision.execution_permitted and self.cfg.mode != "observe")
        self.status.reason = decision.reason
        planned = {plan.symbol for plan in decision.orders}
        for signal in deduped:
            if signal.symbol in planned:
                notes.append((stamp, signal.symbol, "risk", decision.reason, "planned"))
            elif signal.action == "BUY":
                notes.append((stamp, signal.symbol, "risk", decision.reason, "rejected"))
        if not decision.execution_permitted or not submit:
            self._record(notes)
            if not decision.execution_permitted:
                log_event("entries_paused", mode=self.cfg.mode, reason=decision.reason)
            return 0

        opened = 0
        for plan in decision.orders:
            if self.journal.recently_submitted(plan.symbol):
                notes.append((stamp, plan.symbol, "broker", "duplicate_cooldown", "skipped"))
                log_event("duplicate_skipped", symbol=plan.symbol)
                continue
            if opened >= 1:
                break
            if self._halted():
                self._mark_halt()
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
            client_id = "warp3c-" + uuid.uuid4().hex
            payload = make_trade_payload(self.cfg, plan, client_id)
            self.journal.reserve(client_id, plan.symbol, payload)
            try:
                result = self.broker.create_strategy(payload)
            except IntegrationError as exc:
                notes.append((stamp, plan.symbol, "broker", "rejected" if not exc.uncertain else "unknown", "halted"))
                self._record(notes)
                if not exc.uncertain:
                    self.journal.reject(client_id)
                raise
            self.journal.confirm(client_id, str(result["strategyId"]))
            notes.append((stamp, plan.symbol, "broker", "confirmed", str(result["strategyId"])))
            log_event("strategy_confirmed", symbol=plan.symbol, strategy_id=str(result["strategyId"]))
            opened += 1
        self.status.opened = opened
        self._record(notes)
        return opened

    def _load_account(self):
        if self.cfg.api_key and self.cfg.api_secret and self.cfg.profile_code:
            account = self.broker.get_account(self.cfg.profile_code, self.cfg.exchange_code)
            self._require_mode_matches(account.demo)
            positions = self.broker.get_live()
            self.last_positions = positions
            self._reconcile(positions)
            return account, positions
        if self.cfg.mode != "observe":
            raise IntegrationError("Trading requires configured credentials", uncertain=False)
        self.last_positions = []
        return None, []

    def _fresh(self, now: datetime) -> bool:
        if not self.evidence:
            return False
        limit = self.cfg.max_signal_age * 60
        return all(
            (now - item.as_of.astimezone(timezone.utc)).total_seconds() <= limit for item in self.evidence.values()
        )

    def _mark_fresh(self, now: datetime) -> None:
        self.status.market_data_fresh = self._fresh(now)

    def _mark_halt(self) -> None:
        self.status.reason = "halt"
        self.status.dependencies_healthy = True
        self.status.execution_permitted = False
        self.status.risk_permitted = False
        log_event("halt", mode=self.cfg.mode)

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

    def _halted(self) -> bool:
        return Path(self.cfg.db_path).parent.joinpath("HALT").exists()
