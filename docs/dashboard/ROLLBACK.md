# Rollback and backup runbook (one Fly machine, SQLite volume)

**Status: procedure only.** Nothing in this document was executed by the P5 lane. No `flyctl` command was run, no production or staging host was contacted, and the `flyctl` flags below were not tried against the live app. Check each command with `fly <command> --help` on the operator's machine before the first real use.

Scope: Fly app `warp3c-bayue`, one machine in `sin`, SQLite journal at `/data/warp.db` on the volume `warp_data` (`fly.toml` `[[mounts]]`, `[env] WARP_DB_PATH`). Deploys are local `flyctl` deploys by the operator. GitHub Actions is billing-locked, so there is no CI deploy to roll back.

The worker runs in `TRADING_MODE=observe`. A rollback never needs a mode change, and none of the steps below touch `TRADING_MODE`, the live-execution acknowledgement, or any secret.

## Rules for every step

1. Take a note of the current release, image and volume IDs before changing anything (section 1).
2. Roll back the **image** first. It is the smaller change and it is reversible in minutes. Touch the volume only when the data itself is wrong or lost.
3. **Keep every old volume.** A volume that was replaced stays in the account until the owner decides, in writing, that it can go. This document gives no command that removes a volume, and none should be added.
4. Run one machine only. Do not scale above 1: the volume attaches to one machine and two writers on one SQLite file is data loss.
5. After any change, run the health gate (section 6) before declaring the rollback finished.
6. Record what was done, with the IDs, in the incident note. Do not paste tokens, cookies, secrets or full environment dumps into it.

## 1. Capture state first (read-only)

```bash
fly status -a warp3c-bayue
fly releases -a warp3c-bayue
fly machine list -a warp3c-bayue
fly volumes list -a warp3c-bayue
fly image show -a warp3c-bayue
```

Write down: current release version (v21 at P4C), the image reference, the machine ID and its region, the volume ID attached to `/data`, and the volume IDs that are not attached.

`fly.toml` mounts by **name** (`warp_data`). If an unattached volume with the same name exists, a later `fly deploy` or machine create can pick it. After any volume change, confirm which volume ID is attached (section 4, step 6) and do not deploy until you have.

## 2. Roll back the image (code and dashboard only)

Use this for a bad deploy: broken dashboard, regression in the worker, bad bundle. The volume is not changed.

```bash
# 1. Find the last good release and its image.
fly releases -a warp3c-bayue --image

# 2. Redeploy that exact image. Keep one machine.
fly deploy -a warp3c-bayue --ha=false --image registry.fly.io/warp3c-bayue:<deployment-tag-of-last-good-release>
```

Notes:

- `--image` reuses a built image, so no local build and no new code is involved.
- `fly.toml` is read by `fly deploy`, so the environment block in the file at the time of the rollback is applied, not the one the old image shipped with. If the bad release changed `fly.toml`, check out the matching commit first (`git checkout <sha> -- fly.toml` in a scratch clone, never on `main`).
- Secrets are stored on the app, not in the image. A code rollback does not alter them.
- Expect one restart. `kill_timeout = 30` lets the worker finish its write before SIGTERM becomes SIGKILL.

If the machine will not become healthy after the redeploy, stop and go to section 6. Do not keep redeploying.

## 3. Back up the volume before any data change

Fly takes automatic volume snapshots daily and keeps them for a limited time. Take a manual one before any risky action and write down its ID.

```bash
fly volumes list -a warp3c-bayue
fly volumes snapshots list <volume-id> -a warp3c-bayue
fly volumes snapshots create <volume-id> -a warp3c-bayue
fly volumes snapshots list <volume-id> -a warp3c-bayue   # wait until the new snapshot shows as created
```

Backup gate. Do not start a restore until all of these are true:

- [ ] The snapshot to restore is chosen by ID and by creation time, and the time is compared with the incident time.
- [ ] A fresh manual snapshot of the volume as it is now exists, even if it is damaged.
- [ ] The decision is recorded: what data is expected to be lost between the snapshot and now. The journal holds order submissions, bot ownership and equity marks, so a gap here can hide an unknown submission.
- [ ] The owner approved the restore. Restoring is a data decision, not a code decision.

## 4. Restore a volume (create a new one, keep the old one)

A snapshot is never restored onto the existing volume. A **new** volume is created from the snapshot, the machine is moved to it, and the old volume stays untouched and detached.

