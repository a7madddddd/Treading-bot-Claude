"""SnapshotUniverseSource -- reads today's ApprovedUniverseSnapshot
and exposes its symbols to the Engine as a WatchlistSource.

Per D-0026 §6 "no-universe = no-trade": an absent, stale, or empty
snapshot returns an empty tuple. The Engine then simply does not
start any new trades that tick -- existing positions are unaffected
by watchlist emptiness (their Ladder / Floor logic runs regardless).

An optional `fallback_watchlist` is supported for the transition
period while D-0026 pipeline output is being validated: when a
snapshot for the effective date is missing, the fallback is used
instead. Passing `fallback_watchlist=None` (the default) enforces
the strict D-0026 §6 rule.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from typing import Callable, Optional, Tuple

from d0026.repository import SnapshotRepository
from engine.watchlist import WatchlistSource


_ET = ZoneInfo("America/New_York")
"""P-039 (2026-10-05). This was a hardcoded -4.0 hour offset, which is
US Eastern only during EDT; from 1 November 2026 Eastern is UTC-5.

Measured before the change, replaying a full winter day hour by hour:
exactly one UTC hour disagreed -- 04:00 UTC, which is 23:00 ET the
previous evening -- and every hour that matters (the 06:00 ET universe
run, the 09:30 open, the 16:00 close) was already correct. So this is a
correctness fix with no effect inside trading hours, not a bug fix for
an active failure.

It is fixed anyway because the failure SHAPE is the one that cost a
whole trading day on 2026-10-05: the engine asks for a date whose
snapshot does not exist, gets an empty watchlist, and says nothing.

ZoneInfo reads the system IANA database (/usr/share/zoneinfo), which
already carries the 2026 and 2027 transitions. No network call -- a
network dependency on this path would be the same class of fault as
P-032, where a rate-limited broker left the Floor check unevaluated.
`engine/schedule.py` and `scheduler/next_fire.py` already use ZoneInfo;
this makes the subsystem consistent rather than introducing a third
convention."""


class SnapshotUniverseSource(WatchlistSource):
    def __init__(
        self,
        repository: SnapshotRepository,
        *,
        fallback_watchlist: Optional[Tuple[str, ...]] = None,
        now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._repository = repository
        self._fallback = (
            tuple(s.strip().upper() for s in fallback_watchlist)
            if fallback_watchlist else None
        )
        self._now_fn = now_fn

    def get_active_symbols(self) -> Tuple[str, ...]:
        effective = _current_effective_date_et(self._now_fn())
        snapshot = self._repository.get_latest_for_date(effective)
        if snapshot is None or snapshot.is_empty:
            if self._fallback is None:
                return ()
            return self._fallback
        return tuple(entry.ticker_as_of_date.upper()
                     for entry in snapshot.symbols)


def _current_effective_date_et(now_utc: datetime) -> date:
    """Returns today's US Eastern trading date, DST-correct in both
    halves of the year (P-039). A naive datetime is read as UTC, which
    is what every caller in this repo passes."""
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    return now_utc.astimezone(_ET).date()
