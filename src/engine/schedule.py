"""D-0021 trigger-detection schedule helper (Controller-approved:
"D-0021 hourly trigger-detection cadence remains unchanged"), with
timezone realigned per D-0041 from America/Chicago to America/New_York.

The Engine's `reconcile_unresolved()`/decision-intake cadence is a
separate, tighter, independently-approved concern (Controller-approved:
"Reconciliation may use a separate tighter polling cadence because it
does not change trigger semantics") and does NOT use this module --
only Ladder/Floor trigger detection is gated by this schedule.

D-0021's decision text, as realigned by D-0041: `paper-trading-monitor`
(the D-0040 symbol-agnostic template) first pass at 09:30
America/New_York, then hourly on the half-hour through 15:30 ET,
weekdays only (`30 9-15 * * 1-5`). The wall-clock moments are
identical to the pre-D-0041 CT labels; only the timezone label
changes. This module answers only "does this `now` fall on one of
those scheduled check times" -- it does not decide whether the Engine
should be running at all, does not know about US market holidays
(D-0006's calendar is a separate, not-yet-implemented concern --
explicitly NOT invented here; see the implementation report), and
never silently changes the schedule itself, per D-0021's own text:
"The Controller directed that this must not be silently changed."

DST behavior: the timezone is TZ-aware. On the US spring-forward and
fall-back Sundays, `America/New_York` handles the offset shift
automatically; a caller's fixed-UTC clock will drift by an hour and is
explicitly forbidden by D-0020 (preserved in D-0041 §1).
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

# Renamed per D-0041 (America/New_York). The legacy `D0021_TIMEZONE`
# and `D0021_CHECK_TIMES_CT` symbols are retained as aliases below so
# any external caller that imported the old names does not break; they
# now point at the D-0041 values.
D0021_TIMEZONE_ET = ZoneInfo("America/New_York")

D0021_CHECK_TIMES_ET = tuple(time(hour=h, minute=30) for h in range(9, 16))
"""09:30, 10:30, 11:30, 12:30, 13:30, 14:30, 15:30 -- the exact 7
scheduled passes per D-0041's realignment of D-0021 (identical
wall-clock moments to the pre-D-0041 CT labels)."""

# Backwards-compatible aliases -- same objects, new-TZ values.
D0021_TIMEZONE = D0021_TIMEZONE_ET
D0021_CHECK_TIMES_CT = D0021_CHECK_TIMES_ET

DEFAULT_TOLERANCE_SECONDS = 90.0
"""How close `now` must be to a scheduled check time to count as that
check -- exists only because a real caller's wake cadence (whatever
external scheduler invokes the Engine) is not guaranteed to land on
the exact second; does not widen or narrow the set of approved times
themselves."""


def is_d0021_check_time(
    now: datetime, *, tolerance_seconds: float = DEFAULT_TOLERANCE_SECONDS
) -> bool:
    """True if `now` (any timezone-aware datetime) falls within
    `tolerance_seconds` of one of D-0021's 7 scheduled check times, on
    a weekday, in America/New_York wall-clock time (per D-0041). Does
    NOT account for US market holidays (D-0006) -- a known,
    explicitly-flagged gap, not a silent omission; see the Engine
    implementation report."""

    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    now_et = now.astimezone(D0021_TIMEZONE_ET)
    if now_et.weekday() >= 5:
        return False

    for scheduled_time in D0021_CHECK_TIMES_ET:
        scheduled = now_et.replace(
            hour=scheduled_time.hour, minute=scheduled_time.minute, second=0, microsecond=0
        )
        if abs((now_et - scheduled).total_seconds()) <= tolerance_seconds:
            return True
    return False
