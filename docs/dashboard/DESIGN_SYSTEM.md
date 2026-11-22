# WARP 3C dashboard design system

The desk prioritizes a decision an operator can make from each screen. Overview leads with the scanner output and the entry gate, followed by account marks and pipeline details. Opportunities keeps the worker’s blended-confidence order. Bots & positions separates journal activity from preset editing. Screener keeps ranking above filters. Settings & risk groups account selection, budget controls, policy and runtime configuration.

The visual language follows [WalkerMind OS](https://bayuewalker.github.io/walkermind-os/): near-black canvas, blue→green gradient accents, Space Grotesk headings, mono uppercase eyebrows, HUD-style stat strips and thin gradient rules under the sticky nav.

| Token | Value | Usage |
| --- | --- | --- |
| Background | `#0A0A0A` | Workspace canvas, with faint blue/green radial glows and scanlines |
| Panel / surface | `#0E1014` / `#14181F` | Cards, HUD cells, nav backgrounds |
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
- `useLoad` aborts unmounted reads, schedules the next monitoring poll 30 seconds after the last read finishes and supports retry. Forms own their drafts. A 401 from an authenticated API returns the operator to sign-in.
- Preserve a successful zero. A null value is shown as “Not reported” or “Unavailable”, never as 0. Numbers use thousands separators and size-appropriate decimals (`web/src/format.ts`). Every time on the desk is browser-local with its zone label (for example `07:21:49 GMT+7`); the page eyebrow states the zone. The only UTC reference is the PnL day boundary, which is labelled as such.
- Each pipeline row opens by keyboard or touch to expose the stage’s timestamp, freshness and counts. Unpublished values remain explicit. HALT stays visible in the header across all pages.
- A TradingView chart opens only after the operator follows its link. Its Binance symbol is a chart reference. Per-card exchange identity and numeric risk score are unavailable in the #6 card contract. The worker’s rubric score has a separate label.
- Broker actions bind to the selected WARP record, supported cabinet product, observed PAPER mode and matching active demo spot account. Resume also requires HALT to be clear. The server still owns policy and ownership decisions. PAPERCHECK retains the typed confirmation and is disabled outside eligible paper mode.

## Responsive behavior

The live-stream badge collapses to its dot on phones; its label stays available to screen readers. Interactive header controls have 44px touch targets. The bell summarizes the current worker state. A sticky HALT strip remains visible while the operator scrolls. The scanner dial shows the number of AI outputs. Its fixed outline has no percentage scale.

Above 900px, navigation stays in a persistent left rail. At 900px and below, five bottom links retain their labels and an active-page marker. At 650px and below, KPIs use two columns and monitoring panels use one. On phones every `data-table` (screener, forward test) turns into one labelled card per row, signal metrics and settings rows stack, and section tabs wrap, so nothing scrolls sideways at 360px. The bottom inset reserves space for the mobile browser’s safe area.

## Review captures

All six images show the same authenticated local observe-mode startup snapshot, with an empty journal and no account keys. Values are the actual dispatcher response.

| Viewport | Baseline | Redesign |
| --- | --- | --- |
| 390 × 844 | [Mobile before](screenshots/before-390.png) | [Mobile after](screenshots/after-390.png) |
| 768 × 1024 | [Tablet before](screenshots/before-768.png) | [Tablet after](screenshots/after-768.png) |
| 1440 × 960 | [Desktop before](screenshots/before-1440.png) | [Desktop after](screenshots/after-1440.png) |
