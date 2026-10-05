"""D-0077 (Controller-approved 2026-10-05): make the two portfolio trade
limits respond to how much of the market the day's research actually
saw.

THE PROBLEM THIS SOLVES, AND THE ONE IT DOES NOT
------------------------------------------------
P-053 originally scaled the universe's Top-N. P-055 showed that would be
inert: the universe publishes 10 candidates, but `max_daily_new_trades`
is 3, so cutting 10 to 4 changes nothing — 4 still exceeds 3. The
binding constraint was never the candidate list; it is the trade cap.

So the scaling is applied HERE, to the two D-0047 limits the Controller
approved:

    max_daily_new_trades   = 3
    max_concurrent_trades  = 5

"Saw half the market today" becomes "open one new trade instead of
three", which is a real, graded response.

This reduces EXPOSURE, never error. Every D-0048 stage is
percentile-based, so a percentile taken over a partial pool stays wrong
however few trades are finally opened. Nothing here improves the quality
of a pick.

WHAT IT CANNOT DO
-----------------
It never raises a limit. `scale` is clamped at 1.0, so a day that sees
more of the market than usual gets the Controller-approved numbers and
not one trade more.

It never closes a position. `max_concurrent_trades` gates NEW trades
only (`snapshot.open_trades < limit`), so a scaled-down cap with
positions already open simply refuses to add — it can never force an
exit.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence, Tuple

from risk.models import PortfolioRiskLimits


BOOTSTRAP_MIN_SAMPLES = 20
"""Distinct trading dates needed before the baseline switches from the
maximum to the median.

Until then the MAXIMUM is used, because a maximum cannot be dragged down
by a bad day. The first recorded run would otherwise define its own
baseline and score 1.00 whatever it was — and if that first run were
itself degraded, a second degraded day would score ~1.0 and pass
(P-054 §4). The maximum errs toward fewer trades while we are blind,
which is the safe direction.

Why the median afterwards and not the mean: the median does not move at
all until more than half the window is bad, so a two or three day
provider outage cannot redefine 'normal'. A mean would absorb some of
every bad day."""


@dataclass(frozen=True)
class LimitDecision:
    """What the day's data says the limits should be."""

    scale: float
    today_enriched: Optional[int]
    baseline: Optional[int]
    baseline_source: str          # "median" | "maximum" | "none"
    limits: PortfolioRiskLimits   # what to enforce
    trading_allowed: bool         # False => no NEW trades at all today
    reason: str


def load_recent_enriched(
    conn: sqlite3.Connection, *, before_date: date, limit: int,
) -> Tuple[int, ...]:
    """The `enriched_with_features` figure for each of the most recent
    distinct trading dates STRICTLY BEFORE `before_date`.

    Two deliberate choices, both from P-054:

    * **Distinct DATES, not rows.** Measured on the Controller's VM:
      7 snapshots across 3 dates. A window of N rows would have covered
      about N/2.3 days, and a single busy date could fill most of it.
      One row per date — the latest — is taken.
    * **Strictly before today.** If the day being judged were inside its
      own baseline it would drag the median toward its own low figure
      and score closer to 1.0 than it deserves.
    """
    rows = conn.execute(
        "SELECT effective_trading_date, data_quality_json FROM "
        "universe_snapshots WHERE effective_trading_date < ? "
        "ORDER BY effective_trading_date DESC, snapshot_at DESC",
        (before_date.isoformat(),),
    ).fetchall()

    import json
    seen: set = set()
    out: list = []
    for row in rows:
        day = row[0]
        if day in seen:
            continue           # keep only the latest snapshot of that date
        seen.add(day)
        try:
            dq = dict(json.loads(row[1]))
        except Exception:      # noqa: BLE001 - a malformed row is skipped
            continue
        value = dq.get("enriched_with_features")
        if isinstance(value, int) and value > 0:
            out.append(value)
        if len(out) >= limit:
            break
    return tuple(out)


