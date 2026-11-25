# WARP 3C dashboard design system

The desk prioritizes a decision an operator can make from each screen. Overview starts with a compact six-role trading room and four account metrics. Source evidence, scanner and risk controls, positions and capital, and activity follow. Opportunities keeps the worker’s blended-confidence order. Bots & positions separates journal activity from preset editing. Screener keeps ranking above filters. Settings & risk groups account selection, budget controls, policy and runtime configuration.

The visual language follows [WalkerMind OS](https://bayuewalker.github.io/walkermind-os/): near-black canvas, blue→green gradient accents, Space Grotesk headings, mono uppercase eyebrows, HUD-style stat strips and thin gradient rules under the sticky nav.

| Token | Value | Usage |
| --- | --- | --- |
| Background | `#0A0A0F` | Workspace canvas, with faint blue/green radial glows and scanlines |
| Panel / surface | `#0E1014` / `#14181F` | Cards, HUD cells, nav backgrounds |
| Command bar / role labels | `#10131A` / `#BFA8FC` | Sticky controls and trading room labels |
| Border | `#1D232C` / `#2A323E` | Card and control outlines |
| Primary | `#00A8FF` | Navigation, eyebrows, focus, links |
| Success | `#00D4AA` | Healthy, fresh, rising prices; second stop of the brand gradient |
| Warning | `#FFB547` | Caution states, paired with state text |
| Danger | `#FF6B6B` | HALT, blocked stages, falling prices, destructive actions |
| Muted | `#A8B1BD` (`#8A95A3` for labels) | Metadata; both keep WCAG AA contrast on every surface |

Space Grotesk sets headings and large figures, Inter carries body text, and JetBrains Mono sets times, prices and uppercase labels. Each Latin font is self-hosted with its license; the app fetches no external fonts. Body text is at least 14px on phones; uppercase labels are 12px. Cards use a single-pixel border and 10px radius. Buttons, tabs and header controls have 44px targets. Motion respects reduced-motion.

## Components and states

- `Card`, `CardHead`, `Badge`, `Button`, `Field`, `Choice`, `Note`, `KV`, `Empty`, `Loading` and `Problem` live in `web/src/ui.tsx`. Every page builds from these and the shared classes in `web/src/index.css` (one stylesheet: tokens, corner-tick cards, `card-head`, `kv` rows, `data-table`, `form-section`, `choice`, `form-note`, `hud-strip`). Avoid page-specific Tailwind colours; add a shared class instead.
- Rule and configuration keys show a plain-language name from `web/src/labels.ts`, with the raw key and a readable value (for example `0.25 · 25%`) as a hint. Status badges combine a dot with text. A visible label names each editable field. Confirmation fields identify their target and require the established action word.
- `useLoad` aborts unmounted reads, schedules disconnected monitoring polls five seconds after the last read finishes and supports retry. Forms own their drafts. A 401 from an authenticated API returns the operator to sign-in.
- Preserve a successful zero. A null value is shown as “Not reported” or “Unavailable”, never as 0. Numbers use thousands separators and size-appropriate decimals (`web/src/format.ts`). Every time on the desk is browser-local with its zone label (for example `07:21:49 GMT+7`); the page eyebrow states the zone. The only UTC reference is the PnL day boundary, which is labelled as such.
- Each pipeline row opens by keyboard or touch to expose the stage’s timestamp, freshness and counts. Unpublished values remain explicit. HALT stays visible in the header across all pages.
- A TradingView chart opens only after the operator follows its link. Its Binance symbol is a chart reference. Per-card exchange identity and numeric risk score are unavailable in the #6 card contract. The worker’s rubric score has a separate label.
- Broker actions bind to the selected WARP record, supported cabinet product, observed PAPER mode and matching active demo spot account. Resume also requires HALT to be clear. The server still owns policy and ownership decisions. PAPERCHECK retains the typed confirmation and is disabled outside eligible paper mode.

## Live updates

Every panel reads through `useLoad(path, true)`: it applies frames from the authenticated `/api/stream` and polls every 5s only while the stream is down; the stream reconnects with backoff. Forms (rules, bot preset, budget draft) keep their drafts; the budget draft resets only when the saved values change. Each section shows "Updated Xs ago" and a stale warning past its expected cadence.

| Source | Refresh | Stream event |
| --- | --- | --- |
| Binance tape prices | 3s, worker thread | `prices` |
| Status, risk, HALT, opportunities, bots, forward, events | every worker loop (≤3s) | `snapshot` |
| Screener (TradingView) | 15s | `snapshot` |
| Signals / AI analysis | `WARP_SCAN_SECONDS` 300s, `WARP_RESEARCH_SECONDS` 900s | `snapshot` |
| 3Commas accounts and balances | 30s, read-only `DashboardReads` thread | `accounts` (+ `snapshot`) |
| Open positions on every bot per account | 60s on the same thread (GET bot lists, then GET `/open_api/{dca,grid}_bots/:code/positions` only for bots reporting open positions, max 40) | `positions` (+ `snapshot`) |
| Marks for holdings and positions outside the tape | 30s, Binance public `ticker/price`, valid 90s | inside `accounts` / `positions` |

Position rows carry their source: price and PnL from 3Commas when reported, otherwise from the Binance mark, labelled "calculated". Fields 3Commas omits show "Not reported". Rows from bots WARP did not create carry an External badge and are display-only.

## Responsive behavior

The command bar groups brand and mode alongside alerts, language and sign-out. A second row displays HALT, execution permission, worker age and stream status in text at every width. The worker age uses the P1 five-second forward clock allowance and 15-minute publication threshold. Interactive header controls have 44px touch targets. The bell summarizes the current worker state. A sticky HALT strip remains visible while the operator scrolls. The scanner dial shows the number of AI outputs. Its fixed outline has no percentage scale.

Above 900px, navigation stays in a persistent left rail. At 900px and below, five bottom links retain their labels and an active-page marker. At 650px and below, KPIs use two columns and monitoring panels use one. On phones every `data-table` (screener, forward test) turns into one labelled card per row, signal metrics and settings rows stack, and section tabs wrap, so nothing scrolls sideways at 320px. Safe-area insets reserve space around header controls and the bottom bar. Mobile preset previews stay in normal flow so the operator can scroll the Save button clear of the navigation.

P2 shell comparisons and interaction results are in [the P2 QA report](TRADINGDESK_P2.md).

## Review captures

The historical captures below show the same authenticated local observe-mode startup snapshot, with an empty journal and no account keys. Values are the actual dispatcher response.

| Viewport | Baseline | Redesign |
| --- | --- | --- |
| 390 × 844 | [Mobile before](screenshots/before-390.png) | [Mobile after](screenshots/after-390.png) |
| 768 × 1024 | [Tablet before](screenshots/before-768.png) | [Tablet after](screenshots/after-768.png) |
| 1440 × 960 | [Desktop before](screenshots/before-1440.png) | [Desktop after](screenshots/after-1440.png) |

## Tokens as shipped through P4C (P5 audit)

Tokens live in the `:root` block of `web/src/index.css`. Contrast ratios below are computed from those hex values by `web/p5-contrast.test.ts` (WCAG 2.x relative luminance), so a palette edit fails the test instead of drifting. Every text and accent token meets AA (4.5:1) on every surface.

| Token | Value | `--bg` | `--bg2` | `--bg3` | `--bg4` | `--command-surface` |
| --- | --- | --- | --- | --- | --- | --- |
| `--text` | `#FFFFFF` | 19.8 | 19.0 | 17.8 | 16.5 | 18.6 |
| `--text2` | `#DFE4EA` | 15.4 | 14.9 | 13.9 | 12.9 | 14.5 |
| `--muted` | `#A8B1BD` | 9.1 | 8.8 | 8.2 | 7.6 | 8.6 |
| `--muted2` (labels) | `#8A95A3` | 6.5 | 6.3 | 5.9 | 5.4 | 6.1 |
| `--primary` | `#00A8FF` | 7.6 | 7.3 | 6.8 | 6.3 | 7.1 |
| `--primary-2` (focus ring) | `#29C0FF` | 9.5 | 9.1 | 8.5 | 7.9 | 8.9 |
| `--success` | `#00D4AA` | 10.3 | 10.0 | 9.3 | 8.7 | 9.7 |
| `--success-2` | `#2EE8C0` | 12.6 | 12.2 | 11.4 | 10.6 | 11.9 |
| `--warning` | `#FFB547` | 11.2 | 10.8 | 10.1 | 9.4 | 10.6 |
| `--danger` | `#FF6B6B` | 7.1 | 6.9 | 6.4 | 6.0 | 6.7 |
| `--danger-2` | `#FF8F8F` | 9.0 | 8.7 | 8.1 | 7.5 | 8.5 |
| `--agent-accent` (room role labels) | `#BFA8FC` | 9.6 | 9.3 | 8.7 | 8.0 | 9.0 |

Surfaces: `--bg` `#0A0A0F`, `--bg2` `#0E1014`, `--bg3` `#14181F`, `--bg4` `#1A1F28`, `--command-surface` `#10131A`. Lines (`--line` `#1D232C`, `--line2` `#2A323E`, `--line3` `#3A4452`) are decoration and never carry text. Other tokens: `--touch` 44 px, `--radius` 10 px, `--radius-sm` 6 px, `--display`, `--body` and `--mono` font stacks. The comment beside `--muted` in the stylesheet says 8.3:1 on `--bg3`. The measured value is 8.2:1, which is rounding and does not change the verdict.

Focus is one rule for the whole app: `:focus-visible { outline: 2px solid var(--primary-2); outline-offset: 2px; }`. The accessibility audit found a visible indicator on all 1,931 keyboard stops it visited.

## Components as shipped through P4C

| Component | File | Notes |
| --- | --- | --- |
| `Card`, `CardHead`, `Badge`, `Button`, `Field`, `Choice`, `Note`, `KV`, `Empty`, `Loading`, `Problem` | `web/src/ui.tsx` | Shared primitives. Pages add no private colours. |
| Command shell: command bar, status strip (HALT, Execution, Worker age, Stream), persistent rail above 900 px, five-link bottom nav at 900 px and below | `web/src/tradingdesk/Shell.tsx` | Six rail items, five bottom items. The status strip shows HALT as "off" when clear, Execution as "Blocked" in OBSERVE and as "Execution permitted" only for PAPER or LIVE with a current snapshot, HALT clear and `execution_permitted` true. A frame the shell cannot read shows "Not reported". |
| Stream badge | `Shell.tsx`, `web/src/live.ts` | "Live" only after a valid hello. "Reconnecting" otherwise. Reconnect backs off `min(30 s, 1 s x 2^attempt)`. |
| Incident strip | `web/src/tradingdesk/IncidentStrip.tsx` | `data-incident` is one of `stream`, `statusStale`, `statusFailed`, `halt`, `risk`, `provider`. Links to the Risk tab. Hidden when there is no incident. |
| Trading room | `web/src/tradingdesk/TradingRoom.tsx`, `room.css` | Six stations. Motion is gated by `data-ambient` and `data-recent` and by `prefers-reduced-motion`. Animations touch only `opacity`, `transform` and `rotate`. A station opens a keyboard-operable drawer that returns focus to its trigger. |
| Evidence drawer ("telemetry") | `web/src/tradingdesk/Evidence.tsx` | One row per source, with its state (Confirmed, Confirmed empty, Awaiting worker, Invalid snapshot, stale, partial, loading, unauthorized) and time. |
| Opportunities | `opportunities.tsx`, `opportunity-model.ts` | Cards read `PROPOSED / NOT SUBMITTED`. No execution control exists on a proposal. |
| Accounts and positions | `accounts-model.ts` and `web/src/desk.tsx` | Separate stale, partial and mark badges. "Not reported" for nulls. Counts withheld on an incomplete book. External bots carry an External badge and have no controls. |
| Activity timeline, Risk and health | `Activity.tsx`, `activity-model.ts` | `React.lazy` chunk (7.92 kB). Events, alerts, journal, signals and breakers in bounded windows. The journal is untimed because its `created_at` has no zone. Risk badge texts: "No incident reported", "Incident", "Not verified", "Awaiting fresh snapshot". |
| OBSERVE chip | `activity-model.ts` `observeGate` | "Observe mode · entries blocked by design". Neutral, not an incident, and the health level is never "clear" while it applies. |

## Breakpoints

Styles switch at 1100, 900 (rail becomes the bottom nav), 650 (tables become cards, KPIs go to two columns), 520, 480, 380 and 359 px, and at 901 px and above for the persistent rail. P5 added Pixel-class 412 px, Galaxy-class 360 px, desktop 1280 and 2560 px, 200% browser zoom and 400% reflow to the audit. All 344 layouts have no horizontal overflow. See [TRADINGDESK_P5.md](TRADINGDESK_P5.md#responsive-and-accessibility).

## Known design findings (P5, not fixed)

- **F-P5-1.** At 200% text size in Indonesian at 390 px the status strip is 6 px too wide, its labels overlap and bottom-nav labels break mid-word. See [the capture](screenshots/p5/p5-text-200pct-390-overview-en.webp).
- **F-P5-2.** At 200% text size and 1024 px, the Activity tab overflows its container (EN 1 px, ID 80 px).
- Touch targets, accessible names and text contrast had no findings in the audit.

## P5 evidence captures

Same fixtures, reduced motion and masked clock on both sides. Product source is unchanged in P5, so the main build and this branch render identical pixels.

| Viewport | Main `91d3c38` | P5 |
| --- | --- | --- |
| 390 x 844 | [before](screenshots/p5/before-main-vs-p5-390.webp) | [after](screenshots/p5/after-main-vs-p5-390.webp) |
| 1440 x 960 | [before](screenshots/p5/before-main-vs-p5-1440.webp) | [after](screenshots/p5/after-main-vs-p5-1440.webp) |

