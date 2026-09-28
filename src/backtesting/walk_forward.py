"""Walk-forward validation (B27b).

Splits a historical bar sequence into non-overlapping consecutive
windows and runs a portfolio backtest independently over each. The
aggregated summary shows whether the strategy is robust to
different market regimes (a strategy that returns +30% in one
window and -15% in the next is not the same story as one that
returns +5% consistently across all windows).

Design:
  - Input: a mapping symbol → list[Bar] (pre-sorted ascending by
    date) and a window size in trading days.
  - Splits the full timeline into consecutive windows of exactly
    that many trading days. A trailing partial window is dropped
    (no partial-window bias in the aggregate).
  - Runs one PortfolioSimulator per window with the same
    PortfolioBacktestConfig -- each window starts with the initial
    cash fresh, so windows are independent.
  - Aggregates: mean / median / std of per-window total_return,
    win rate, max drawdown, per-window CAGR, best and worst windows.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Mapping, Sequence, Tuple

from backtesting.models import Bar
from backtesting.portfolio_simulator import PortfolioSimulator
from backtesting.portfolio_models import (
    PortfolioBacktestConfig, PortfolioBacktestResult,
)


@dataclass(frozen=True)
class WalkForwardWindow:
    """One period's isolated backtest result."""

    window_index: int
    start: date
    end: date
    trading_days: int
    result: PortfolioBacktestResult

    @property
    def total_return(self) -> float:
        return self.result.metrics.total_return

    @property
    def cagr(self) -> float:
        return self.result.metrics.cagr


@dataclass(frozen=True)
class WalkForwardSummary:
    windows: Tuple[WalkForwardWindow, ...]
    mean_return: float
    median_return: float
    stddev_return: float
    best_return: float
    worst_return: float
    positive_windows: int
    negative_windows: int
    max_drawdown_across_windows: float


def _sorted_all_dates(bars_by_symbol: Mapping[str, Sequence[Bar]]) -> List[date]:
    seen = set()
    for bars in bars_by_symbol.values():
        for b in bars:
            seen.add(b.bar_date)
    return sorted(seen)


def split_windows(
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    window_trading_days: int,
) -> Tuple[Tuple[date, date, Dict[str, List[Bar]]], ...]:
    """Splits every symbol's bars into non-overlapping windows of
    exactly `window_trading_days` days each, keyed by the union of
    trading dates. Returns a tuple of (start_date, end_date, per-symbol
    bars-in-window) triples."""

    if window_trading_days < 2:
        raise ValueError("window_trading_days must be >= 2")

    all_dates = _sorted_all_dates(bars_by_symbol)
    if len(all_dates) < window_trading_days:
        return ()

    n_windows = len(all_dates) // window_trading_days
    windows = []
    for i in range(n_windows):
        start_dt = all_dates[i * window_trading_days]
        end_dt = all_dates[(i + 1) * window_trading_days - 1]
        per_symbol: Dict[str, List[Bar]] = {}
        for sym, bars in bars_by_symbol.items():
            per_symbol[sym] = [b for b in bars
                               if start_dt <= b.bar_date <= end_dt]
        windows.append((start_dt, end_dt, per_symbol))
    return tuple(windows)


def run_walk_forward(
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    *,
    window_trading_days: int = 252,
    config: PortfolioBacktestConfig = None,
) -> WalkForwardSummary:
    """Runs an independent portfolio backtest per window and
    aggregates the results. Default window = 252 trading days
    (~1 year)."""

    cfg = config or PortfolioBacktestConfig()
    slices = split_windows(bars_by_symbol, window_trading_days)
    if not slices:
        return WalkForwardSummary(
            windows=(), mean_return=0.0, median_return=0.0,
            stddev_return=0.0, best_return=0.0, worst_return=0.0,
            positive_windows=0, negative_windows=0,
            max_drawdown_across_windows=0.0,
        )

    windows: List[WalkForwardWindow] = []
    for idx, (start_dt, end_dt, per_sym) in enumerate(slices):
        r = PortfolioSimulator(cfg).run(per_sym)
        n_days = sum(1 for _ in _sorted_all_dates(per_sym))
        windows.append(WalkForwardWindow(
            window_index=idx, start=start_dt, end=end_dt,
            trading_days=n_days, result=r,
        ))

    returns = [w.total_return for w in windows]
    return WalkForwardSummary(
        windows=tuple(windows),
        mean_return=_mean(returns),
        median_return=_median(returns),
        stddev_return=_stddev(returns),
        best_return=max(returns),
        worst_return=min(returns),
        positive_windows=sum(1 for r in returns if r > 0),
        negative_windows=sum(1 for r in returns if r < 0),
        max_drawdown_across_windows=max(
            w.result.metrics.max_drawdown_fraction for w in windows
        ),
    )


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _median(xs: Sequence[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 == 1 else (s[mid - 1] + s[mid]) / 2.0


def _stddev(xs: Sequence[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(var)
