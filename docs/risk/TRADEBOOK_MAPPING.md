# Tradebook v1.0 mapping

Source: Grokbot Dynamic Spot DCA Tradebook v1.0 (11 November 2026), the text embedded with the PR #33 audit. Section numbers below are that document. This file records what the code enforces. It does not authorize live trading.

`go_no_go` in `src/warp3c/capital.py` is still not a hard gate.

## Rule → env → default → code

| Rule | Env var | Default | Code |
| --- | --- | --- | --- |
| Eligible NAV E. Shared mode: sleeve + realized P&L since `shared_account_since` + open WARP P&L. Otherwise strategy NAV. §3.1 | `WARP_SLEEVE_USDT` (shared only) | unset | `src/warp3c/risk.py` `warp_sleeve_equity`, `circuit_reason` |
| Portfolio ceiling 75% of E. §3.1 | `WARP_ALLOCATION_CEILING` | `0.75` | `src/warp3c/config.py:133`, `capital.capital_caps` `src/warp3c/capital.py:153` |
| Reserve floor 25% of E. §3.1 | `WARP_MIN_FREE_RESERVE_FRACTION` | `0.25` | `src/warp3c/config.py:355`, `src/warp3c/capital.py:152` |
| Per-deal ceiling 25% of E. §3.1 | `WARP_PER_DEAL_CEILING` | `0.25` | `src/warp3c/config.py:134`, `src/warp3c/capital.py:154` |
| Legacy fractions can only shrink that ceiling. §6.2 | `WARP_MAX_COMMITTED_FRACTION`, `WARP_MAX_PER_PAIR_FRACTION`, `WARP_MAX_GROUP_FRACTION` | `0.40`, `0.15`, `0.25` | `src/warp3c/risk.py:528` `full_budget`; `src/warp3c/engine.py:1005` passes `plan.budget` as `max_commitment` |
| Max concurrent deals 3. §7.1 | `WARP_MAX_DEALS` | `3` | `src/warp3c/config.py:135`, `src/warp3c/capital.py:723` |
| Stop risk per deal 2.5% of E. §7.1 | `WARP_DEAL_STOP_RISK` | `0.025` | `src/warp3c/config.py:136`, `src/warp3c/capital.py:320` |
| Aggregate open stop risk 6.5% of E. §7.1 | `WARP_AGGREGATE_STOP_RISK` | `0.065` | `src/warp3c/config.py:137`, `src/warp3c/capital.py:322` |
| Daily loss halt 3% of day-start E. The stricter of the two layers wins. §7.1 | `WARP_TRADE_DAILY_LOSS` and `WARP_MAX_DAILY_LOSS_FRACTION` | `0.03` and `0.05` | `src/warp3c/risk.py:376` |
| Peak drawdown halt 8%. The stricter of the two layers wins. §7.1 | `WARP_TRADE_DRAWDOWN` and `WARP_MAX_DRAWDOWN_FRACTION` | `0.08` and `0.15` | `src/warp3c/risk.py:377` |
| Open WARP stop, unfilled safety orders, owned base. Missing data waits. §3.1, §7.1 | (stored ladder, not a new cap) | n/a | `src/warp3c/engine.py:1053` `_warp_exposure`; journal column `warp_bots.ladder` |
| Order floor: exchange minimum × (1 + cushion) + fee, and at least 5 USDT. §6.2 | `WARP_MIN_ORDER_USDT` | `5` | `src/warp3c/config.py:161`, `src/warp3c/capital.py:264` `order_minimum` |
| LOT_SIZE minQty/stepSize after rounding. Missing filter waits. §6.1 | Binance `LOT_SIZE` on the evidence | none | `src/warp3c/capital.py:270` `lot_ok`; `src/warp3c/market.py` `_rules`; `src/warp3c/engine.py` waits with `lot_size` |
| Entry signal ≤ 5 min. Live quote ≤ 30 s. Dashboard cards still use the 30 minute card age. §7.1 | `WARP_MAX_ENTRY_SIGNAL_SECONDS` (new). `WARP_MAX_SIGNAL_AGE_MINUTES` unchanged | `300` and `30` | `src/warp3c/config.py:162`, `src/warp3c/engine.py:1027` `_entry_stale` |
| Account must be the saved target and Binance spot. §3.2 | saved `target_account` | n/a | `src/warp3c/engine.py:1038` `_account_mismatch`; `capital.approve` reason `wrong_account` |
| Stress cap 8% of E. Not raised. §7.2 | `WARP_STRESS_CAP` | `0.08` | `src/warp3c/config.py:143`, `src/warp3c/capital.py:346` `scenario_stress` |
| Adverse slippage in the stress grid, top of the 0.3–1% band. §7.2 | `WARP_STRESS_SLIPPAGE` | `0.01` | `src/warp3c/config.py:406` |
| Decision record on `/api/status`. No secrets, no coin balances. §5.2, §10 | n/a | latest 20 | `src/warp3c/state.py` `record_risk_decision`; `src/warp3c/operator.py:295` |

