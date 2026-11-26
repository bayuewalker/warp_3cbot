# Tradebook v1.0 mapping

Source: Grokbot Dynamic Spot DCA Tradebook v1.0 (11 November 2026), the text embedded with the PR #33 audit. Section numbers below are that document. This file records what the code enforces. It does not authorize live trading.

`go_no_go` in `src/warp3c/capital.py` is still not a hard gate.

## Rule → env → default → code

| Rule | Env var | Default | Code |
| --- | --- | --- | --- |
| Eligible NAV E. Shared mode: sleeve + realized P&L since `shared_account_since` + open WARP P&L. Otherwise strategy NAV. §3.1 | `WARP_SLEEVE_USDT` (shared only) | unset | `src/warp3c/risk.py` `warp_sleeve_equity`, `circuit_reason` |
| Portfolio ceiling 75% of E. §3.1 | `WARP_ALLOCATION_CEILING` | `0.75` | `src/warp3c/config.py:141`, `capital.capital_caps` `src/warp3c/capital.py:155` |
| Reserve floor 25% of E. §3.1 | `WARP_MIN_FREE_RESERVE_FRACTION` | `0.25` | `src/warp3c/config.py:375`, `src/warp3c/capital.py:154` |
| Per-deal ceiling 25% of E. §3.1 | `WARP_PER_DEAL_CEILING` | `0.25` | `src/warp3c/config.py:142`, `src/warp3c/capital.py:156` |
| Legacy fractions can only shrink that ceiling. §6.2 | `WARP_MAX_COMMITTED_FRACTION`, `WARP_MAX_PER_PAIR_FRACTION`, `WARP_MAX_GROUP_FRACTION` | `0.40`, `0.15`, `0.25` | `src/warp3c/risk.py:654` `full_budget`; `src/warp3c/engine.py:1044` passes `plan.budget` as `max_commitment` |
| Max concurrent deals 3. §7.1 | `WARP_MAX_DEALS` | `3` | `src/warp3c/config.py:143`, `src/warp3c/capital.py:768` |
| Stop risk per deal 2.5% of E. §7.1 | `WARP_DEAL_STOP_RISK` | `0.025` | `src/warp3c/config.py:144`, `src/warp3c/capital.py:345` |
| Aggregate open stop risk 6.5% of E. §7.1 | `WARP_AGGREGATE_STOP_RISK` | `0.065` | `src/warp3c/config.py:145`, `src/warp3c/capital.py:347` |
| Daily loss halt 3% of day-start E. The stricter of the two layers wins. §7.1 | `WARP_TRADE_DAILY_LOSS` and `WARP_MAX_DAILY_LOSS_FRACTION` | `0.03` and `0.05` | `src/warp3c/risk.py:495` |
| Peak drawdown halt 8%. The stricter of the two layers wins. §7.1 | `WARP_TRADE_DRAWDOWN` and `WARP_MAX_DRAWDOWN_FRACTION` | `0.08` and `0.15` | `src/warp3c/risk.py:496` |
| Open WARP stop, unfilled safety orders, owned base. Missing data waits. §3.1, §7.1 | (stored ladder, not a new cap) | n/a | `src/warp3c/engine.py:1092` `_warp_exposure`; journal column `warp_bots.ladder` |
| Order floor: exchange minimum × (1 + cushion) + fee, and at least 5 USDT. §6.2 | `WARP_MIN_ORDER_USDT` | `5` | `src/warp3c/config.py:169`, `src/warp3c/capital.py:289` `order_minimum` |
| LOT_SIZE minQty/stepSize after rounding. Missing filter waits. §6.1 | Binance `LOT_SIZE` on the evidence | none | `src/warp3c/capital.py:295` `lot_ok`; `src/warp3c/market.py` `_rules`; `src/warp3c/engine.py` waits with `lot_size` |
| Entry signal ≤ 5 min. Live quote ≤ 30 s. Dashboard cards still use the 30 minute card age. §7.1 | `WARP_MAX_ENTRY_SIGNAL_SECONDS` (new). `WARP_MAX_SIGNAL_AGE_MINUTES` unchanged | `300` and `30` | `src/warp3c/config.py:170`, `src/warp3c/engine.py:1066` `_entry_stale` |
| Account must be the saved target and Binance spot. §3.2 | saved `target_account` | n/a | `src/warp3c/engine.py:1077` `_account_mismatch`; `capital.approve` reason `wrong_account` |
| Stress cap 8% of E. Not raised. §7.2 | `WARP_STRESS_CAP` | `0.08` | `src/warp3c/config.py:151`, `src/warp3c/capital.py:373` `scenario_stress` |
| Adverse slippage in the stress grid, top of the 0.3–1% band. §7.2 | `WARP_STRESS_SLIPPAGE` | `0.01` | `src/warp3c/config.py:426` |
| Share of a move past the stop that is treated as lost. Range 0 to 1. Never lowers the 1% slippage floor. §7.2 | `WARP_STRESS_GAP_SHARE` | `0.50` | `src/warp3c/config.py:173`, `src/warp3c/capital.py:395` |
| Dust residual booked at a fresh mark. Must be above 0 and at most 5. | `WARP_DUST_USDT` | `1.00` | `src/warp3c/config.py:172`, `src/warp3c/risk.py:377` |
| Research retry base. Range 30 to `WARP_RESEARCH_SECONDS`. Unset and a research interval under 60 clamps to `min(60, research_seconds)`. | `WARP_RESEARCH_RETRY_SECONDS` | `60` | `src/warp3c/config.py:33` `research_retry_default`, field `src/warp3c/config.py:171` |
| Risk-decision rows kept. Minimum 100. | `WARP_RISK_DECISIONS_KEEP` | `5000` | `src/warp3c/config.py:174`, `src/warp3c/state.py:484` `record_risk_decision` |
| Decision record on `/api/status`. No secrets, no coin balances. §5.2, §10 | n/a | latest 20 | `src/warp3c/state.py` `recent_risk_decisions`; `src/warp3c/operator.py:333` |

