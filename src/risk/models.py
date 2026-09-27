"""Data types for the portfolio-level risk subsystem (D-0047).

Frozen dataclasses. All numeric parameters are documented with the
D-0047-approved default. `PortfolioSnapshot` is the plain data view
the enforcer inspects; the enforcer never reaches out to Alpaca or
SQLite on its own -- a separate snapshot builder does that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Tuple


class RiskVerdict(str, Enum):
    ALLOWED = "ALLOWED"
    VIOLATED = "VIOLATED"


@dataclass(frozen=True)
class PortfolioRiskLimits:
    """The Controller-approved portfolio-level limits.

    Defaults match D-0047's approved numbers:
      max_gross_exposure_fraction        = 0.60  (60% of equity)
      max_single_symbol_fraction         = 0.10  (10% of equity per symbol)
      max_concurrent_trades              = 5
      max_daily_new_trades               = 3
      daily_loss_kill_switch_fraction    = 0.03  (3% of prior-day equity)

    Any change to these values is itself a Controller decision (§5
    CLAUDE.md), recorded in `docs/trading/decisions.md`.
    """

    max_gross_exposure_fraction: float = 0.60
    max_single_symbol_fraction: float = 0.10
    max_concurrent_trades: int = 5
    max_daily_new_trades: int = 3
    daily_loss_kill_switch_fraction: float = 0.03

    def __post_init__(self) -> None:
        for name in ("max_gross_exposure_fraction",
                     "max_single_symbol_fraction",
                     "daily_loss_kill_switch_fraction"):
            v = getattr(self, name)
            if not (0.0 < v <= 1.0):
                raise ValueError(f"{name} must be in (0, 1], got {v!r}")
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
