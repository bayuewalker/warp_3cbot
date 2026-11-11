"""Single-writer orchestration and global risk gates."""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

from .config import Settings
from .risk import GlobalRisk, make_trade_payload, managed_strategies
from .state import OrderJournal
from .threecommas import IntegrationError, ThreeCommas
from .vibe import VibeResearch

LOG = logging.getLogger("warp3c.engine")


class Engine:
    def __init__(self, cfg: Settings, broker: ThreeCommas, research: VibeResearch, journal: OrderJournal):
        self.cfg = cfg
        self.broker = broker
        self.research = research
        self.journal = journal
        self.guard = GlobalRisk(cfg)

    def cycle(self) -> int:
        if Path(self.cfg.db_path).parent.joinpath("HALT").exists():
            LOG.warning("HALT file present: refusing all new entries")
            return 0
        if not all((self.cfg.api_key, self.cfg.api_secret, self.cfg.profile_code)):
            if self.cfg.mode == "observe":
                LOG.info("Observe mode waiting for read-only 3Commas credentials")
                return 0
            raise IntegrationError("Trading requires configured credentials")
        account = self.broker.get_account(self.cfg.profile_code, self.cfg.exchange_code)
        if self.cfg.mode == "paper" and not account.demo:
            raise IntegrationError("Paper mode refuses a real-money 3Commas profile")
        if self.cfg.mode == "live" and account.demo:
            raise IntegrationError("Live mode refuses a demo 3Commas profile")
        live = self.broker.get_live()
        unresolved = self.journal.reconcile(live)
        if unresolved:
            raise IntegrationError(f"{len(unresolved)} trade submissions unreconciled: entries halted")
        active = managed_strategies(live, account.code)
        # This startup check must pass before asking the AI model for candidate research.
        if any(p.get("exchange", {}).get("code") != self.cfg.exchange_code for p in active):
            raise IntegrationError("Mixed exchange profile: cannot size safely")
        signals = self.research.research(self.cfg.symbols, self.cfg.max_signal_age)
        plans = self.guard.plan(account, live, signals)
        opened = 0
        for plan in plans:
            if self.journal.recently_submitted(plan.symbol):
                LOG.info("Skip recent strategy %s (24h duplicate cooldown)", plan.symbol)
                continue
            if opened >= 1:  # deliberate controlled rollout: max one new DCA per cycle
                break
            if self.cfg.mode == "observe":
                LOG.info("OBSERVE %s base=%s full_commitment=%s confidence=%s",
                         plan.symbol, plan.base_amount, plan.full_commitment, plan.confidence)
                opened += 1
                continue
            if Path(self.cfg.db_path).parent.joinpath("HALT").exists():
                break
            client_id = "warp3c-" + uuid.uuid4().hex
            payload = make_trade_payload(self.cfg, plan, client_id)
            self.journal.reserve(client_id, plan.symbol, payload)
            # A failure or timeout leaves an unknown journal item. No automatic retries.
            result = self.broker.create_strategy(payload)
            self.journal.confirm(client_id, str(result["strategyId"]))
            LOG.info("Confirmed %s client=%s strategy=%s", plan.symbol, client_id, result["strategyId"])
            opened += 1
        return opened
