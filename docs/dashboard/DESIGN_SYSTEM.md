# WARP 3C dashboard design system

The desk prioritizes a decision an operator can make from each screen. Overview leads with the scanner output and the entry gate, followed by account marks and pipeline details. Opportunities keeps the worker’s blended-confidence order. Bots & positions separates journal activity from preset editing. Screener keeps ranking above filters. Settings & risk groups account selection, budget controls, policy and runtime configuration.

| Token | Value | Usage |
| --- | --- | --- |
| Background | `#0A0C0E` | Workspace canvas |
| Surface | `#14191E` | KPI panels; related panels use the darker `#101417` |
| Primary | `#26B8EB` | Navigation, monitoring accents and focus family |
| Success | `#53E1BA` | Healthy, passed and fresh observations |
| Warning | `#FFAB70` | Caution family, paired with state text |
| Danger | `#FF938A` | HALT, blocked stages and destructive actions |
| Muted | `#A6B3BD` | Metadata, units and provenance |

Space Grotesk identifies headings, Inter carries body text, and JetBrains Mono sets times and counts. Each Latin font is self-hosted with its license. Cards use a single-pixel border, 12px radius and 12–20px padding. Standard buttons have a 44px height. The focus outline and text labels carry meaning independently of color. Motion respects the operating system’s reduced-motion preference. The app fetches no external fonts.

## Components and states

- `Card`, `Badge`, `Button`, `Field`, `Loading` and `Problem` live in `web/src/ui.tsx`. Status badges combine a dot with text. A visible label names each editable field. Confirmation fields identify their target and require the established action word.
- `useLoad` aborts unmounted reads, schedules the next monitoring poll 30 seconds after the last read finishes and supports retry. Forms own their drafts. A 401 from an authenticated API returns the operator to sign-in.
- Preserve a successful zero. Use “Not reported” for absent account values and a dash for unpublished scanner counts or timestamps. A failed read offers retry. The browser receipt time is local to the browser. Signal and evidence timestamps come directly from the worker.
- Each pipeline row opens by keyboard or touch to expose the stage’s timestamp, freshness and counts. Unpublished values remain explicit. HALT stays visible in the header across all pages.
- A TradingView chart opens only after the operator follows its link. Its Binance symbol is a chart reference. Per-card exchange identity and numeric risk score are unavailable in the #6 card contract. The worker’s rubric score has a separate label.
- Broker actions bind to the selected WARP record, supported cabinet product, observed PAPER mode and matching active demo spot account. Resume also requires HALT to be clear. The server still owns policy and ownership decisions. PAPERCHECK retains the typed confirmation and is disabled outside eligible paper mode.

## Responsive behavior

The mode, bell and language controls share a 28px visual height. Interactive header controls have 44px touch targets. The bell summarizes the current worker state. A sticky HALT strip remains visible while the operator scrolls. The scanner dial shows the number of AI outputs. Its fixed outline has no percentage scale.

Above 900px, navigation stays in a persistent left rail. At 900px and below, five bottom links retain their labels and an active-page marker. At 650px and below, KPIs use two columns and monitoring panels use one. Tables retain their columns in a named, keyboard-focusable scroll region. The bottom inset reserves space for the mobile browser’s safe area.

## Review captures

All six images show the same authenticated local observe-mode startup snapshot, with an empty journal and no account keys. Values are the actual dispatcher response.

| Viewport | Baseline | Redesign |
| --- | --- | --- |
| 390 × 844 | [Mobile before](screenshots/before-390.png) | [Mobile after](screenshots/after-390.png) |
| 768 × 1024 | [Tablet before](screenshots/before-768.png) | [Tablet after](screenshots/after-768.png) |
| 1440 × 960 | [Desktop before](screenshots/before-1440.png) | [Desktop after](screenshots/after-1440.png) |
