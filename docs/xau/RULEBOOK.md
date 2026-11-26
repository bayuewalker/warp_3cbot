# XAUUSD Refined Rulebook v1.0 (WARP-XAU P0)

Sources of truth (boss t117u), in priority order:
1. **MASTER** = `XAUUSD MASTER EXECUTION PLAYBOOK v2.0` (/workspace/xau-rules/194f8c0f*.pdf, text 194f8c0f.txt)
2. **EMA** = `XAUUSD EMA 20/50 TRIPLE-RETEST PLAYBOOK v1.0` (/workspace/xau-rules/1d6eea19*.pdf, text 1d6eea19.txt)

These replace the older box skills (master v2 skill, SMC v1.1, EMA21/50 pullback) wherever they conflict. Older-skill rules are used only to fill a gap the PDFs leave open, and each such use is listed in §12.

Every rule has an ID. Code and tests must reference these IDs. **No refinement loosens a PDF risk rule.** Where two rules overlap, the stricter one applies.

---
## 0. Conventions
- **R0.1 Time.** Timestamps are stored in UTC and displayed in WIB (Asia/Jakarta, UTC+7). All bar logic uses **closed bars only**; nothing is decided intrabar.
- **R0.2 Trading day.** A day runs from 17:00 America/New_York to the next 17:00 America/New_York (DST-aware; 05:00 WIB in winter). PDH/PDL, daily caps and daily stops use this day. A **week** starts at the first trading-day open after Friday 17:00 New York.
- **R0.3 Price frame.** Levels are published in the spot-mid frame (spec: perp − basis). `S` = assumed spread = **$0.30** in P0 (config `WARP_XAU_SPREAD_ASSUMED`). Side prices: ask = mid + S/2, bid = mid − S/2.
- **R0.4 Rounding.** Levels have 2 decimals. SL is rounded **away** from entry and TP **toward** entry, so rounding can never improve R.
- **R0.5 Indicators.**
  - EMA(n) is computed on close with α = 2/(n+1), seeded with SMA(n), using at least 200 warm-up bars.
  - ATR(n) is Wilder's ATR with n = 14 unless stated.
  - `ATR_M15`, `ATR_H1` and `ATR_D1` are ATR14 on that timeframe.
- **R0.6 Pip.** For XAUUSD, 1 pip = **$0.10** (so 20 pips = $2.00), as fixed by the EMA PDF §5. A per-broker override goes in the profile at P1.
- **R0.7 R.** 1R = |entry − SL| in $. realized_R = Σ(fraction × exit-move) / 1R, after S.
- **R0.8 Reference equity (P0).** Equity = profile `starting_balance` (default 10,000) + the simulated realized P&L of sent signals. The %-caps use this equity.

## 1. Structure primitives (shared)
- **D1 Swing.** A pivot high (or low) with 2 candles on each side whose highs are all lower (or lows all higher). It is confirmed only once the 2nd right-hand candle closes. A single wick is not a swing unless it meets this test. *(MASTER §4)*
- **D2 BOS.** A body close (close, not wick) beyond the most recent confirmed swing in the trend direction. *(MASTER §4)*
- **D3 CHoCH.** A body close beyond the most recent confirmed **opposing** swing. It marks a candidate reversal, not permission to enter by itself. A wick-only break is not a CHoCH. *(MASTER §4, §10.4)*
- **D4 Micro CHoCH** (S2 and scalp). Same as D3, but the swing is a pivot with 1 candle on each side.
- **D5 Displacement.**
  - At least 2 consecutive candles on the trigger timeframe, all closing in the trade direction.
  - Each candle's body is ≥ 60% of its own range.
  - The combined high-low range of those candles is ≥ 1.0 × ATR of that timeframe.
  - The sequence leaves at least one FVG (D6).
  - *(MASTER §4 + numeric thresholds, §12)*
- **D6 FVG.**
  - A three-candle imbalance. Bullish: `high[c1] < low[c3]`, zone = [high[c1], low[c3]]. Bearish: `low[c1] > high[c3]`, zone = [high[c3], low[c1]].
  - Minimum size: max($0.30, 0.10 × ATR of that timeframe).
  - **CE** = zone midpoint.
  - The FVG is mitigated (dead) once a candle **closes** beyond its far edge.
