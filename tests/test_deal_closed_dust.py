"""deal_closed reports realized P&L when the only residual is allowed dust."""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from test_core import evidence

from warp3c.config import Settings
from warp3c.engine import Engine, _deal_closed_detail
from warp3c.models import MarketEvidence
from warp3c.state import OrderJournal


def _row(*, entry_qty="1", exit_qty="1", entry="100", exit_cost="99.70", fee="0.10"):
    return {
        "status": "completed",
        "pair": "SOLUSDT",
        "totalEntryCost": entry,
        "totalExitCost": exit_cost,
        "totalEntryAmount": entry_qty,
        "totalExitAmount": exit_qty,
        "totalEntryCommissionCost": fee,
        "totalExitCommissionCost": fee,
    }


class DealClosedDustTests(unittest.TestCase):
    def test_dust_below_the_threshold_with_a_fresh_mark_is_numeric(self):
        # residual 0.01 * mark 10 = 0.10, under min(min_notional 5, dust 1)
        detail = _deal_closed_detail(
            _row(exit_qty="0.99"),
            mark=Decimal("10"),
            min_notional=Decimal("5"),
            dust_usdt=Decimal("1"),
        )
        self.assertEqual(detail, "realized -0.60 fee 0.20 status completed")
        self.assertNotIn("unknown", detail)

    def test_residual_at_or_above_the_threshold_stays_unknown(self):
        # 0.01 * 100 = 1.00, which is not under min(5, 1)
        at = _deal_closed_detail(
            _row(exit_qty="0.99"),
            mark=Decimal("100"),
            min_notional=Decimal("5"),
            dust_usdt=Decimal("1"),
        )
        above = _deal_closed_detail(
            _row(exit_qty="0.90"),
            mark=Decimal("100"),
            min_notional=Decimal("10"),
            dust_usdt=Decimal("1"),
        )
        self.assertEqual(at, "realized unknown fee unknown status completed")
        self.assertEqual(above, "realized unknown fee unknown status completed")

    def test_no_mark_stays_unknown(self):
        detail = _deal_closed_detail(
            _row(exit_qty="0.997"),
            mark=None,
            min_notional=Decimal("5"),
            dust_usdt=Decimal("1"),
        )
        self.assertEqual(detail, "realized unknown fee unknown status completed")

    def test_no_residual_stays_numeric(self):
        detail = _deal_closed_detail(_row(exit_qty="1", exit_cost="102"))
        self.assertEqual(detail, "realized 1.80 fee 0.20 status completed")

    def test_retire_path_passes_the_fresh_dust_mark(self):
        client_id = "warp3c-dustalert"
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(db_path=os.path.join(tmp, "warp.db"), dust_usdt=Decimal("1"))
            journal = OrderJournal(cfg.db_path)
            engine = Engine(cfg, None, None, journal, None)
            fresh = evidence("SOLUSDT")
            engine.evidence = {
                "SOLUSDT": MarketEvidence(
                    fresh.symbol, fresh.as_of, Decimal("10"), fresh.quote_volume,
                    fresh.trend, fresh.momentum, fresh.rsi, fresh.volatility, fresh.regime,
                    fresh.min_notional, fresh.quote_precision, fresh.tradable, fresh.passed,
                    fresh.reason,
                )
            }
            stamp = "2026-11-26T00:00:00+00:00"
            journal.insert_warp_bot({
                "warp_id": "dust1",
                "name": "WARP-dust1",
                "client_id": client_id,
                "external_code": "strat-dust",
                "kind": "dca",
                "product": "strategy",
                "account_code": "ACCT",
                "market": "spot",
                "symbol": "SOLUSDT",
                "status": "confirmed",
                "commitment": "10",
                "created_at": stamp,
                "updated_at": stamp,
            })
            row = _row(exit_qty="0.99")
            row.update({"clientId": client_id, "strategyId": "strat-dust", "pair": "SOLUSDT"})
            engine._retire_finished([row])
            due = journal.due_alerts(datetime.now(timezone.utc) + timedelta(seconds=1))
            self.assertEqual(due[0][1], f"deal_closed:SOLUSDT:{client_id}")
            self.assertEqual(due[0][2], "realized -0.60 fee 0.20 status completed")
            journal.close()


if __name__ == "__main__":
    unittest.main()
