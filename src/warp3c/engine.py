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

    def cycle(self) -> int:
        self.status = CycleStatus()
        if self._halted():
            self.status.reason = "halt"
            self.status.dependencies_healthy = True
            log_event("halt", mode=self.cfg.mode)
            return 0
        try:
            opened = self._cycle()
        except (IntegrationError, MarketDataError, ResearchError) as exc:
            self.status.reason = type(exc).__name__
            self.status.dependencies_healthy = False
            self.status.execution_permitted = False
            log_event("cycle_blocked", mode=self.cfg.mode, error=type(exc).__name__)
            raise
        self.journal.heartbeat(
            {
                "mode": self.cfg.mode,
                "reason": self.status.reason,
                "execution_permitted": self.status.execution_permitted,
                "market_data_fresh": self.status.market_data_fresh,
            }
        )
        return opened

    def _cycle(self) -> int:
        now = datetime.now(timezone.utc)
        account = None
        positions: list[dict] = []
        if self.cfg.api_key and self.cfg.api_secret and self.cfg.profile_code:
            account = self.broker.get_account(self.cfg.profile_code, self.cfg.exchange_code)
            self._require_mode_matches(account.demo)
            positions = self.broker.get_live()
            self._reconcile(positions)
        elif self.cfg.mode != "observe":
            raise IntegrationError("Trading requires configured credentials", uncertain=False)

        evidence = self.market.scan(self.cfg.symbols, now=now)
        self.status.market_data_fresh = all(
            (now - item.as_of.astimezone(timezone.utc)).total_seconds() <= self.cfg.max_signal_age * 60
            for item in evidence.values()
        )
        signals = [replace(signal, evidence=evidence.get(signal.symbol)) for signal in self._research()]
        prices = {symbol: item.last_price for symbol, item in evidence.items()}

        if account is None:
            self.status.dependencies_healthy = True
            self.status.execution_permitted = False
            self.status.reason = "observe_without_account"
            log_event("observe", signals=len(signals), fresh=self.status.market_data_fresh)
            return 0

        decision = self.guard.assess(
            account, positions, signals, prices=prices, marks=self.journal.equity_marks(), now=now
        )
        if decision.equity is not None:
            self.journal.record_equity(decision.equity, decision.equity_reliable, now=now)
        self.status.dependencies_healthy = True
        self.status.execution_permitted = decision.execution_permitted and self.cfg.mode != "observe"
        self.status.reason = decision.reason
        if not decision.execution_permitted:
            log_event("entries_paused", mode=self.cfg.mode, reason=decision.reason)
            return 0

        opened = 0
        for plan in decision.orders:
            if self.journal.recently_submitted(plan.symbol):
                log_event("duplicate_skipped", symbol=plan.symbol)
                continue
            if opened >= 1:
                break
            if self._halted():
                self.status.reason = "halt"
                self.status.execution_permitted = False
                break
            if self.cfg.mode == "observe":
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
                if not exc.uncertain:
                    self.journal.reject(client_id)
                raise
            self.journal.confirm(client_id, str(result["strategyId"]))
            log_event("strategy_confirmed", symbol=plan.symbol, strategy_id=str(result["strategyId"]))
            opened += 1
        self.status.opened = opened
        return opened

    def _research(self) -> list[Signal]:
        return self.research.research(self.cfg.symbols, self.cfg.max_signal_age)

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
