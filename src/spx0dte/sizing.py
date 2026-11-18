"""Position sizing, concurrency limit, and daily loss limit for the
SPX 0DTE module (D-0095, D-0095-A).

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

CONTRACT_MULTIPLIER = 100 is a US listed-options market convention
(1 contract = 100 shares-equivalent), not a code choice -- every raw
`exit_price - entry_price` figure anywhere in this module must be
multiplied by this (and by quantity) to read as real dollars. See
docs/trading/decisions.md D-0095-A, "CORRECTION... not real dollars"
(2026-11-18) for why this matters and what it corrects.

DAILY_LOSS_LIMIT_PCT = 0.02 (2%) of the account's START-OF-DAY equity
is Controller-approved, 2026-11-18: measured from the real daily-pnl
distribution in `spx0dte_measure_risk_sizing.py`'s first run
(2025-06-01..2025-12-31, 148 trading days) -- the worst day was -4.18%
of a $97,593.76 reference equity, the 5th-percentile day -1.58%; 2%
sits just above that 5th-percentile day and comfortably below the
worst day. Reference is the WHOLE account (no separate capital
allocation exists yet between this module and the equity-ladder
strategy -- Controller-confirmed, 2026-11-18); if a dedicated
allocation is created later, this should be redefined against that
instead. Full numbers: docs/trading/decisions.md D-0095-A.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_type, datetime
from typing import List, Optional

CONTRACTS_PER_TRADE = 1
MAX_CONCURRENT_POSITIONS = 2
CONTRACT_MULTIPLIER = 100
DAILY_LOSS_LIMIT_PCT = 0.02


def realized_pnl_dollars(entry_price: float, exit_price: float, quantity: int) -> float:
    """The real dollar pnl of a closed position -- applies the
    contract multiplier the rest of this module's history did not
    (see module docstring). Always use this, never the bare
    `exit_price - entry_price` difference, for anything that will be
    compared against a dollar or percent-of-equity threshold."""
    return (exit_price - entry_price) * quantity * CONTRACT_MULTIPLIER


def percentile(sorted_values: List[float], pct: float) -> float:
    """Linear-interpolated percentile of an ALREADY-SORTED sequence
    (ascending). Same formula as
    `scripts/spx0dte_measure_risk_sizing.py`'s own `_percentile` --
    kept here too (not imported from a script) so production code
    never depends on a scripts/ module. Raises ValueError on an
    empty sequence -- a caller must check for enough history first,
    never silently default to a fabricated number."""
    if not sorted_values:
        raise ValueError("sorted_values must be non-empty")
    n = len(sorted_values)
    k = (n - 1) * pct
    f, c = int(k), min(int(k) + 1, n - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


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


@dataclass
class DailyLossLimitTracker:
    """Blocks NEW entries once realized losses for the trading day
    reach `limit_pct` of that day's locked starting equity (D-0095-A,
    2026-11-18 -- Controller-approved: whole account, 2%). Does NOT
    touch already-open positions: those keep being managed by their
    own target/stop/stall/time-cap exit logic via `live_exit.
    LiveExitTracker` -- this tracker only gates whether a NEW
    opportunity may become a proposal.

    `start_new_day` is idempotent within a day (a caller may call it
    every tick; it only actually resets state the first time a new
    `today` is seen) -- mirrors `DailyPaceTracker`'s own "lock once
    per day" pattern in `exit_logic.py`."""

    limit_pct: float = DAILY_LOSS_LIMIT_PCT
    _current_date: Optional[str] = None
    _locked_equity: Optional[float] = None
    _realized_pnl_today: float = 0.0

    def __post_init__(self) -> None:
        if not (0.0 < self.limit_pct < 1.0):
            raise ValueError("limit_pct must be between 0 and 1 (exclusive)")

    def start_new_day(self, today: str, equity: float) -> None:
        if self._current_date == today:
            return
        if equity <= 0:
            raise ValueError(f"equity must be positive, got {equity!r}")
        self._current_date = today
        self._locked_equity = equity
        self._realized_pnl_today = 0.0

    def record_realized_pnl(self, pnl_dollars: float) -> None:
        self._realized_pnl_today += pnl_dollars

    def limit_reached(self) -> bool:
        if self._locked_equity is None:
            return False
        return self._realized_pnl_today <= -(self._locked_equity * self.limit_pct)

    @property
    def realized_pnl_today(self) -> float:
        return self._realized_pnl_today

    @property
    def locked_equity(self) -> Optional[float]:
        return self._locked_equity
