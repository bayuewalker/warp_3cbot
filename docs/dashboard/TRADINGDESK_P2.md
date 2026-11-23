# P2 shared shell and responsive QA

The baseline is the PR #20 merge, `5f6ff71f08cb34d65779f7de87165d512e191916`. P2 changes the client shell, Overview composition, visual tokens and browser evidence. It uses the existing P1 read model and authenticated routes.

## What changed

- The sticky command bar groups mode with the brand or desktop breadcrumb. Alerts, language and sign-out keep separate 44 px targets. A second row shows HALT, execution permission, worker publication age and stream connection.
- Worker age uses the source timestamp and P1 clock tolerance. A recent browser receipt cannot refresh it. Positive execution permission requires a current publication and explicit permission in a reported PAPER or LIVE mode. A retained HALT stays visible across routes and above the open alert panel. These are display decisions.
- Five hash destinations retain their labels and history. The rail appears above 900 px. At 900 px and below, bottom navigation reserves the safe area. The mobile preset preview scrolls in normal flow, keeping Save reachable.
- Overview starts with six compact P1 role readings and four metrics. Operators can inspect evidence, open opportunities, check risk and navigate to accounts. Original agent artwork and interactive workstations belong to P3.

## Executed checks

| Check | Result |
| --- | --- |
| `PYTHONPATH=src python -m unittest discover -s tests` | 148 passed |
| `uvx ruff check .` | All checks passed |
| `cd web && npm ci && npx tsc --noEmit && npx vite build && npm test` | Build succeeded. 22 frontend tests passed. Final bundles were 88.39 kB JS and 9.91 kB CSS gzipped. |
| `python web/qa/account_reads.py` | Browser regressions passed. Broker-read failures, retained rows and session expiry remain covered. |
| `python web/qa/telemetry.py` | Browser regressions passed. SSE reconnect, timed REST fallback and source evidence remain covered. |
| `python web/qa/shell.py` | Browser regressions passed. Checks cover 900/901 px navigation, all five routes, keyboard focus, 44 px navigation and toolbar targets, HALT with alerts open in EN/ID, stale and unknown clocks, preset preview and save, screener filter persistence after worker publication, HALT confirmation and sign-out. |
| `python web/qa/account_reads.py --layout /tmp/p2-delivery-layout` | 126 layouts, 0 with horizontal overflow, 0 page errors. The same run on the base had 18 failures, all 28 px wide at 320 px. The right edge of the logout button reached x=348. |

The layout matrix covers nine views in English and Indonesian at 320, 360, 390, 768, 1024, 1440 and 1920 CSS pixels. The shell suite checks all four KPI cards fit above the bottom navigation at 320 and 390 px. The five sampled status and role text colors range from 8.68:1 to 12.17:1 against their specified surfaces.

## Screenshots

| Viewport | Merged P1 | P2 |
| --- | --- | --- |
| 320 × 844 | [Before](screenshots/p2/before-320.webp) | [After](screenshots/p2/after-320.webp) |
| 390 × 844 | [Before](screenshots/p2/before-390.webp) | [After](screenshots/p2/after-390.webp) |
| 1440 × 960 | [Before](screenshots/p2/before-1440.webp) | [After](screenshots/p2/after-1440.webp) |

Screenshots use actual local OBSERVE startup reads with an empty journal and no broker credentials. The test process injects status faults only after capturing screenshots. Set `PYTHONPATH=src` and `CHROMIUM_PATH` to run browser checks. Set `WARP_QA_SCREENSHOTS` to capture the P2 views.

Physical Android, assistive technology and production broker connectivity remain unverified. All browser tests ran in Chromium. Production deployment requires owner review.
