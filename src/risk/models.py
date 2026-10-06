"""Data types for the portfolio-level risk subsystem (D-0047).

Frozen dataclasses. All numeric parameters are documented with the
D-0047-approved default. `PortfolioSnapshot` is the plain data view
the enforcer inspects; the enforcer never reaches out to Alpaca or
SQLite on its own -- a separate snapshot builder does that.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional, Tuple


TRADE_BUDGET_FRACTION = 0.05
"""D-0051's approved trade budget: 5% of equity funds ONE complete
trade (Initial 25% + Ladder 1 25% + Ladder 2 50%).

DUPLICATED ON PURPOSE. The canonical owner is
`proposals.position_sizing.PositionSizingPolicy.trade_budget_pct`, and
`risk` deliberately does not import `proposals` -- the two packages have
no dependency in either direction today and this change does not create
one. The duplication is guarded by a test that asserts the two values
are equal, so they cannot diverge silently:
`tests/risk/test_d0079_derived_limits.py::TestTheDuplicateIsGuarded`."""

DAILY_NEW_TRADE_FRACTION = 0.25
"""D-0079 (Controller-approved 2026-10-06): what share of the concurrent
capacity may be opened in a single trading day. 25% of 12 chairs = 3 new
trades a day, which is the value that was hardcoded before -- the number
does not change, its SOURCE does.