- **D7 Order block (OB).** The last opposite-colour candle before a displacement (D5). The zone is that candle's high to low; the OB 50% is its midpoint.
- **D8 Liquidity levels.**
  - SSL (for longs, swept below): the Asia range low (07:00–14:00 WIB), PDL, the London KZ low (for the NY KZ only), confirmed M15 swing lows in the last 96 M15 bars, and equal lows.
  - Equal lows: ≥ 2 confirmed M5/M15 swing lows within $0.50 of each other in the last 48 bars.
  - BSL is the mirror for shorts.
- **D9 Sweep.**
  - A candle trades beyond a D8 level by at least $0.10.
  - Within **≤ 2 candles** of the same timeframe (counting that candle), price **closes back** inside the level.
  - If 2 consecutive closes stay beyond the level, it is a BOS (break), not a sweep. *(MASTER §4, plus a closing window)*
- **D10 H4 dealing range.** The H4 leg between the most recent confirmed H4 swing low and swing high that produced the latest H4 BOS.
  - EQ = 50% of that leg.
  - Discount = below EQ, premium = above EQ.
  - OTE = 62–70.5% retracement of the displacement leg being traded. It counts as confluence only. *(MASTER §4)*
- **D11 HTF bias** (per timeframe, D1 or H4).
  - **Bullish:** the last BOS was up and the close is above the last confirmed swing low.
  - **Bearish:** the mirror.
  - **Range:** neither.
  - **Fresh HTF reversal:** an H4 CHoCH against the D1 bias within the last 6 H4 bars, made by an H4 displacement (D5). A setup relying on it is capped at grade A.
- **D12 M15 directional / chop.**
  - **Directional:** the last M15 BOS has a direction and the close is beyond the last opposing M15 swing.
  - **Overlap/chop:** the last 8 M15 bars span (max high − min low) < 1.5 × ATR_M15, **or** three M5 CHoCHs have printed without an M15 BOS. *(MASTER §3B)*
- **D13 DOL** (draw on liquidity). The nearest unswept D1/H4 swing high (for longs) or swing low (for shorts) beyond price. It is recorded on the signal and serves as the TP2 candidate.

## 2. Clock (WIB, fixed per MASTER; DST decision due before 2027-03-14)
- **K1 Killzones.** London KZ 14:00–17:00 WIB; NY KZ 20:00–23:00 WIB. *(MASTER header)*
- **K2 Scalp ritual.**
  - 13:50 / 19:50: write the bias.
  - 14:00–14:15 / 20:00–20:15: OR15 forms; no entries.
  - The execution window is 14:15–16:15 / 20:15–22:15.
  - No new scalp after 16:15 / 22:15; flatten within 8 minutes.
  - Friday: the NY scalp window closes at **21:45 WIB**. *(MASTER §7 + exact Friday time, §12)*
- **K3 EMA windows.** London/NY active hours **outside** both KZs: **17:00–20:00 WIB** and **23:00–02:00 WIB**. No EMA entry at any other time, including Asia. No EMA entry after Friday 23:00 WIB. *(EMA §8 "London/NY active hours, avoid Asia" combined with the one-engine-per-window rule, §12)*
- **K4 Closed market.** No signals from Friday 17:00 New York to Sunday 18:00 New York, or during the daily 17:00–18:00 New York break. Perp bars in those periods are discarded.
- **K5 SMC Friday.** No new SMC signal after Friday 22:30 WIB. *(older SMC skill; tightening)*

## 3. Engine selection (one engine per window)
- **E1.** Each KZ is a window. The engine is selected **once**, at KZ start (14:00 / 20:00 WIB), from closed bars, then locked and journaled. *(MASTER §2, §13)*
- **E2 Selection.**
  - **SMC** if all of these hold:
    - D1 and H4 bias (D11) are aligned and not range, or a fresh HTF reversal exists.
    - At least 1 valid H1 POI (S1.2) exists on the correct side of EQ.
    - Distance from price to the DOL is ≥ 2 × ATR_H1. This is the pre-check proxy for a "≥ 2R path".
  - Otherwise **SCALP** if all of these hold: H4 is mixed or range, M15 is directional (D12), and there is no H1 veto (D2 BOS on H1 against the M15 direction within the last 4 H1 bars).
  - Otherwise **NO_TRADE**.
  - *(MASTER §2)*
