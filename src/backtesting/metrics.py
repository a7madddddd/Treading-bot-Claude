"""Backtest metric computations (B25). Pure functions."""

from __future__ import annotations

import math
from typing import Sequence

from backtesting.models import BacktestMetrics, BacktestTrade


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
    if std == 0:
        return 0.0
    return mean / std
