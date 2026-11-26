"""Master checklist C1–C14. Only A+ and A with 14/14 are sent. Scalp is never sent in P0."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal

D = Decimal
CHECKS = tuple(f"C{i}" for i in range(1, 15))


@dataclass
class Setup:
    engine: str
    model: str
    direction: str
    grade: str
    confidence: int
    entry_type: str
    entry: D
    sl: D
    tp1: D
    tp2: D | None
    r_usd: D
    rr_tp1: D
    rr_tp2: D | None
    risk_pct: D
    reason: str
    bias: str
    dol: D | None
    window: str
    valid_until: datetime | None
    tags: dict = field(default_factory=dict)
    poi_ok: bool = False
    liquidity_ok: bool = False
    trigger_ok: bool = False
    displacement_ok: bool = False
    chase_ok: bool = False
    sl_ok: bool = False
    target_ok: bool = False
    feed: str = ""
    basis: D | None = None
    spread: D = D("0.30")
    example_lot: D = D("0")
    checklist: dict = field(default_factory=dict)
    sent: bool = False
    failures: tuple[str, ...] = ()
    signal_id: str = ""


@dataclass(frozen=True)
class GateContext:
    window_engine: str | None
    now: datetime
    feed_fresh: bool
    basis_healthy: bool
    dirty: bool
    news_clear: bool
    risk_clear: bool
    smc_open: bool
    ema_open: bool


def evaluate(setup: Setup, ctx: GateContext) -> Setup:
    """C1–C14. One failure journals B. Two or more journal C. B and C are never sent."""
    checks = {
        "C1": _c1(setup, ctx),
        "C2": _c2(setup, ctx),
        "C3": bool(setup.bias) and len(setup.bias) <= 120,
        "C4": ctx.feed_fresh and ctx.basis_healthy and not ctx.dirty and setup.engine != "SCALP",
        "C5": setup.poi_ok,
        "C6": setup.liquidity_ok,
        "C7": setup.trigger_ok,
        "C8": setup.displacement_ok if setup.engine == "SMC" else True,
        "C9": setup.chase_ok,
        "C10": setup.sl_ok,
        "C11": setup.risk_pct > 0,
        "C12": setup.target_ok,
        "C13": ctx.news_clear and not ctx.dirty,
        "C14": ctx.risk_clear,
    }
    failures = tuple(name for name in CHECKS if not checks[name])
    grade = setup.grade
    sent = False
    if setup.engine == "SCALP":
        sent = False
    elif not failures and grade in {"A+", "A"}:
        sent = True
    elif len(failures) == 1:
        grade = "B"
    elif len(failures) >= 2:
        grade = "C"
    if grade not in {"A+", "A"}:
        sent = False
    return replace(setup, grade=grade, sent=sent, checklist=checks, failures=failures)


def _c1(setup: Setup, ctx: GateContext) -> bool:
    if ctx.window_engine not in {"SMC", "EMA"}:
        return False
    return setup.engine == ctx.window_engine


def _c2(setup: Setup, ctx: GateContext) -> bool:
    if setup.engine == "SMC":
        return ctx.smc_open
    if setup.engine == "EMA":
        return ctx.ema_open
    return False
