"""Dataclasses for the backtesting subsystem (B25). All frozen."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional, Tuple


@dataclass(frozen=True)
class Bar:
    """One daily bar. Timezone / adjustment convention is the caller's
    responsibility (matches MarketDataBar in the trading path but is
    a lighter object -- backtesting does not need to persist
    identity, sector, etc.)."""

    bar_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        if self.low > self.high:
            raise ValueError(f"low > high on {self.bar_date}")
        if not (self.low <= self.open <= self.high):
            raise ValueError(f"open outside [low, high] on {self.bar_date}")
        if not (self.low <= self.close <= self.high):
            raise ValueError(f"close outside [low, high] on {self.bar_date}")
        if self.volume < 0:
            raise ValueError(f"negative volume on {self.bar_date}")


class ExitReason(str, Enum):
    FLOOR_HIT = "floor_hit"
    TRAILING_FLOOR_HIT = "trailing_floor_hit"
    END_OF_PERIOD = "end_of_period"


@dataclass(frozen=True)
class BacktestTrade:
    """One completed simulated trade (from initial entry to final
    exit). Immutable audit record."""

    symbol: str
    entry_date: date
    entry_price: float
    initial_shares: int
    ladder1_fill_price: Optional[float]
    ladder1_fill_qty: int
    ladder2_fill_price: Optional[float]
    ladder2_fill_qty: int
    exit_date: date
    exit_price: float
    exit_reason: ExitReason
    final_shares: int
    weighted_avg_entry_price: float
    trailing_activated: bool
    trailing_peak_threshold: Optional[float]

    def pnl(self) -> float:
        """Cash P&L: (exit_price - weighted_avg_entry) * final_shares."""
        return (self.exit_price - self.weighted_avg_entry_price) * self.final_shares

    def return_fraction(self) -> float:
        """Return as fraction of cost basis."""
        cost = self.weighted_avg_entry_price * self.final_shares
        if cost <= 0:
            return 0.0
        return self.pnl() / cost


@dataclass(frozen=True)
class BacktestResult:
    """Aggregated outcome of one backtest run."""

    symbol: str
    trades: Tuple[BacktestTrade, ...]
    metrics: "BacktestMetrics"


@dataclass(frozen=True)
class BacktestMetrics:
    total_trades: int
    winning_trades: int
    losing_trades: int
    total_pnl: float
    average_return: float
    win_rate: float
    profit_factor: float
    max_drawdown: float
    sharpe_ratio: float


@dataclass(frozen=True)
class BacktestConfig:
    """Static configuration for one backtest run.

    `override_initial_qty` / `override_ladder1_qty` /
    `override_ladder2_qty` (all optional) let a caller experiment
    with different position sizes; when None, the strategy's own
    `initial_qty` / `ladder_1_qty` / `ladder_2_qty` are used
    unchanged (default = D-0004 approved values 10 / 10 / 20).
    """

    override_initial_qty: Optional[int] = None
    override_ladder1_qty: Optional[int] = None
    override_ladder2_qty: Optional[int] = None
    cooldown_days_after_exit: int = 5
