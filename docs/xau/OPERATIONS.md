# XAU operations

XAU is off until a human sets the flag after this change has been audited. Do not set it from a deploy script.

## Enable

```bash
fly secrets set WARP_XAU_ENABLED=1 -a warp3c-bayue
```

The process reads the flag at boot. Exactly `1` starts the desk. Unset, `0`, or any other value leaves it disabled, and the DCA loop still starts.

## Disable

Unset the secret and restart:

```bash
fly secrets unset WARP_XAU_ENABLED -a warp3c-bayue
```

## Pause XAU only

Create the file next to the database. In production the database is `/data/warp.db`, so the file is:

```bash
touch /data/XAU_HALT
```

New signals stop. Open signals keep being tracked. Remove the file to resume.

`/data/HALT` is the existing global pause. It also blocks new XAU signals and does not change how DCA treats HALT.

## Read status

`GET /api/status` includes `xau` when the operator is authenticated. The block has `enabled`, `state`, `heartbeat_at`, `window`, `engine`, `feed`, `basis`, `today`, `open_signal`, `recent`, and `stats30`.

`state` is `feed_unavailable` when no candle source answers. DCA `/live`, `/health`, and `/ready` are unchanged.

Alerts go out through the existing Telegram outbox. Every XAU event starts with `XAU `. The desk does not add a bot token or a chat id.
