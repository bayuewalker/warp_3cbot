# Dedicated Trading Desk

Open `/dashboard#desk` from the sidebar, mobile navigation or Overview. The page opens at the same destination when an operator signs in through its direct link. Overview keeps account KPIs, the pipeline and existing controls.

The room uses an original generated environment with six interactive WARP stations. Each station opens its telemetry detail. OBSERVE, HALT and connection status remain visible in the shared header. Text-only mode hides the set and illustrations. Existing reduced-motion and evidence requirements govern workstation animations.

## Data on the desk

| Display | Source | Freshness and fallback |
| --- | --- | --- |
| Market screen | Existing `/api/prices` observation | Uses the reported quote clock, freshness limit and P1 skew tolerance. A retained quote carries its stale or error label and its trace is hidden. Absent prices remain unavailable. |
| Session trace | Up to 60 distinct source times per currently listed symbol | Plots received Binance quotes after two valid samples. Repeated or older source timestamps do not append. History is local to the mounted desk. No candle history is assumed. |
| Six stations and KPIs | Existing P1 telemetry adapter and P3 room | Missing balances, positions or readings remain labelled. Confirmed zeros remain visible. |
| Latest ranked research | First card in the worker's `/api/opportunities` sequence | Shows the worker verdict and published text. Positive freshness requires both card clocks and a current worker publication. Full opportunity evidence is available from the Signals link. |
| Worker events | `/api/events` | Lists recent events with server timestamps and refresh status. |

## QA

Base `b548838`. Local Chromium used a temporary authenticated OBSERVE worker without broker credentials. The visual captures show that startup state. The price trace was tested with isolated local stream fixtures.

| Check | Output |
| --- | --- |
| Frontend | `npm ci`, TypeScript and Vite passed. Node reported 28 tests passed. |
| Python | 148 tests passed with shell proxy variables unset. |
| Ruff | All checks passed. |
| Browser | Account reads, telemetry, shell, room and dedicated desk suites passed. Verified direct authenticated navigation, shared transport, no desk mutations, price-state transitions and all six details. |
| Responsive matrix | 140 layouts, 0 with horizontal overflow, 0 page errors. Covers seven widths and both languages. |

The assets are served locally. The backdrop is approximately 117 kB. No production price feed, physical device or authenticated production deployment was used for QA.