`WARP_LLM_DAILY_CALLS` stays 48 and `WARP_LLM_DAILY_USD` stays 2 (`src/warp3c/config.py:132`). `WARP_RESEARCH_SECONDS` stays 900 (`src/warp3c/config.py:83`). Those defaults are not raised here.

## Sizing

`GlobalRisk.plan` still selects candidates, rejects duplicates and full slots, and computes the legacy budget `full_budget` (the min of the committed, per-pair and per-group fractions times equity, minus the reserve). `capital.approve` receives that budget as `max_commitment` and `WARP_MAX_BASE_ORDER_USDT` as `max_base`. It does not receive `base × legacy ladder gross`. A legacy `WARP_MIN_BASE_ORDER_USDT` below the tradebook ladder does not drop the candidate. The legacy budget can only make the AEGIS cap smaller.

## Ladder shape

The tradebook sets `B0 = 0.15 × M` and then `SO1 = (capital − B0) / G`. The code keeps the 3Commas shape: the base order is repeated as the first safety order (`applyDcaForFirstSafetyOrder` false), so base = SO1. `build_ladder` in `src/warp3c/capital.py` is that shape. The 0.15 base share is not what is sent.

`apply_ladder` (`src/warp3c/capital.py:834`) always sets `extraOrderDeviationMultiplier` to 1 and `applyDcaForFirstSafetyOrder` to false, including when the incoming payload or the preset multiplier is 1.25. It does not read `WARP_DCA_DEVIATION_MULTIPLIER`.