Why a fraction of the chairs and not of equity: it keeps "days to fill
the shelf" invariant when the exposure ceiling changes. At 60% gross the
cap is 12 and the pace 3 (4 days); at 80% it is 16 and 4 (4 days); at
40% it is 8 and 2 (4 days). A fixed 3 would fill a 40% shelf in 3 days
and an 80% shelf in 6, for no stated reason."""


def _floor_ratio(numerator: float, denominator: float) -> int:
    """floor(numerator / denominator) computed in Decimal, never float.

    The whole reason this function exists, measured:

        0.60 / 0.05        == 11.999999999999998
        math.floor(...)    == 11          <- one whole position lost
        Decimal exact      == 12

    Neither 0.60 nor 0.05 is representable in binary floating point, so
    the quotient lands just below the integer and floor() truncates to
    the wrong side. The failure is silent: no exception, no test that
    checks the formula's shape would notice, and the only symptom is a
    twelfth proposal refused with no comprehensible reason.
    """
    return math.floor(Decimal(str(numerator)) / Decimal(str(denominator)))


def _floor_product(fraction: float, count: int) -> int:
    """floor(fraction * count) in Decimal, for the same reason as
    `_floor_ratio` -- e.g. 0.25 * 12 must be exactly 3."""
    return math.floor(Decimal(str(fraction)) * Decimal(count))


class RiskVerdict(str, Enum):
    ALLOWED = "ALLOWED"
    VIOLATED = "VIOLATED"


@dataclass(frozen=True)
class PortfolioRiskLimits:
    """The Controller-approved portfolio-level limits.

    The three FRACTIONS are the Controller's approved numbers and are
    never derived:
      max_gross_exposure_fraction        = 0.60  (60% of equity)
      max_single_symbol_fraction         = 0.10  (10% of equity per symbol)
      daily_loss_kill_switch_fraction    = 0.03  (3% of prior-day equity)

    The two COUNTS are DERIVED from them (D-0079, Controller-approved
    2026-10-06) instead of being hardcoded:

      max_concurrent_trades = floor(max_gross_exposure_fraction
                                    / trade_budget_fraction)
                            = floor(0.60 / 0.05) = 12
      max_daily_new_trades  = floor(daily_new_trade_fraction
                                    * max_concurrent_trades)
                            = floor(0.25 * 12) = 3

    WHY THE CONCURRENT CAP CHANGED FROM 5 TO 12
    -------------------------------------------
    Two approved numbers contradicted each other. One complete trade is
    5% of equity (D-0051), so a cap of 5 concurrent trades could only
    ever reach 25% of equity -- while the approved gross-exposure
    ceiling is 60%. 35 percentage points of the Controller's own risk
    budget were unreachable, not as a deliberate margin but because a
    count and a percentage were set independently and never reconciled.
    Deriving the count removes the contradiction by construction: the
    cap is now exactly as many whole trades as the approved exposure
    ceiling holds.

    The daily pace is UNCHANGED at 3. Only its source changed.

    PASSING EITHER COUNT EXPLICITLY OVERRIDES THE DERIVATION, which is
    what every existing test does and why they are unaffected. Leaving
    it None (the default) derives it, so the single production call site
    -- `scripts/run_paper_session.py` -- picks this up with no change,
    and so would any call site added later.

    Any change to the fractions is itself a Controller decision (§5
    CLAUDE.md), recorded in `docs/trading/decisions.md`.
    """

    max_gross_exposure_fraction: float = 0.60
    max_single_symbol_fraction: float = 0.10
    max_concurrent_trades: Optional[int] = None
    max_daily_new_trades: Optional[int] = None
    daily_loss_kill_switch_fraction: float = 0.03
    trade_budget_fraction: float = TRADE_BUDGET_FRACTION
    daily_new_trade_fraction: float = DAILY_NEW_TRADE_FRACTION

    def __post_init__(self) -> None:
        for name in ("max_gross_exposure_fraction",
                     "max_single_symbol_fraction",
                     "daily_loss_kill_switch_fraction",
                     "trade_budget_fraction",
                     "daily_new_trade_fraction"):
            v = getattr(self, name)
            if not (0.0 < v <= 1.0):
                raise ValueError(f"{name} must be in (0, 1], got {v!r}")

        # Derive only what was not given. An explicit value always wins,
        # including an invalid one -- the >= 1 checks below still run on
        # it, so an explicit 0 raises exactly as it did before D-0079.
        if self.max_concurrent_trades is None:
            object.__setattr__(
                self, "max_concurrent_trades",
                # max(1, ...) is not cosmetic: a small exposure ceiling
                # (e.g. 3% gross with a 5% trade budget) floors to 0,
                # and a limits object that cannot be constructed would
                # crash the engine at startup. The floor of 1 means "one
                # trade at a time", which is what a tiny ceiling should
                # mean -- never "trading is impossible".
                max(1, _floor_ratio(self.max_gross_exposure_fraction,
                                    self.trade_budget_fraction)),
            )
        if self.max_daily_new_trades is None:
            object.__setattr__(
                self, "max_daily_new_trades",
                # Same reason: floor(0.25 * 2) == 0 for a 2-chair cap.
                max(1, _floor_product(self.daily_new_trade_fraction,
                                      self.max_concurrent_trades)),
            )

        if self.max_concurrent_trades < 1:
            raise ValueError("max_concurrent_trades must be >= 1")
        if self.max_daily_new_trades < 1:
            raise ValueError("max_daily_new_trades must be >= 1")


@dataclass(frozen=True)
class PositionView:
    """A single Alpaca position as the enforcer sees it. `market_value`
    is the absolute-value dollar exposure of the position (long or
    short); short positions are not part of the current strategy but
    the field carries their absolute exposure for the gross-exposure
    check anyway."""

    symbol: str
    qty: float
    market_value: float

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("PositionView.symbol required")
        if self.market_value < 0:
            raise ValueError("PositionView.market_value must be absolute (>=0)")


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Read-only snapshot of everything the enforcer needs.

    - `equity_current`: portfolio_value at snapshot time.
    - `equity_at_day_open`: Alpaca's `last_equity` (equity at prior
      trading day close). This is the anchor for the daily-loss kill
      switch.
    - `positions`: list of currently-held Alpaca positions.
    - `open_trades`: count of trades currently AWAITING_INITIAL_FILL
      or ACTIVE in SQLite (source of truth for concurrency limits).
    - `new_trades_today`: count of trades whose `created_at` falls
      within the current US market trading day (see snapshot builder).
    - `pending_ladder_proposals`: proposals for actions that would
      ADD to a position (LADDER_1/LADDER_2). Currently informational;
      not used to reject.
    """

    equity_current: float
    equity_at_day_open: float
    positions: Tuple[PositionView, ...] = field(default_factory=tuple)
    open_trades: int = 0
    new_trades_today: int = 0
    snapshot_at: Optional[datetime] = None

    def gross_exposure(self) -> float:
        return sum(p.market_value for p in self.positions)

    def exposure_for_symbol(self, symbol: str) -> float:
        symbol = symbol.upper()
        return sum(p.market_value for p in self.positions
                   if p.symbol.upper() == symbol)


@dataclass(frozen=True)
class RiskCheckResult:
    verdict: RiskVerdict
    checks: Tuple["RiskCheck", ...] = field(default_factory=tuple)

    @property
    def allowed(self) -> bool:
        return self.verdict is RiskVerdict.ALLOWED

    def violations(self) -> Tuple["RiskCheck", ...]:
        return tuple(c for c in self.checks if not c.passed)

    def first_violation_reason(self) -> Optional[str]:
        for c in self.checks:
            if not c.passed:
                return c.reason
        return None


@dataclass(frozen=True)
class RiskCheck:
    name: str
    passed: bool
    reason: str = ""
