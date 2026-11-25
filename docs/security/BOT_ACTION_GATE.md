# Server-side gate on `POST /api/warp-bots/action`

The route pauses, resumes, closes or deletes one journaled WARP bot. Before this gate only the UI (`desk.tsx` `allowed()`) limited it to paper mode on a demo spot account. The server now enforces the same rule in `_bot_action_gate` (`src/warp3c/httpapi.py`).

The gate only adds refusals. Every refusal returns `places_orders: false` and makes no `get_*_bot` call and no broker write. The only broker call it can cause is the existing read-only account refresh (`broker.list_accounts`).

## Order

1. Auth, JSON parse, typed confirm word (`PAUSE`, `RESUME`, `CLOSE`, `DELETE`). Unchanged, `400`.
2. Mode: `cfg.mode != "paper"` -> `403 paper_mode_required` (also returns `mode`). Covers observe, live and unknown.
3. Journal row: missing, not a `WARP-<id>` row -> `403 not_owned` (same reason ownership uses, existence is not leaked).
4. Account: refresh read-only, then resolve `row.account_code` in `engine.last_accounts`.
5. Product and state.
6. HALT check for `resume` -> `409 halt`. Unchanged.
7. `apply_action` unchanged (remote name and code ownership check, then the write).

## Reason codes

| Status | `error` | When |
| --- | --- | --- |
| 400 | `confirmation_required` | wrong or missing confirm word (unchanged) |
| 403 | `paper_mode_required` | mode is not `paper` |
| 403 | `not_owned` | no journal row, row is not a WARP bot, or no `external_code` |
| 403 | `account_unavailable` | refresh failed or did not happen, accounts list error, or the account row is incomplete |
| 403 | `account_not_found` | not exactly one account with the bot's `account_code` |
| 403 | `demo_account_required` | account is not demo |
| 403 | `spot_only` | market is not `spot` |
| 403 | `inactive` | account status is not `active` |
| 403 | `unsupported_product` | product is not `dca_bot` or `grid_bot` |
| 409 | `already_deleted` | journal status is `deleted` |
| 409 | `halt` | `resume` while the HALT file exists (unchanged) |
| 403 | `foreign_bot` | remote name or code does not match the journal (unchanged, from `apply_action`) |

Fail closed: an unknown mode, a missing or duplicated account, an account list error, a stale refresh and a missing bot row are all refusals.

## UI and server now match

`allowed()` in `desk.tsx` requires paper mode, a demo, spot, active account, a bot with `external_code` and product `dca_bot` or `grid_bot`, status not `deleted`, and no HALT for resume. The server enforces each of those. The UI also hides the buttons on any list or accounts read error; the server refuses the same case as `account_unavailable`.

Tests: `tests/test_bot_action_gate.py` and `KnownGapTests.test_P0_warp_bot_action_is_refused_by_the_server_outside_paper_mode`.