UpsertDcaBot (https://trade.3commas.io/docs/rest-api/schemas/upsertdcabot): `extraOrderDeviationMultiplier` "Scales successive extra-order price spacing." `applyDcaForFirstSafetyOrder` false is Position Averaging. The spacing used here is cumulative: each safety order's gap is `step × multiplier^(i-1)`, and the price is `price × (1 − cumulative gap)`. At multiplier 1 that is `price × (1 − step × i)`, which is `build_ladder`. A non-cumulative reading would stack every safety order at the same price when the multiplier is 1, and the N=5..8 round-trip test rejects that.

## extraOrderCount

Verified from the official 3Commas schemas, not guessed:

- UpsertDcaBot: "Maximum entry orders including the initial entry; 5 permits the entry and four extra orders." https://trade.3commas.io/docs/rest-api/schemas/upsertdcabot
- EditDcaStrategy: "Entry order + DCA orders total count." https://trade.3commas.io/docs/rest-api/schemas/editdcastrategy

`apply_ladder` therefore still sends `extraOrderCount = safety_orders + 1` (`src/warp3c/capital.py:827`). The host string is not in the Python source because `tests/test_p5_release_contracts.py` rejects it there. `readback_ok` compares `amountPerTrade`, `extraOrderCount`, `extraOrderVolumeMultiplier`, `extraOrderDeviation`, `stopLoss`, `extraOrderDeviationMultiplier` and `applyDcaForFirstSafetyOrder` when the echo contains them. A multiplier other than 1, or `applyDcaForFirstSafetyOrder` other than false, makes `readback_ok` false. The engine raises an uncertain `IntegrationError` ("3Commas read-back does not match the approved order"). An id-only body is still accepted. Absent keys are not treated as a match against a guessed value.

## Realized P&L

Official Strategy schema (https://trade.3commas.io/docs/rest-api/schemas/strategy):

- `totalEntryCommissionCost` and `totalExitCommissionCost` are the entry and exit orders' cost commission sum, in quote. Both are required on a retired row. Missing or unparseable fails closed with "Realized P&L unreadable; entries halted". An explicit `0` is a fee of zero. One field present and the other absent fails closed. The fee is subtracted from realized P&L.
- `totalEntryCommission` and `totalExitCommission` are the amount commission, in base. Held base is `totalEntryAmount − totalEntryCommission − totalExitAmount`. `totalExitCommission` is not subtracted. A negative residual fails closed.
- `totalProfitLoss` is commission-included P&L in $ basis points. The code converts it with `× 0.0001` USD. If it differs from the computed P&L by more than `max(0.05 USDT, 0.5% of entry cost)`, the more negative of the two is booked and a `pnl_crosscheck_mismatch` event records `computed`, `reported` and `taken`. An unparseable value fails closed. An absent value skips the cross-check.

A residual whose fresh mark value is strictly below `min(market minNotional, WARP_DUST_USDT)` is dust. Its quote value at that mark is subtracted from realized P&L (it is not booked as zero) and a `dust_residual` event records `qty` and `value`. The engine passes the mark and `minNotional` from cached evidence that is still inside the account-age window. `risk.py` does not call the network. No fresh mark, no `minNotional`, or a residual at or above the threshold keeps the halt "Closed strategy still holds base; realized P&L withheld". Shared mode leaves the dust in the sleeve. Nothing sells or converts it.

## Stress

`stress_loss` (flat 30% shock plus 1% slippage) is unchanged. `portfolio_stress` uses it only for a single deal with an empty open book, and only when it is larger than the scenario. A book that already has deals uses the scenario alone. The engine does not use the flat shock for a live WARP deal whose stored ladder is missing: that is `WAIT_DATA_INCOMPLETE` (`open_risk`), not a zero.

`scenario_stress` shocks every open deal together by 10%, 20%, 30% and 40% from its own commitment. Loss inside the stop is the shocked loss plus slippage. Past the stop, loss is `stop_loss + commitment × max(stress_slippage, gap_fraction)`, with `gap_fraction = (shock − stop/commitment) × WARP_STRESS_GAP_SHARE`. The stop is not assumed to fill at the trigger (tradebook §7.2). The result is never below today's `stop + commitment × 1%`.

Worked comparison at E = 1200 with the solver's own ladder (price 100, cap 300, ATR 3.5%, commitment 300, stop 26.92052313596159528579790344 USDT, five safety orders, step 0.0315): flat stress on three such deals is 276.30 USDT. The gap-aware grid prices the same three at 220.3807847039423929286968551 USDT. Both are above the 96 USDT cap. The previous grid figure of about 89.76 is no longer the result. Three times that stop is 80.76156940788478585739371032 USDT, which is over 6.5% of 1200 (78 USDT), so the aggregate stop gate still rejects the third deal. `WARP_STRESS_CAP` stays 0.08.

Max total commitment the solver will stack, with price 100, order minimum 5, ATR unset (step 0.02), an empty book, and each new deal capped at `min(25% of E, room left under 75% of E)`. The legacy 15% per-pair budget is not applied in these three figures. Production still passes `plan.budget`, so a live order can be smaller.

| E | Deals the solver keeps | Total commitment | Scenario stress | Stress cap |
| --- | --- | --- | --- | --- |
| 250 | 1 × 62.50 (stop 6.210346515661278129378026141) | 62.50 | 15.60517325783063906468901307 | 20 |
| 1200 | 300.00 + 98.30 | 398.30 | 95.13334996940591586055709515 | 96 |
| 5000 | 1250.00 + 409.60 | 1659.60 | 396.3929390138741098724091271 | 400 |

A second deal at E = 250 does not clear the gates. E = 250 with the legacy pair budget (`max_commitment` 37.50, price 100, free 250) is still APPROVED: safety orders 6, commitment 37.50, stop 2.915945968505273345987962086 (2.916 USDT at 0.001, half up), 1.166378387402109338395184834% of E. The single-deal check uses the larger of the flat shock (11.5125) and the gap scenario (8.957972984252636672993981042). 11.5125 is under the 20 USDT cap.

## Freshness

`WARP_MAX_SIGNAL_AGE_MINUTES` (default 30) still governs dashboard cards and `signal_executable`. The entry check is separate. With a tradebook gate, the evidence must be ≤ `WARP_MAX_ENTRY_SIGNAL_SECONDS` and `quote_age` must be present and ≤ 30. With no gate, the only timestamp is the evidence, so it has to be ≤ 30 seconds. That is inside the 5 minute signal limit. Missing or naive timestamps wait.

## Research budget

Usage for the UTC day (calls and USD) is stored in the journal setting `llm_budget`. A new process reads it. A corrupt value raises "LLM budget unreadable" and does not reset the counter to zero. `/api/status` `providers.budget` is `{day, calls_used, calls_cap, usd_used, usd_cap}`.

At startup, `llm_budget_short` is logged once at WARNING when `86400 / research_seconds × 2` is greater than `WARP_LLM_DAILY_CALLS`. The defaults are 192 calls needed and a cap of 48. The log does not change either default.

A failed research run logs one `research_failed` event (`error_class`, `error` truncated to 200 characters, `cause_class`, and for an `LlmError` cause the provider name and HTTP status only). A `research_blocked` reminder is at most one per 300 seconds. The next attempt is 60, 120, 240, … seconds later, capped at `WARP_RESEARCH_SECONDS`, and it charges the same budget. "LLM daily cap reached" and "LLM providers are not configured" wait for the ordinary research interval. While the latest run failed and the risk reason would otherwise be `ok`, `engine.status.reason` is `research_blocked:<code>` with `code` one of `llm_cap`, `providers_failed`, `bad_json`, `unconfigured`, `research_error`. `/api/status` `research` is `{state, error_code, last_ok_at, last_failed_at, next_attempt_at, consecutive_failures}` and `state` is `ok`, `failed`, `backoff` or `capped`. `pipeline.analysis.state` is `blocked` in the three failing states. `dependencies_healthy`, `last_cycle_ok` and `risk_permitted` are not changed for a research-only failure.

## Risk decisions

`record_risk_decision` inserts the row and deletes older rows in the same transaction, keeping the newest `WARP_RISK_DECISIONS_KEEP` rows. `/api/status` `risk_decisions` is still the newest 20 and the same keys. Opening an existing database does not drop the table.

## Both-mode changes

These apply with `WARP_SHARED_ACCOUNT` unset:

- AEGIS sizes inside `plan.budget`, not inside the legacy ladder gross.
- `circuit_reason` uses the tighter of the sleeve/account fractions and the tradebook fractions. With defaults that is 3% daily (was 5%) and 8% drawdown (was 15%). A 4% drop from the day start is `daily_loss`. A drop of more than 8% from peak is `drawdown`.
- The entry stale check runs with no tradebook gate.
- Missing `min_qty` or `step_size` on the evidence waits with `lot_size`.
- `capital.approve` is passed `mismatched_account` when the fetched account is not Binance spot or disagrees with the saved target.
- `apply_ladder` sends deviation multiplier 1 and Position Averaging.
- Realized P&L requires the quote commission-cost fields, with the dust rule above.
- Past-stop stress uses `WARP_STRESS_GAP_SHARE`.
- `risk_decisions` keeps the newest `WARP_RISK_DECISIONS_KEEP` rows.

Shared-mode-only: realized P&L ledger, persisted day-start and peak, and passing those into `capital.approve`. Dust that is booked stays inside that sleeve ledger.

## UNVERIFIED

- `totalFee` is not a field on the official Strategy schema. It is used only when both `totalEntryCommissionCost` and `totalExitCommissionCost` are absent and `totalFee` is present.
- Whether `totalEntryCost` and `totalExitCost` are already net of `totalEntryCommissionCost` and `totalExitCommissionCost`. Subtracting the commission-cost fields assumes the costs are gross. When `totalProfitLoss` disagrees beyond the tolerance, the more negative value is the one booked.
