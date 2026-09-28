"""Backtest metric computations (B25 + B27c). Pure functions."""

from __future__ import annotations

import math
from datetime import date
from typing import Optional, Sequence, Tuple

from backtesting.models import BacktestMetrics, BacktestTrade


_TRADING_DAYS_PER_YEAR = 252


def compute_metrics(trades: Sequence[BacktestTrade]) -> BacktestMetrics:
    if not trades:
        return BacktestMetrics(
            total_trades=0, winning_trades=0, losing_trades=0,
            total_pnl=0.0, average_return=0.0, win_rate=0.0,
            profit_factor=0.0, max_drawdown=0.0, sharpe_ratio=0.0,
        )

    pnls = [t.pnl() for t in trades]
    returns = [t.return_fraction() for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    total_pnl = sum(pnls)
    win_rate = len(wins) / len(trades)
    average_return = sum(returns) / len(returns)

    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (
        float("inf") if gross_win > 0 else 0.0
    )

    max_drawdown = _max_drawdown(pnls)
    sharpe = _sharpe(returns)

    return BacktestMetrics(
        total_trades=len(trades),
        winning_trades=len(wins),
        losing_trades=len(losses),
        total_pnl=total_pnl,
        average_return=average_return,
        win_rate=win_rate,
        profit_factor=profit_factor,
        max_drawdown=max_drawdown,
        sharpe_ratio=sharpe,
    )


def _max_drawdown(pnls: Sequence[float]) -> float:
    """Maximum peak-to-trough drawdown over the equity curve."""
    if not pnls:
        return 0.0
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    return max_dd


def compute_cagr(
    initial_equity: float, final_equity: float,
    start: Optional[date], end: Optional[date],
) -> float:
    """Compound Annual Growth Rate. Returns 0.0 for degenerate inputs
    (non-positive initial, zero elapsed days, missing dates)."""
    if initial_equity <= 0 or final_equity <= 0 or start is None or end is None:
        return 0.0
    days = (end - start).days
    if days <= 0:
        return 0.0
    years = days / 365.25
    if years <= 0:
        return 0.0
    return (final_equity / initial_equity) ** (1.0 / years) - 1.0


def compute_annualized_sharpe(
    per_trade_returns: Sequence[float],
    trades_per_year: float,
) -> float:
    """Scales the per-trade Sharpe to an annualized figure. The
    standard scaling is `per_trade_sharpe * sqrt(trades_per_year)`.
    Returns 0.0 when there aren't enough trades or trades_per_year
    is non-positive."""
    if trades_per_year <= 0:
        return 0.0
    per_trade = _sharpe(per_trade_returns)
    return per_trade * math.sqrt(trades_per_year)


def _sharpe(returns: Sequence[float]) -> float:
    """Sharpe ratio with zero risk-free rate; annualization assumes
    each trade is one observation (not a daily return). This is a
    per-trade Sharpe, useful for comparing strategies with the same
    trade frequency. A future extension can annualize by trades/year."""
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(var)
    if std < 1e-12:
        return 0.0
    return mean / std
