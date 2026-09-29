"""DST-safe next-fire-time computation (B29 / D-0023).

Pure functions, stdlib only (`zoneinfo` + `datetime`). Given a
timezone-aware `now`, a set of times-of-day, and the set of weekdays
the schedule fires on, computes the earliest wall-clock moment
strictly after `now` at which one of those times occurs on one of
those weekdays.

DST semantics:
  - The IANA zone (via `zoneinfo`) handles both spring-forward and
    fall-back automatically. We just build the wall-clock datetime
    and let `astimezone` normalize it.
  - Spring forward: on the transition day (US: 2nd Sunday of March),
    a wall-clock time like 02:30 does not exist in America/New_York.
    Constructing such a datetime materializes as the "compressed"
    UTC offset -- meaning the schedule effectively skips the missing
    hour without drift, which is the correct behavior for a
    weekly business schedule.
  - Fall back: the wall-clock hour 01:00-01:59 repeats. `datetime`
    with `fold=0` (default) picks the earlier instance, which is
    what we want: we fire on the FIRST occurrence of each wall
    clock time so the daily schedule still fires exactly once per
    calendar day.
  - Sundays are excluded from the default weekdays set (Monday=0
    through Friday=4), so both DST-transition Sundays are skipped
    by the same rule that excludes any weekend day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import FrozenSet, Sequence, Tuple
from zoneinfo import ZoneInfo


WEEKDAYS_MON_TO_FRI: FrozenSet[int] = frozenset({0, 1, 2, 3, 4})


@dataclass(frozen=True)
class ScheduleSpec:
    """A repeating weekly schedule in a fixed IANA timezone.

    `times_of_day`: sorted, unique tuple of `time` objects (no tz).
    `weekdays`: 0=Monday .. 6=Sunday.
    `tz`: IANA zone (e.g. ZoneInfo("America/New_York")).
    """

    tz: ZoneInfo
    times_of_day: Tuple[time, ...]
    weekdays: FrozenSet[int] = WEEKDAYS_MON_TO_FRI

    def __post_init__(self) -> None:
        if not self.times_of_day:
            raise ValueError("times_of_day must be non-empty")
        # Enforce sorted, unique.
        prev = None
        for t in self.times_of_day:
            if prev is not None and t <= prev:
                raise ValueError(
                    "times_of_day must be strictly increasing"
                )
            prev = t
        if not self.weekdays:
            raise ValueError("weekdays must be non-empty")
        for wd in self.weekdays:
            if not 0 <= wd <= 6:
                raise ValueError(f"weekday {wd} out of range 0..6")


def next_fire_at(now: datetime, spec: ScheduleSpec) -> datetime:
    """Earliest fire moment strictly after `now`, in `spec.tz`.

    `now` must be tz-aware. Returns a tz-aware datetime in `spec.tz`.
    """

    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    now_local = now.astimezone(spec.tz)
    # Look up to 14 days ahead -- enough to skip a long holiday chunk
    # if weekdays exclude everything sooner (though our default is
    # Mon-Fri and no schedule spans > 3 non-weekdays in a row).
    for day_offset in range(0, 15):
        candidate_date = (now_local + timedelta(days=day_offset)).date()
        if candidate_date.weekday() not in spec.weekdays:
            continue
        for t in spec.times_of_day:
            candidate = datetime.combine(
                candidate_date, t, tzinfo=spec.tz,
            )
            if candidate > now_local:
                return candidate
    raise RuntimeError(
        "no fire moment found within 14 days; spec has an empty "
        "effective schedule"
    )


def is_fire_time(
    now: datetime,
    spec: ScheduleSpec,
    *,
    tolerance_seconds: float = 60.0,
) -> bool:
    """True if `now` falls within `tolerance_seconds` of any scheduled
    fire moment. Used by wall-clock oracle callers (`is_d0021_check_time`
    et al.) rather than the daemon (which sleeps to exact times)."""

    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now_local = now.astimezone(spec.tz)
    if now_local.weekday() not in spec.weekdays:
        return False
    for t in spec.times_of_day:
        scheduled = now_local.replace(
            hour=t.hour, minute=t.minute,
            second=0, microsecond=0,
        )
        if abs((now_local - scheduled).total_seconds()) <= tolerance_seconds:
            return True
    return False
