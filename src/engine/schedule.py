"""D-0021 trigger-detection schedule helper (Controller-approved:
"D-0021 hourly trigger-detection cadence remains unchanged").

The Engine's `reconcile_unresolved()`/decision-intake cadence is a
separate, tighter, independently-approved concern (Controller-approved:
"Reconciliation may use a separate tighter polling cadence because it
does not change trigger semantics") and does NOT use this module --
only Ladder/Floor trigger detection is gated by the D-0021 schedule.

D-0021's decision text: `tsla-paper-trading-monitor` first pass at
08:30 America/Chicago, then hourly on the half-hour through 14:30 CT,
weekdays only (`30 8-14 * * 1-5`). This module answers only "does this
`now` fall on one of those scheduled check times" -- it does not decide
whether the Engine should be running at all, does not know about market
holidays (D-0006's calendar is a separate, not-yet-implemented concern
-- explicitly NOT invented here; see the implementation report), and
never silently changes the schedule itself, per D-0021's own text:
"The Controller directed that this must not be silently changed."
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

D0021_TIMEZONE = ZoneInfo("America/Chicago")

D0021_CHECK_TIMES_CT = tuple(time(hour=h, minute=30) for h in range(8, 15))
"""08:30, 09:30, 10:30, 11:30, 12:30, 13:30, 14:30 -- the exact 7
scheduled passes from D-0021."""

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
    a weekday, in America/Chicago wall-clock time. Does NOT account for
    US market holidays (D-0006) -- a known, explicitly-flagged gap, not
    a silent omission; see the Engine implementation report."""

    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    now_ct = now.astimezone(D0021_TIMEZONE)
    if now_ct.weekday() >= 5:
        return False

    for scheduled_time in D0021_CHECK_TIMES_CT:
        scheduled = now_ct.replace(
            hour=scheduled_time.hour, minute=scheduled_time.minute, second=0, microsecond=0
        )
        if abs((now_ct - scheduled).total_seconds()) <= tolerance_seconds:
            return True
    return False