def baseline_from_history(
    values: Sequence[int], *, min_samples: int = BOOTSTRAP_MIN_SAMPLES,
) -> Tuple[Optional[int], str]:
    """Returns (baseline, source). See BOOTSTRAP_MIN_SAMPLES."""
    clean = [int(v) for v in values if isinstance(v, int) and v > 0]
    if not clean:
        return None, "none"
    if len(clean) < min_samples:
        return max(clean), "maximum"
    ordered = sorted(clean)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid], "median"
    return (ordered[mid - 1] + ordered[mid]) // 2, "median"


def compute_scale(today_enriched: Optional[int],
                  baseline: Optional[int]) -> float:
    """How much of a normal day's market this run actually saw.

    Clamped to [0.0, 1.0]: a day that sees MORE than the baseline is a
    normal day, never a licence to trade beyond the Controller-approved
    numbers.
    """
    if not baseline or baseline <= 0:
        return 1.0                      # no history yet -> change nothing
    if today_enriched is None or today_enriched < 0:
        return 1.0                      # unknown -> change nothing
    return max(0.0, min(1.0, today_enriched / baseline))


def scale_limits(base: PortfolioRiskLimits, scale: float
                 ) -> Tuple[PortfolioRiskLimits, bool]:
    """Applies the scale to the two trade-count limits, rounding DOWN
    (Controller's decision, 2026-10-05).

    Returns (limits, trading_allowed). `PortfolioRiskLimits` validates
    both counts as >= 1, so a scaled value of zero cannot be expressed
    in it at all (P-054 §3 — writing the obvious code would raise
    ValueError on exactly the case that must refuse). Zero is therefore
    carried as `trading_allowed=False` alongside limits left at 1, which
    are never consulted when trading is not allowed.

    Only the two COUNT limits scale. The exposure fractions and the
    daily-loss kill switch are untouched: they are already expressed as
    percentages of equity and do not become more or less appropriate
    because the research saw less of the market.
    """
    daily = math.floor(base.max_daily_new_trades * scale)
    concurrent = math.floor(base.max_concurrent_trades * scale)
    if daily < 1 or concurrent < 1:
        return base, False
    return (
        PortfolioRiskLimits(
            max_gross_exposure_fraction=base.max_gross_exposure_fraction,
            max_single_symbol_fraction=base.max_single_symbol_fraction,
            max_concurrent_trades=concurrent,
            max_daily_new_trades=daily,
            daily_loss_kill_switch_fraction=base.daily_loss_kill_switch_fraction,
        ),
        True,
    )


def decide(*, base: PortfolioRiskLimits, today_enriched: Optional[int],
           history: Sequence[int],
           min_samples: int = BOOTSTRAP_MIN_SAMPLES) -> LimitDecision:
    """The whole decision, as one pure function."""
    baseline, source = baseline_from_history(history, min_samples=min_samples)
    scale = compute_scale(today_enriched, baseline)
    limits, allowed = scale_limits(base, scale)

    if baseline is None:
        reason = ("no recorded history yet, so the approved limits stand "
                  "unchanged")
    elif not allowed:
        reason = (f"saw {today_enriched} of a normal {baseline} "
                  f"({scale:.0%}) — too little to open any new trade today")
    elif scale >= 1.0:
        reason = (f"saw {today_enriched} of a normal {baseline} "
                  f"({scale:.0%}) — a normal day, approved limits stand")
    else:
        reason = (f"saw {today_enriched} of a normal {baseline} "
                  f"({scale:.0%}) — new trades {base.max_daily_new_trades}"
                  f"→{limits.max_daily_new_trades}, concurrent "
                  f"{base.max_concurrent_trades}"
                  f"→{limits.max_concurrent_trades}")

    return LimitDecision(
        scale=scale, today_enriched=today_enriched, baseline=baseline,
        baseline_source=source, limits=limits, trading_allowed=allowed,
        reason=reason,
    )
