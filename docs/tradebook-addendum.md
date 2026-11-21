# WARP addendum v1.1

These settings sit on top of the Grokbot Dynamic Spot DCA Tradebook v1.0. The tradebook values stay the defaults unless this page names a different one. Nothing here authorizes live trading.

## 1. Strategy capital sleeve

`E` is not the whole exchange account. Other bots (VORTEQ, AEGIS, and any bot whose name does not start with `WARP-`) keep their committed funds and unfilled safety orders.

`E = min(sleeve, WARP-owned value + free USDT not reserved for non-WARP safety orders)`.

| Env | Default | Meaning |
| --- | --- | --- |
| `WARP_SLEEVE_USDT` | `0` (unused) | Absolute sleeve cap |
| `WARP_SLEEVE_PCT` | `0.10` | Fraction of account USDT |

If both are set, the sleeve is the smaller of the two.

## 2. Account stress

All open WARP deals down 30% from their anchors, then 1% worse on the exit, must not lose more than 8% of `E`. Otherwise the new deal is vetoed.

`WARP_STRESS_SHOCK=0.30`, `WARP_STRESS_SLIPPAGE=0.01`, `WARP_STRESS_CAP=0.08`.

## 3. Slippage-adjusted stop

Projected stop loss uses `stop_price * (1 - 0.5%)` plus taker fees on the entry and the exit. `WARP_STOP_SLIPPAGE=0.005`. Fees follow `WARP_FEE_BPS`.

## 4. Correlation

At most one open deal in a cluster whose 30-day daily-return correlation is above `WARP_CORR_MIN` (0.85). At most `WARP_MAX_ALTS` (2) names that are not BTCUSDT or ETHUSDT.

## 5. BTC regime

No new entries when BTCUSDT 4h close is below its 4h EMA200 and the 1h EMA50 slope is negative, or BTC 24h change is at or below -5%, or BTC 1h ATR% is more than twice its 30-day median.

## 6. Cooldowns

| Env | Default |
| --- | --- |
| `WARP_COOLDOWN_STOP_HOURS` | 24 |
| `WARP_COOLDOWN_TP_HOURS` | 2 |
| `WARP_STREAK_PAUSE_HOURS` | 12 after `WARP_STOP_STREAK` (2) consecutive stops |

## 7. Time exit

Deals older than `WARP_REVIEW_DAYS` (10) that are not in profit are flagged for review. WARP does not close them. Their commitments stay reserved until the configured stop or an operator action.

## 8. Stop and take-profit toggles

Each bot preset has two switches, both on by default, plus an optional trailing exit that defaults off.

| Switch | Default | When off |
| --- | --- | --- |
| Stop loss | on | No stop is sent to 3Commas. Size still uses a virtual stop: the lower of the tradebook stop and `P0 * (1 - WARP_VIRTUAL_STOP)`. `WARP_VIRTUAL_STOP` defaults to 0.35. The 2.5% deal cap, 6.5% book cap, and 8% stress cap still apply to that virtual loss, so turning the stop off makes the order smaller. The dashboard shows **No SL**. |
| Take profit | on | The deal needs a trailing exit or it is marked **Manual exit**. A deal with neither take-profit, trailing, nor stop still shows that warning. |
| Trailing exit | off | When on, and take-profit is off, the bot sends a trailing exit instead of a fixed target. |

Read-back checks only the exits that are on. An id-only create response is accepted. An echoed body that disagrees with the switches is rejected.

## 9. Score threshold and rubric

`WARP_MIN_SCORE` defaults to 80. The dashboard can set a different floor on the bot preset, from 50 to 100. A floor below 80 is saved and shown with a warning badge. A model `BUY` is still required, and it is not enough on its own.

The 100-point rubric is visible and editable. The five weights must sum to 100.

| Component | Default |
| --- | --- |
| Trend | 25 |
| Reversal | 25 |
| Liquidity | 20 |
| Structure | 20 |
| Events | 10 |

## 10. Fees and take-profit floor

When take-profit is on, the target is at least 1.2% gross and at least three times the round-trip fee plus slippage estimate.

## 11. Go / no-go

The forward journal reports the average move after round-trip costs. Live is not offered by this screen. A paper record is a pass only when it has at least 30 days, at least 30 closed deals, positive net expectancy, and max drawdown under 8%.

## Tradebook values kept

Allocation ceiling 75%, reserve 25%, per-deal ceiling 25%, max 3 deals, stop risk 2.5% of `E`, aggregate stop risk 6.5% of `E`, daily loss halt 3% of day-start NAV, drawdown halt 8%, spot buy/long only, finite 5–8 safety orders.