- **E3.** No mixing. SMC and SCALP never both run in one KZ. EMA never fires inside a KZ, and only in the K3 windows.
- **E4.** Only **one live XAU signal** (pending or open) exists at a time across all engines. A new signal waits until the current one is closed or expired. *(Tightening; prevents stacking and cross-engine "recovery", MASTER §8)*

## 4. SMC engine (KZ only)
### S1 Judas Sweep (PRIMARY)
1. **Bias:** D1 and H4 are aligned (D11), or a fresh HTF reversal is used (capped at A). The DOL (D13) exists.
2. **H1 POI:**
   - An unmitigated H1 FVG (D6) or H1 OB (D7).
   - For longs it sits fully or mostly (≥ 50% of the zone) in discount (D10); premium for shorts.
   - At most the 2 nearest valid POIs are kept. If a third would be needed to explain the trade, there is no trade.
3. **Location:** during the KZ, an M15 candle's range overlaps the POI.
4. **Sweep:** an SSL (long) or BSL (short) D9 sweep on M15, made inside or into the POI during the KZ.
5. **Displacement:** an M15 D5 displacement away from the sweep extreme, starting within ≤ 4 M15 bars after the sweep candle.
6. **Trigger:**
   - An **M15 body CHoCH** (D3) in the trade direction, within ≤ 6 M15 bars after the sweep. *(MASTER §6, which replaces the M5 MSS of SMC v1.1)*
   - The opposing swing used is the last confirmed M15 swing high (for longs) before the sweep.
7. **Entry:**
   - Default: a **limit** at the CE of the displacement FVG. If several FVGs exist, use the one nearest the CHoCH close that lies inside the POI or between the POI and the CHoCH close.
   - **Market** entry (at the CHoCH close) only if that close is still inside the POI **and** R:R to TP1 ≥ 2.0 at that price.
8. **SL (long; short mirrors):** `SL = sweep_low − max(0.15 × ATR_H1, 1.50) − S/2`. *(MASTER "volatility buffer" made exact with the SMC v1.1 values, §12)*
9. **SL cap:** if `entry − SL > 1.3 × ATR_H1`, skip. Never tighten the SL to fit. *(MASTER §6)*
10. **First liquidity L1:** the nearest of the following beyond entry in the trade direction:
    - an unswept M15/H1 confirmed swing (internal liquidity);
    - H4 EQ (if entry is on the far side of it).

    If `(L1 − entry) < 2R` after S, skip (reward gate). *(MASTER §5, §10.5)*
11. **TP1 = entry + 2R** (long). Since L1 ≥ 2R, 2R is "structurally first". Exit 50% at TP1. *(MASTER §6)*
12. **TP2:** the DOL / external liquidity (H4 range high for longs).
    - If TP2 ≤ TP1 + 0.5R, no runner target is published: 100% exits at TP1. *(gap fill)*
    - **A+ "liquidity vacuum" edge:** no confirmed opposing H1 swing between TP1 and TP2.
13. **Grades:**
    - **A** = steps 1–12 and C1–C14 (§8) all pass.
    - **A+** = A plus at least one optional edge:
      - OTE overlap (the entry lies within 62–70.5% of the displacement leg);
      - clean session sweep (the swept level is the Asia low/high, PDL/PDH or the London KZ extreme);
      - the liquidity vacuum from step 12.
    - A fresh-HTF-reversal setup is capped at **A**. *(MASTER §5)*
14. **Invalidation before fill** (signal → INVALIDATED):
    - An M15 candle **closes** fully beyond the far side of the POI.
    - Price reaches TP1 before the limit fills (missed, no chase).
    - The KZ ends.
    - The news gate opens (N3).
    - *(MASTER §6 invalidation)*

### S2 Displacement FVG Continuation (SECONDARY)
1. Allowed only if **no S1 is developing**. "Developing" means a D9 sweep inside an H1 POI in this KZ without a completed CHoCH, within the last 6 M15 bars. *(MASTER §6)*
2. A fresh **H4 BOS** in the trade direction within the last **8 H4 bars**.
3. Price pulls back into an H1 or H4 FVG on the correct side of EQ, and touches its CE.
4. The retrace is ≤ 80% of the H4 displacement leg that made the BOS; otherwise skip.
5. Confirmation: an M15 reaction candle (it closes in the trade direction, body ≥ 50% of range), then an M15 **micro CHoCH** (D4) within ≤ 4 M15 bars.
6. Entry, SL, TP1, TP2, SL cap and grading are the same as S1 steps 7–13. The sweep extreme is replaced by the pullback extreme. The clean-session-sweep edge does not apply.

