"""Position sizing and concurrency limit for the SPX 0DTE module (D-0095).

CONTRACTS_PER_TRADE stays at the Controller-approved fixed size of 1.
A dynamic-sizing scheme (shrink size when multiple positions are open)
was explored and rejected analytically: with a 1-contract baseline,
`min(1, anything)` can never exceed 1 and a contract can't go below 1,
so there is no room to shrink further. The only lever that actually
does something is a hard CAP on how many positions may be open at
once -- reject the extra signal, don't shrink it.

MAX_CONCURRENT_POSITIONS = 2 is not a guessed round number: a causal,
sequential simulation over 254 (2025) + 235 (2026) real candidate
trades, at every cap from 1 to 8, showed the big jump in total pnl
happens from cap 1 to cap 2, and plateaus (or in 2025, slightly
reverses) beyond 2. Full numbers: docs/trading/decisions.md D-0095.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_type, datetime
from typing import List

CONTRACTS_PER_TRADE = 1
MAX_CONCURRENT_POSITIONS = 2


def contracts_for_trade() -> int:
    """Returns the approved fixed contract count. No arguments on
    purpose -- this is not meant to grow conditional logic; a change to
    the size itself is a Controller decision, not a code branch."""
    return CONTRACTS_PER_TRADE


@dataclass
class _OpenPosition:
    exit_time: datetime


@dataclass
class ConcurrentPositionTracker:
    """Causal, sequential admission control: a candidate entry is
    accepted only if fewer than `max_concurrent` already-accepted
    positions are still open at that moment. A rejected candidate
    occupies no slot and therefore can never block a later one --
    matches the measurement this cap was derived from
    (measure_concurrency_cap.py's simulate_with_cap)."""

    max_concurrent: int = MAX_CONCURRENT_POSITIONS
    _open: List[_OpenPosition] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")

    def can_open(self, at: datetime) -> bool:
        self._open = [p for p in self._open if p.exit_time > at]
        return len(self._open) < self.max_concurrent

    def record_open(self, at: datetime, exit_time: datetime) -> None:
        self._open = [p for p in self._open if p.exit_time > at]
        self._open.append(_OpenPosition(exit_time=exit_time))


@dataclass
class DailyTradeCounter:
    """Kept for callers that still want a sanity-check daily ceiling in
    addition to the concurrency cap above; NOT part of the D-0095
    backtest (which enforced no daily count limit, only concurrency).
    Unbounded by default -- pass a finite `max_per_day` explicitly if a
    future decision adds one."""

    max_per_day: int = 10_000
    _counts: dict = field(default_factory=dict)

    def can_trade(self, today: date_type) -> bool:
        return self._counts.get(today, 0) < self.max_per_day

    def record_trade(self, today: date_type) -> None:
        self._counts[today] = self._counts.get(today, 0) + 1