```bash
# 1. Stop writes. The machine keeps its config; only the process stops.
fly machine stop <machine-id> -a warp3c-bayue

# 2. Create a new volume from the chosen snapshot, same region, same or larger size.
fly volumes create warp_data --snapshot-id <snapshot-id> --region sin --size <gb> -a warp3c-bayue

# 3. Note the new volume ID from the output, and list both.
fly volumes list -a warp3c-bayue

# 4. Move the machine to the new volume. Verify the flag with: fly machine update --help
fly machine update <machine-id> --volume <new-volume-id>:/data -a warp3c-bayue

# 5. Start the machine.
fly machine start <machine-id> -a warp3c-bayue

# 6. Confirm the attachment. The new volume must show the machine ID, the old one must show none.
fly volumes list -a warp3c-bayue
fly machine status <machine-id> -a warp3c-bayue
```

If step 4 is not supported by the installed `flyctl`, the fallback is to create a replacement machine on the new volume and stop the old machine. Do that only with the owner present, keep the old machine stopped rather than removed, and keep one running machine at all times after the swap.

The old volume now holds the pre-restore data. It stays. Both volumes share the name `warp_data`, so write both IDs in the incident note and re-check the attachment after every later deploy.

## 5. Data checks after a restore

Run these against the **new** attachment before trusting it. They are reads only.

1. Dashboard sign-in works and Overview loads.
2. Settings and risk > Activity shows the journal. The newest submission time is where the snapshot ends. Any submission shown as `unknown` stays `unknown` until it is reconciled by the worker against the broker listing. Do not mark one as done by hand.
3. Settings and risk > Risk and health. HALT state matches what the operator expects. If the restored file predates a HALT, apply HALT again from the dashboard (**HALT new entries**) before anything else. HALT stops new orders and does not close positions.
4. Bots and positions: WARP-owned bots listed are the ones in the journal. Bots from other platforms stay external and view-only.
5. The mode badge says OBSERVE.
6. A cycle ran after start: the status panel shows a fresh worker age (seconds, not minutes) and `last_cycle_ok`.

## 6. Health gate (after any rollback or restore)

Public, unauthenticated, read-only probes:

```bash
curl -fsS https://warp3c-bayue.fly.dev/live   # liveness: 200 as soon as the process serves
curl -sS  https://warp3c-bayue.fly.dev/ready  # 200 only when dependencies are healthy and the last cycle succeeded; 503 is a real signal
fly checks list -a warp3c-bayue
fly logs -a warp3c-bayue --no-tail | tail -n 80
```

Pass when all of these hold:

- `/live` is 200 and the Fly check is passing.
- `/ready` is 200 within a few cycles. On a fresh start it can be 503 for the first cycle (`WARP_CYCLE_SECONDS = 900`, `grace_period = 60s`). A 503 that persists past one full cycle is a failure.
- Logs show the worker started in `observe`, opened `/data/warp.db`, and show no `database is locked` or `unable to open database file` lines.
- Exactly one machine is `started` (`fly machine list`).
- The dashboard shows Stream **Live** and Worker age under 15 s, and the Risk tab shows the OBSERVE notice rather than an incident.

If any check fails: stop, do not retry in a loop, restore the previous state from the notes taken in section 1, and report.

## 7. Worked example: the Nov 23 volume loss

Facts recorded by the owner for this repository:

- On Nov 23 the host holding the volume was lost.
- The recovery created a **new** volume from snapshot `vs_40kv0xollvAtz5lVmV5` (Nov 23, 14:10 WIB) and attached it to the machine.
- The old volume `vol_42k13q9n9g9jo294` was **kept**, detached and untouched.
- Consequence: there is no journal data between the snapshot time and the host loss. Whatever the worker recorded in that window is not on the new volume. The old volume may still hold it if it can be mounted again, which is why it is kept.

What the runbook above would have prescribed, in order:

1. Capture state (section 1) and write down `vol_42k13q9n9g9jo294`.
2. Pass the backup gate (section 3), accepting the gap after 14:10 WIB in writing.
3. Create the new volume from `vs_40kv0xollvAtz5lVmV5` in `sin` (section 4, steps 2 to 3).
4. Move the machine to it, start it, and confirm that the new volume is attached and the old one is not (steps 4 to 6).
5. Data checks (section 5). The newest journal entry must be at or before the snapshot time. Anything newer would mean the wrong volume.
6. Health gate (section 6).
7. Leave `vol_42k13q9n9g9jo294` alone. Open a separate, owner-approved task if the missing window has to be reconstructed from it.

## 8. What this runbook does not cover

- A change to `TRADING_MODE` or to the live-execution acknowledgement. Those are owner decisions outside any rollback.
- Rotating `WARP_OPERATOR_TOKEN` or `WARP_SESSION_SECRET`. Rotating the session secret is the only way to invalidate session cookies that are already issued, because logout clears the cookie in the browser only (see the known gap in the release candidate report). It is a secrets change and needs its own approval.
- Multi-machine or multi-region setups. The app is one machine by design.
- Recovery of broker-side state. 3Commas is the source of truth for bots and positions. The worker only reads it.