### S3 Breaker: research only
It is never generated as a signal and never sent. *(MASTER §6)*

### SMC risk & management (tracked by the outcome engine)
- **SR1 Risk %:** A+ 0.60%, A 0.40%.
  - Reduce to **0.35%** in a "wide-ATR/news week". That means ATR_D1 > 1.3 × its 50-day median, or the week contains CPI, NFP or an FOMC decision. *(MASTER §6; the definitions are §12)*
  - The profile cap applies if lower.
- **SR2:** 50% off at TP1. Then move the remainder's SL to **entry + $1.00** (long). *(MASTER "BE + execution buffer"; buffer from SMC v1.1)*
- **SR3 Trail:** only after +2.5R. Trail behind each newly confirmed M15 swing low (long), minus S/2. *(MASTER §6)*
- **SR4 Time stop:** close the remainder at the next M15 close when all of these hold:
  - ≥ 3 h since fill;
  - unrealized < +0.7R;
  - **M15 has lost delivery**, meaning no new M15 high (long) in the last 4 M15 bars.
  - *(MASTER §6 + definition, §12)*

## 5. EMA 20/50 Triple-Retest engine (K3 windows only; M15)
Definitions (long; short is the exact mirror):
- **M1 Band** = the zone between EMA20 and EMA50. Upper edge = max(EMA20, EMA50); lower edge = min(EMA20, EMA50).
- **M2 Fresh crossover.**
  - The bar where EMA20 closes above EMA50 after having been below it on the previous bar.
  - The setup must reach the retest-3 confirmation within **60 M15 bars** of the cross; otherwise reset. *(gap fill: "fresh")*
- **M3 Not flat:**
  - |EMA50[t] − EMA50[t−10]| ≥ 0.10 × ATR_M15, with the sign matching the direction;
  - the same test for EMA20.
- **M4 Chop:** ≥ 2 EMA20/EMA50 crosses within the last 20 M15 bars means no trade. *(EMA §2)*
- **M5 Separation:** after the cross, at least one closed M15 candle has its **low above the band's upper edge**. Retests are not counted before this. *(EMA §1, §3.2)*
- **M6 Retest event** (counted only after separation, or after the previous retest ended):
  - It **starts** at the first candle whose low ≤ EMA20 (it touches or enters the band).
  - It **ends validly** when a candle **closes > EMA20**.
  - Before that, it **ends invalidly** if any candle closes < EMA50; this resets the whole sequence.
  - All candles from start to end belong to **one** retest.
  - Before the next retest can start, a candle must close with its **low above EMA20** (price has left the band).
  - *(EMA §1)*
- **M7 Reset:** an EMA20/EMA50 recross before entry resets to zero. *(EMA §1, §9)*
- **M8 Trend filter at confirmation:**
  - EMA20 > EMA50 and both are not flat (M3);
  - the confirmation candle closes above both EMAs;
  - not chop (M4).
  - *(EMA §2)*
