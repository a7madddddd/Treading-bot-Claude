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

from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional, Tuple

from d0026.repository import SnapshotRepository
from engine.watchlist import WatchlistSource


_US_MARKET_TZ_OFFSET_HOURS = -4.0
"""Approximate US Eastern offset used only to pick "today" in ET
for snapshot lookup. Precision matches the risk-subsystem convention
(risk/portfolio_snapshot.py). Wall-clock strategy timing lives in
`engine/schedule.py`, not here."""


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
    """Returns today's US Eastern trading date. Loose ET conversion
    (fixed 4-hour offset) is intentional -- the trading date is a
    coarse anchor, not a strategy trigger. The strategy engine uses
    `engine/schedule.py` for precise timing decisions."""
    et_now = now_utc + timedelta(hours=_US_MARKET_TZ_OFFSET_HOURS)
    return et_now.date()
