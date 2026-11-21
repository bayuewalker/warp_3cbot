# WARP 3C dashboard design system

The desk prioritizes a decision an operator can make from each screen. Overview leads with account marks and an observed pipeline. Opportunities keeps the worker’s blended-confidence order. Bots & positions separates journal activity from preset editing. Screener keeps ranking above filters. Settings & risk groups account selection, budget controls, policy and runtime configuration.

| Token | Value | Usage |
| --- | --- | --- |
| Background | `#0A0A0F` | Workspace canvas |
| Surface | `#111827` | KPI panels; related panels use the darker `#101620` |
| Primary | `#3B82F6` | Navigation, monitoring accents and focus family |
| Success | `#10B981` | Healthy, passed and fresh observations |
| Warning | `#F97316` | Caution family, paired with state text |
| Danger | `#EF4444` | HALT, blocked stages and destructive actions |
| Muted | `#94A3B8` | Metadata, units and provenance |

Use the system sans stack for commands and headings. Use the system monospace stack for marks, time and status codes. Cards use a single-pixel border, 12px radius and 12–20px padding. Standard buttons have a 44px height. The focus outline and text labels carry meaning independently of color. Motion respects the operating system’s reduced-motion preference. The app fetches no external fonts.

## Components and states

- `Card`, `Badge`, `Button`, `Field`, `Loading` and `Problem` live in `web/src/ui.tsx`. Status badges combine a dot with text. A visible label names each editable field. Confirmation fields identify their target and require the established action word.
- `useLoad` aborts unmounted reads, schedules the next monitoring poll 30 seconds after the last read finishes and supports retry. Forms own their drafts. A 401 from an authenticated API returns the operator to sign-in.
- Preserve a successful zero. Show a dash for missing money, counts or timestamps. A failed read offers retry. The browser receipt time is local to the browser. Signal and evidence timestamps come directly from the worker.
- Each pipeline row opens by keyboard or touch to expose the stage’s timestamp, freshness and counts. Unpublished values remain explicit. HALT stays visible in the header across all pages.
- A TradingView chart opens only after the operator follows its link. Its Binance symbol is a chart reference. Per-card exchange identity and numeric risk score are unavailable in the #6 card contract. The worker’s rubric score has a separate label.
- Broker actions bind to the selected WARP record, supported cabinet product, observed PAPER mode and matching active demo spot account. Resume also requires HALT to be clear. The server still owns policy and ownership decisions. PAPERCHECK retains the typed confirmation and is disabled outside eligible paper mode.

## Responsive behavior

Above 900px, navigation stays in a persistent left rail. At 900px and below, five bottom links retain their labels and an active-page marker. At 650px and below, KPIs use two columns and monitoring panels use one. Tables retain their columns in a named, keyboard-focusable scroll region. The bottom inset reserves space for the mobile browser’s safe area.

## Review captures

All six images show the same authenticated local observe-mode startup snapshot, with an empty journal and no account keys. Values are the actual dispatcher response.

| Viewport | Baseline | Redesign |
| --- | --- | --- |
| 390 × 844 | [Mobile before](screenshots/before-390.png) | [Mobile after](screenshots/after-390.png) |
| 768 × 1024 | [Tablet before](screenshots/before-768.png) | [Tablet after](screenshots/after-768.png) |
| 1440 × 960 | [Desktop before](screenshots/before-1440.png) | [Desktop after](screenshots/after-1440.png) |
