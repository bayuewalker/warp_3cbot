# Dedicated Trading Desk

Open `/dashboard#desk` from the desktop rail or the Overview link. The mobile bottom navigation keeps the five P2 destinations. A direct Desk link survives sign-in. Overview keeps account KPIs, the pipeline and existing controls.

The trading floor is an original cyberpunk office interior with a rain-lit city window and industrial consoles for six seated robot agents. Tap a desk nameplate to inspect the corresponding worker evidence. The illustration is static. The adjacent status list supplies the actual agent state and opens the same detail from the keyboard. On phones, the market screen and status list sit below the office. OBSERVE, HALT and connection status remain visible in the shared header. Text-only mode hides the set and illustrations. Existing reduced-motion and evidence requirements govern workstation animations.

## Data on the desk

| Display | Source | Freshness and fallback |
| --- | --- | --- |
| Market screen | Existing `/api/prices` observation | Uses the reported quote clock, freshness limit and P1 skew tolerance. A retained quote carries its stale or error label and its trace is hidden. Absent prices remain unavailable. |
| Session trace | Up to 60 distinct source times per currently listed symbol | Plots received Binance quotes after two valid samples. Repeated or older source timestamps do not append. History is local to the mounted desk. No candle history is assumed. |
| Six stations and KPIs | Existing P1 telemetry adapter and P3 room | Missing balances, positions or readings remain labelled. Confirmed zeros remain visible. |
| Latest ranked research | First card in the worker's `/api/opportunities` sequence | The worker publishes `expires_at` from the older of `as_of` and `evidence_as_of` plus `WARP_MAX_SIGNAL_AGE_MINUTES`. Desk requires both clocks, the worker's freshness flag and a current publication. It expires retained research at the deadline, including while SSE heartbeats continue. A legacy card with no deadline has unknown freshness. The Signals link opens the P4A evidence view. |
| Worker events | `/api/events` | Lists recent events with server timestamps and refresh status. |

## Backdrop provenance

Codex generated the replacement `web/src/tradingdesk/art/control-room.webp` on 2026-11-23 using OpenAI ImageGen. The text brief described a rain-lit office, two rows of three original robot workstations and their placement based on the app's clickable desk grid. Generation used that brief alone, with no Vembit artwork, sprites or composition as input. Codex resized the 1254 × 1254 source PNG to 1152 × 1152 and encoded it with ImageMagick WebP quality 84.

| File | SHA-256 |
| --- | --- |
| Source PNG, `exec-3faab780-ba36-4182-a03a-34bcae2229ce.png` in the review evidence | `536bd7ed51e4db2916a93a08f73b6cc9818e22e306f358b8d3f0151f36c7f15c` |
| Repository WebP | `73c2c58293c1f922cd1437053b34fda3523ccc1819bd2f6193a25151ad02437c` |

Under the OpenAI Terms of Use, OpenAI assigns its rights, if any, in the generated output to the account holder to the extent permitted by applicable law. The backdrop was created for use in this repository. This provenance record grants no separate public license. [OpenAI Terms of Use](https://openai.com/policies/terms-of-use/)

## QA

Base `81fd1f9` includes P4A. Local Chromium uses an authenticated OBSERVE worker with a temporary SQLite journal. Screenshots capture its startup state, and isolated stream fixtures supply quotes and research for the regression tests.

| Check | Output |
| --- | --- |
| Frontend | `npm ci`, TypeScript, Vite and 36 frontend tests. |
| Python | 148 tests passed with shell proxy variables unset. |
| Ruff | All checks passed. |
| Browser | Account reads, telemetry, shell, room, P4A opportunities and dedicated desk. The dedicated desk checks sign-in, quote loss, local research expiry during live SSE, zero mutations, six telemetry dialogs and 44 px touch targets. |
| Responsive matrix | 140 layouts, 0 with horizontal overflow, 0 page errors. Covers seven widths and both languages. |

The assets are served locally. The backdrop is approximately 260 kB.
