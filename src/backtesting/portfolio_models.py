"""Portfolio-level backtesting models (B25b).

Extends the single-symbol backtest with shared capital and D-0047
risk-limit enforcement across a multi-symbol simulation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Dict, Optional, Tuple

from backtesting.models import BacktestTrade, ExitReason


class RejectionReason(str, Enum):
    KILL_SWITCH = "kill_switch"
    GROSS_EXPOSURE = "gross_exposure"
    SINGLE_SYMBOL = "single_symbol"
    CONCURRENT_TRADES = "concurrent_trades"
    NEW_TRADES_TODAY = "new_trades_today"
    INSUFFICIENT_CASH = "insufficient_cash"


@dataclass(frozen=True)
class PortfolioBacktestConfig:
    """Static configuration for a portfolio backtest.

    Position quantities default to the D-0004 approved
    initial_qty=10 / ladder_1_qty=10 / ladder_2_qty=20; overrides let
    a caller experiment with sizing. Risk limits default to D-0047's
    approved values.
    """

    initial_cash: float = 50000.0
    max_gross_exposure_fraction: float = 0.60
    max_single_symbol_fraction: float = 0.10
    max_concurrent_trades: int = 5
    max_daily_new_trades: int = 3
    daily_loss_kill_switch_fraction: float = 0.03
    cooldown_days_after_exit: int = 5
    override_initial_qty: Optional[int] = None
    override_ladder1_qty: Optional[int] = None
    override_ladder2_qty: Optional[int] = None


@dataclass(frozen=True)
class EquityPoint:
    bar_date: date
    cash: float
    positions_value: float
    equity: float
    open_positions: int


@dataclass(frozen=True)
class RejectionRecord:
    """One D-0047 rejection during simulation."""

    bar_date: date
    symbol: str
    reason: RejectionReason
    detail: str


@dataclass(frozen=True)
class PortfolioMetrics:
    initial_cash: float
    final_equity: float
    total_return: float
    total_pnl: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    profit_factor: float
    max_drawdown: float
    max_drawdown_fraction: float
    sharpe_ratio: float
    peak_equity: float
    total_rejections: int
    rejection_counts: Tuple[Tuple[str, int], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PortfolioBacktestResult:
    trades: Tuple[BacktestTrade, ...]
    equity_curve: Tuple[EquityPoint, ...]
    rejections: Tuple[RejectionRecord, ...]
    metrics: PortfolioMetrics
