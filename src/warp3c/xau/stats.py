"""Expectancy and the rolling-30 model pause (U7). EMA long and short stay separate (U10)."""

from __future__ import annotations

from decimal import Decimal

D = Decimal
ZERO = D("0")
GROUPS = ("engine", "model", "window", "direction", "grade", "sent")


def expectancy(results: list[D], cost_r: D = ZERO) -> D | None:
    """E = W% · avgW − L% · avgL − costR. avgL is the positive size of a loss."""
    if not results:
        return None
    wins = [item for item in results if item > 0]
    losses = [item for item in results if item < 0]
    count = D(len(results))
    win_rate = D(len(wins)) / count
    loss_rate = D(len(losses)) / count
    avg_w = sum(wins, D("0")) / D(len(wins)) if wins else D("0")
    avg_l = sum((abs(item) for item in losses), D("0")) / D(len(losses)) if losses else D("0")
    return win_rate * avg_w - loss_rate * avg_l - cost_r


def profit_factor(results: list[D]) -> D | None:
    wins = sum((item for item in results if item > 0), D("0"))
    losses = sum((abs(item) for item in results if item < 0), D("0"))
    if losses == 0:
        return None if wins == 0 else wins
    return wins / losses


def max_losing_streak(results: list[D]) -> int:
    streak = best = 0
    for item in results:
        if item < 0:
            streak += 1
            best = max(best, streak)
        else:
            streak = 0
    return best


def pack(results: list[D], cost_r: D = ZERO) -> dict:
    wins = [item for item in results if item > 0]
    losses = [item for item in results if item < 0]
    count = len(results)
    return {
        "count": count,
        "win_rate": None if count == 0 else str(D(len(wins)) / D(count)),
        "avg_win_r": None if not wins else str(sum(wins, D("0")) / D(len(wins))),
        "avg_loss_r": None if not losses else str(sum((abs(item) for item in losses), D("0")) / D(len(losses))),
        "expectancy": None if count == 0 else str(expectancy(results, cost_r)),
        "profit_factor": None if profit_factor(results) is None else str(profit_factor(results)),
        "max_losing_streak": max_losing_streak(results),
    }


def grouped(rows: list[dict], *, rolling: int | None, cost_r: D = ZERO) -> dict:
    """rows are closed signals, oldest first. ``sent`` is the string '1' or '0'."""
    chosen = rows[-rolling:] if rolling else rows
    out: dict = {}
    for key in GROUPS:
        buckets: dict[str, list[D]] = {}
        for row in chosen:
            label = str(row.get(key if key != "sent" else "sent"))
            value = _r(row)
            if value is None:
                continue
            buckets.setdefault(label, []).append(value)
        out[key] = {label: pack(values, cost_r) for label, values in buckets.items()}
    out["all"] = pack([value for row in chosen if (value := _r(row)) is not None], cost_r)
    return out


def paused_models(rows: list[dict], cost_r: D = ZERO) -> list[str]:
    """U7. A model pauses when its last 30 closed results have expectancy below zero."""
    by_model: dict[str, list[D]] = {}
    for row in rows:
        value = _r(row)
        if value is None or not row.get("model"):
            continue
        by_model.setdefault(str(row["model"]), []).append(value)
    paused = []
    for model, values in by_model.items():
        window = values[-30:]
        if len(window) < 30:
            continue
        score = expectancy(window, cost_r)
        if score is not None and score < 0:
            paused.append(model)
    return sorted(paused)


def _r(row: dict) -> D | None:
    raw = row.get("realized_r")
    if raw in (None, ""):
        return None
    try:
        return D(str(raw))
    except Exception:
        return None