## Sizing

`GlobalRisk.plan` still selects candidates, rejects duplicates and full slots, and computes the legacy budget `full_budget` (the min of the committed, per-pair and per-group fractions times equity, minus the reserve). `capital.approve` receives that budget as `max_commitment` and `WARP_MAX_BASE_ORDER_USDT` as `max_base`. It does not receive `base × legacy ladder gross`. A legacy `WARP_MIN_BASE_ORDER_USDT` below the tradebook ladder does not drop the candidate. The legacy budget can only make the AEGIS cap smaller.

## Ladder shape

The tradebook sets `B0 = 0.15 × M` and then `SO1 = (capital − B0) / G`. The code keeps the 3Commas shape: the base order is repeated as the first safety order (`applyDcaForFirstSafetyOrder` false), so base = SO1. `build_ladder` in `src/warp3c/capital.py` is that shape. The 0.15 base share is not what is sent.

## extraOrderCount

Verified from the official 3Commas schemas, not guessed:

- UpsertDcaBot: "Maximum entry orders including the initial entry; 5 permits the entry and four extra orders." https://trade.3commas.io/docs/rest-api/schemas/upsertdcabot
- EditDcaStrategy: "Entry order + DCA orders total count." https://trade.3commas.io/docs/rest-api/schemas/editdcastrategy

`apply_ladder` therefore still sends `extraOrderCount = safety_orders + 1` (`src/warp3c/capital.py:782`). The host string is not in the Python source because `tests/test_p5_release_contracts.py` rejects it there. `readback_ok` compares `amountPerTrade`, `extraOrderCount`, `extraOrderVolumeMultiplier`, `extraOrderDeviation` and `stopLoss` when the echo contains them. A mismatch makes `readback_ok` false, and the engine raises an uncertain `IntegrationError` ("3Commas read-back does not match the approved order"). An id-only body is still accepted. Absent keys are not treated as a match against a guessed value.

## Stress

`stress_loss` (flat 30% shock plus 1% slippage) is unchanged and is the fallback when the caller has open commitment but no per-deal stops. That path is stricter than the grid. The engine does not use it for a live WARP deal: a missing stored ladder is `WAIT_DATA_INCOMPLETE` (`open_risk`), not a zero.

`scenario_stress` shocks every open deal together by 10%, 20%, 30% and 40% from its own commitment. Loss inside the stop is the shocked loss plus slippage. Past the stop, loss is that deal's `stop_loss` plus slippage on the commitment. The stop is not assumed to fill at the trigger.

Worked comparison at E = 1200 with the solver's own ladder (commitment 300, stop about 26.92 USDT, five safety orders): flat stress on three such deals is 276.30 USDT, above the 96 USDT cap, so the old model cannot hold them. The grid prices the same three at about 89.76 USDT, under the cap. Three times that stop is about 80.76 USDT, which is over 6.5% of 1200 (78 USDT), so the aggregate stop gate still rejects the third deal. `WARP_STRESS_CAP` stays 0.08. There is no new fixed maximum commitment: the ceiling is the minimum of 75% of E and whatever set of stored stops keeps the grid ≤ 8% of E.

## Freshness

`WARP_MAX_SIGNAL_AGE_MINUTES` (default 30) still governs dashboard cards and `signal_executable`. The entry check is separate. With a tradebook gate, the evidence must be ≤ `WARP_MAX_ENTRY_SIGNAL_SECONDS` and `quote_age` must be present and ≤ 30. With no gate, the only timestamp is the evidence, so it has to be ≤ 30 seconds. That is inside the 5 minute signal limit. Missing or naive timestamps wait.

## Both-mode changes

These apply with `WARP_SHARED_ACCOUNT` unset:

- AEGIS sizes inside `plan.budget`, not inside the legacy ladder gross.
- `circuit_reason` uses the tighter of the sleeve/account fractions and the tradebook fractions. With defaults that is 3% daily (was 5%) and 8% drawdown (was 15%). A 4% drop from the day start is `daily_loss`. A drop of more than 8% from peak is `drawdown`.
- The entry stale check runs with no tradebook gate.
- Missing `min_qty` or `step_size` on the evidence waits with `lot_size`.
- `capital.approve` is passed `mismatched_account` when the fetched account is not Binance spot or disagrees with the saved target.

Shared-mode-only: realized P&L ledger, persisted day-start and peak, and passing those into `capital.approve`.

## UNVERIFIED

- Whether 3Commas `totalEntryCost` and `totalExitCost` are already net of fees. Realized P&L subtracts `totalFee` only when that field is present. A missing cost fails closed. It is not booked as zero.