- **M9 Exhaustion skip:** skip if, between the end of retest 2 and the confirmation, any close was more than 3.0 × ATR_M15 from EMA20 **and** EMA20's 5-bar slope at confirmation is less than 50% of its 5-bar slope at the end of retest 2. *(EMA §2 "exhausted parabolic + flattening", made exact)*
- **M10 Confirmation candle** (inside retest #3):
  - close > open;
  - close > EMA20;
  - close is in the upper 50% of the candle's range.

  Retest #3 must end with this candle. If it ends with a non-qualifying close above EMA20, the sequence is spent: no 4th-retest rewrite, reset. *(EMA §3.5, checklist 6–7)*
- **M11 Entry:** a **buy stop** at `confirm_high + $0.20`. *(EMA "small broker-safe buffer" = $0.20, §12)*
- **M12 No chase:** skip if `confirm_close − EMA20 > 1.0 × ATR_M15`. *(EMA §9)*
- **M13 SL:** the **farther** from entry of:
  - `EMA50(at confirmation) − $2.00` (20 pips, R0.6);
  - `retest3_low − S`.

  Never pull it inside the retest swing. *(EMA §5)*
- **M14 Room:**
  - The nearest confirmed M15 swing high above entry within the last 100 bars must be ≥ entry + 1.5R; otherwise skip.
  - If there is no such swing, room passes.
  - *(EMA §3 A-grade, §5)*
- **M15 Expiry:** the pending buy stop expires at the **earliest** of these:
  - 2 M15 closes after the confirmation;
  - the end of the K3 window;
  - an M15 close < EMA50;
  - an EMA recross;
  - N3 news cancel.
  - *(gap fill; EMA "do not chase")*
- **M16 Grade A** = all of M2–M14 pass, plus all of these:
  - retests #1 and #2 were **clean and shallow**: each lasted ≤ 4 candles and no candle in it closed below EMA50;
  - EMA20 − EMA50 ≥ 0.25 × ATR_M15 at confirmation ("clearly above");
  - the confirmation falls in a K3 window;
  - room ≥ 1.5R.

  EMA has no A+. A setup that is not grade A is journal-only (MASTER §8: live grades are A/A+ only).
- **M17 Risk:** 0.50% (or the profile cap if lower). *(EMA §7)*
- **M18 Management (tracked):**
  - TP1: 50% at +1.5R. *(EMA §6)*
  - Runner exit: the first M15 **close** below EMA50 (long). The exit is at that close minus S/2.
  - BE: after TP1, move the SL to entry **only if** a new confirmed M15 swing high has formed above the TP1 price.
  - Emergency: an EMA20/EMA50 recross before TP1 means close everything at that bar's close.
- **M19 EMA caps:**
  - max **2 EMA signals per day**;
  - **2 full losses** or **−1.0% equity** in a day stops EMA for that day;
  - **−3.0% realized in a week** from EMA kills EMA until boss resumes it. *(EMA §7)*
  - These sit on top of the unified book (§9). The stricter rule binds.

## 6. Scalp engine (KZ only): computed and journaled, **NOT SENT in P0**
Decision: **Scalp stays shadow-only in P0.**
- MASTER checklist C4 needs a live click spread ≤ $0.35, with an abort above $0.45 for 30 s.
- K1 needs an M1 confirmation within 3 minutes of the reclaim, and management runs on a minute-8 / minute-12 clock.
- P0 has no broker spread, and a Telegram-to-phone round trip cannot meet a 3-minute trigger.
- So, by the PDF's own 14/14 pass rule, a scalp cannot pass C4 in P0. It is journaled with `sent=false` so its expectancy builds for P1.

Rules, implemented exactly as written for the shadow journal:
- **K1 OR15 Sweep:**
  - M15 bias written (D12 directional) and no H1 veto.
  - A D9 sweep on M5 of the OR15 high/low or of M5 equal highs/lows.
  - An M5 close back through the level.
  - Within 3 M1 bars, an M1 body CHoCH (D3 on M1) or an M1 D5 impulse.
  - Entry at the confirming M1 close.
  - SL = sweep extreme ± $0.80, with SL distance clamped to [$1.20 + S, $3.50]; if the needed SL is > $3.50, skip.
- **K2 Continuation Tap:**
  - The OR15 already broke in the M15 bias direction.
  - A tap of an M5 FVG, micro OB or the OR15 midline, then a directional M5 close, then an M1 micro CHoCH.
  - Not at the extreme of the last 5 M5 bars.
  - At most 2 K2 attempts per window; no K2 while K1 is forming.
- **Risk:** A+ 0.35%, A 0.25%. If ATR(M5) > 1.3 × its 20-bar median, cap at 0.25%.
- **Targets:**
  - TP1 = the closer of 1.2R and the OR15 midline; close 70%. The plan must offer ≥ 1.2R after S; ≥ 1.5R is preferred and recorded.
  - Runner 30% to the far side of OR15 or 2R; trail the M1 swing after +1.2R.
  - BE + $0.40 after a full M1 close at ≥ +1.0R.
- **Time:** at minute 8, exit if < +0.6R; at minute 12, hard exit.
- **Emergency:** an opposite M5 CHoCH, or an M1 bar range > 2 × ATR(M1) against the position.
- **Profit lock:** no new scalp after +2.5R realized in the day.
- **Spread:** no scalp when the session's typical spread is > $0.35 (P1+; P0 assumes S).

## 7. News gate
- **N1 Tier-1 list:** USD events that are FF impact "High", **or** whose title matches `CPI|Non-Farm|NFP|FOMC|Federal Funds|PCE|GDP|Unemployment Claims`. *(MASTER §9 list ∪ EMA §8 list)*
- **N2 Windows (no new signals):**
  - SMC: T−30 to T+20.
  - EMA: T−30 to T+15.
  - Scalp: none for the whole NY scalp window (20:15–22:15) if a release falls inside it.
- **N3 Cancel:** any pending, unfilled signal is CANCELLED_NEWS at T−30 if a tier-1 release falls before its expiry. *(EMA §8, tightened to T−30)*
- **N4 Open trades at T−30 before TP1:**
  - SMC: close 50%.
  - EMA: close 100%.
  - Scalp: close 100%.
  - *(MASTER "reduce or close by engine rule"; the EMA/scalp choices are §12)*
- **N5 SMC re-arm after news:** after T+20, a fresh M15 structural close (a D2 BOS or D3 CHoCH body close) must occur after T+20 before a new SMC signal. *(MASTER §9)*
- **N6 Stale calendar:**
  - If the FF cache is older than 24 h and the static calendar does not cover today, there are no KZ or EMA signals. Fail closed.
  - Tracking of existing signals continues.

## 8. Master checklist (14/14 per signal; any false = not sent) *(MASTER §11)*
| # | Check | Machine test |
|---|---|---|
| C1 | One engine selected | Window engine (E1) ∈ {SMC, EMA}, and the signal's engine equals it |
| C2 | Legal window and time | Inside K1 (SMC) or K3 (EMA); K4/K5 clear |
| C3 | One-sentence bias | Bias string generated (D11 + DOL), ≤ 120 chars |
| C4 | Spread/slippage acceptable | P0 proxy: feed fresh (last M5 ≤ 7 min), basis healthy (spec §2), no dirty-state flag this window |
| C5 | Valid location | SMC: H1 POI on the correct side. EMA: retest #3 in band |
| C6 | Valid liquidity event | SMC: D9 sweep (S1) or pullback-to-CE (S2). EMA: retest count is exactly 3 |
| C7 | Body-close trigger | D3/D4 body close (SMC) or M10 (EMA) |
| C8 | Displacement/FVG when required | D5 + D6 (SMC). Not applicable for EMA = pass |
| C9 | Not chased | SMC: limit, or market rule S1.7. EMA: M12 |
| C10 | Structural SL within limits + buffer | S1.8/9 or M13 |
| C11 | Size from risk tier | risk_pct from SR1 / M17, capped by the profile |
| C12 | Real liquidity target | SMC: L1 ≥ 2R. EMA: M14 room ≥ 1.5R |
| C13 | News/dirty-state clear | N2/N5/N6 clear |
| C14 | Loss limits not hit | §9 and M19 clear |

Grades: A+/A are sent. When 1 mandatory item fails the setup is journaled as **B**; with ≥ 2 failures it is **C**. B and C are never sent.

## 9. Unified risk book & kill rules (all engines, virtual book in P0) *(MASTER §8, §9, §12)*
- **U1 Attempts:** at most 4 per window and **6 per day** (sent signals that filled count as attempts). The **EMA cap of 2/day** (M19) also applies.
- **U2 Window stop:** 2 losses in the same window close that window.
- **U3 Daily loss stop:** no new signals for the rest of the day at **−1.5R realized or −0.8% equity**, whichever comes first. This applies to all engines and is stricter than EMA's −1.0%.
- **U4 Daily profit lock:** at **+2.5R realized**, no new signals of any engine. An open SMC runner continues only if its SL is already at BE or better. *(MASTER: stop scalp, SMC runner only if protected; extended to all new entries, §12)*
- **U5 Consecutive losses:** 4 losses in a row, across engines and sessions, pause everything for 24 h and send an XAU alert.
- **U6 Weekly:** SMC at −5R stops new SMC until next week. EMA at −3.0% realized kills EMA until it is resumed manually (M19).
- **U7 Model kill:** a rolling 30-signal expectancy < 0 pauses that model (S1, S2, EMA, K1, K2 separately) and sends an alert. Paused models still compute and journal (sent=false).
- **U8 Dirty state:** a stale feed (> 7 min with no closed M5 bar inside a window), unhealthy basis, or a data gap invalidates the rest of that window (no new signals). The P1 equivalent is a broker freeze/requote/slip > $0.80.
- **U9 No recovery logic:** size never depends on prior P&L. There is no martingale and no re-entry on the same setup after a loss; a new signal needs a new sequence. *(MASTER §10.8, EMA §7)*
- **U10 Validation gates:**
  - No rule or threshold change before **80 signals per primary MASTER model** and **100 qualified EMA setups**.
  - Any change starts a new cohort, tagged `rules_version`.
  - EMA long and short statistics are kept separate.
  - *(MASTER §12, EMA §12–14)*

## 10. Fill and outcome model (spot-mid bars, S applied; M1 bars for tracking)
- **F1 Long fills.**
  - Buy limit fills when `low_mid ≤ entry − S/2`.
  - Buy stop fills when `high_mid ≥ entry − S/2`. The fill price is the entry, or the bar open if it gapped through (worse).
  - Short is the mirror.
- **F2 Long exits.**
  - The SL is hit when `low_mid ≤ SL + S/2`.
  - A TP is hit when `high_mid ≥ TP + S/2`.
- **F3 Same-bar ambiguity.** If an SL and a TP or fill are both reachable inside one M1 bar, assume the **worst** order: fill then SL.
- **F4** Management rules (SR2–SR4, M18, scalp) are evaluated on bar closes of the timeframe they name.
- **F5** realized_R sums each partial. MAE and MFE are tracked in R from fill to final exit.
- **F6 Statuses:** PENDING → FILLED | EXPIRED | INVALIDATED | CANCELLED_NEWS. FILLED → TP1 → (TP2 | BE | TRAIL | TIME | RUNNER_EMA50 | NEWS_CUT | SL) or SL directly.

## 11. SL/TP in $: worked examples (100 oz/lot contract; lots are informational)
- **SMC long:**
  - Sweep low 3560.00, ATR_H1 12.00 → buffer max(1.80, 1.50) = 1.80; S/2 = 0.15 → SL = 3558.05.
  - Entry (FVG CE) 3565.40 → 1R = $7.35. The cap 1.3 × 12 = 15.6 passes.
  - TP1 = 3565.40 + 14.70 = 3580.10. L1 must be ≥ 3580.10.
  - Risk 0.40% of 10,000 = $40 → lots = 40 / (7.35 × 100) = 0.054 → **0.05**.
- **EMA long:**
  - Confirmation high 3572.10 → entry 3572.30.
  - EMA50 = 3566.00 → 3564.00. Retest-3 low 3565.20 − 0.30 = 3564.90. The farther is 3564.00 → 1R = $8.30.
  - TP1 = 3572.30 + 12.45 = 3584.75. The nearest swing high must be ≥ 3584.75.
  - Risk 0.50% = $50 → 50 / 830 = 0.060 → **0.06**.

## 12. Refinements vs the PDFs (each one tightens a rule or fills a gap; none loosens risk)
| # | PDF text | Refinement | Reason |
|---|---|---|---|
| 1 | "Swing = 2 candles each side" | Confirmed only after the 2nd right candle closes | Prevents look-ahead bias in backtests and live use |
| 2 | Displacement = "impulsive, dominant bodies" | ≥ 2 candles, body ≥ 60%, combined range ≥ 1.0 × ATR, leaves an FVG | Makes it testable |
| 3 | FVG has no size | Minimum max($0.30, 0.10 × ATR) | A gap smaller than the spread is noise |
| 4 | Sweep "closes back" | Within ≤ 2 candles; 2 closes outside = BOS | Separates a sweep from a break |
| 5 | Liquidity pools unspecified | Asia/PDL/London-KZ/M15 swings/equal lows ($0.50 tolerance) | Gives the rule a target set |
| 6 | "Volatility buffer" | max(0.15 × ATR_H1, $1.50) + S/2 | Value from the older SMC skill; exact |
| 7 | SMC trigger | M15 body CHoCH (MASTER) replaces SMC v1.1's M5 MSS | PDF is authoritative |
| 8 | TP1 "internal liq/EQ or 2R" vs gate "first liquidity ≥ 2R" | L1 < 2R → skip; TP1 = 2R | Resolves the internal conflict; never accepts < 2R |
| 9 | "BE + execution buffer" | Entry + $1.00 | Exact value (older SMC skill) |
| 10 | "M15 loses delivery" | No new M15 extreme in the trade direction in 4 bars | Makes the time stop testable |
| 11 | "Wide ATR/news week" | ATR_D1 > 1.3 × 50-day median, or the week has CPI/NFP/FOMC | Makes the 0.35% tier deterministic |
| 12 | Selector "real liquidity ≥ 2R" pre-session | DOL distance ≥ 2 × ATR_H1 proxy at KZ start | R is unknown before a setup; the per-signal 2R gate still applies |
| 13 | Scalp in P0 | Shadow-only (journaled, not sent) | C4 spread and the 3-minute trigger can't be met signal-only |
| 14 | Friday scalp "close early" | Last NY scalp entry 21:45 WIB | Exact time |
| 15 | No SMC Friday rule in v2.0 | No new SMC after Friday 22:30 WIB | Kept from the older skill; tightening |
| 16 | EMA "London/NY active hours" | Only 17:00–20:00 and 23:00–02:00 WIB (outside KZs); none after Friday 23:00 | Honors the one-engine-per-window rule and keeps EMA out of SMC/scalp windows |
| 17 | EMA "fresh crossover" undefined | Must confirm within 60 M15 bars of the cross | An old cross isn't fresh |
| 18 | "Not flat" undefined | 10-bar EMA slope ≥ 0.10 × ATR_M15 in the trend direction | Testable |
| 19 | "Separate from band" | One candle with its low above the band's upper edge | Testable |
| 20 | Retest start/end | Starts at low ≤ EMA20, ends at close > EMA20, invalid at close < EMA50, and a candle must leave the band before the next retest | Testable; matches "multiple candles = one retest" |
| 21 | "Bullish confirmation candle" | Close > open, close > EMA20, close in the upper half of the range | Testable |
| 22 | "Small broker-safe buffer" | $0.20 above the high (below the low for shorts) | Exact |
| 23 | "Parabolic, band flattening" | Close > 3 × ATR from EMA20 since retest 2 **and** EMA20 slope < 50% of its slope at retest 2 | Testable |
| 24 | "Extended far from EMA20" | Confirmation close − EMA20 > 1.0 × ATR_M15 → skip | Testable |
| 25 | EMA pending-order lifetime unspecified | 2 M15 closes / window end / close < EMA50 / recross / news | No stale stop orders |
| 26 | EMA A-grade "clean and shallow" | Each retest ≤ 4 candles and no close < EMA50; EMA gap ≥ 0.25 × ATR | Testable |
| 27 | EMA news cancel "if release occurs before trigger" | Cancel at T−30 | Tightening; matches the no-entry window |
| 28 | Open EMA/scalp trades before news | Close 100% before TP1 at T−30 | MASTER defers to the engine rule; EMA doesn't define one; the conservative choice |
| 29 | Daily stop EMA −1.0% vs MASTER −0.8% / −1.5R | The unified −0.8% / −1.5R binds all engines; the EMA-only stop is 2 losses / −1.0% | Strictest rule wins |
| 30 | Profit lock +2.5R "stop scalp" | Stop all new signals | Tightening; MASTER already implies SMC runners only |
| 31 | No cross-engine concurrency rule | One live XAU signal at a time | Tightening; prevents stacked exposure and "recovery" trades |
| 32 | "Spread acceptable" with no broker feed | P0 proxy = feed freshness + basis health | P0 has no live spread; scalp stays unsent for this reason |
| 33 | "20 pips" | $2.00 (1 pip = $0.10) | The EMA PDF says to convert per broker; the profile overrides at P1 |
| 34 | Day/week boundaries | 17:00 New York (DST-aware) | Matches the gold session close and PDH/PDL |
| 35 | DXY (older SMC v1.1 grade-A requirement) | Logged as an informational field only, not a gate | Not in MASTER v2.0; revisit after 80 trades |
| 36 | Double-sweep rule (older SMC v1.1) | First/second sweep logged as a tag only | Not in MASTER v2.0 |
| 37 | v1.1 "max 2 SMC/day" | Dropped; MASTER U1 (4/window, 6/day) applies | PDF supersedes; one-live-signal (31) already limits |
