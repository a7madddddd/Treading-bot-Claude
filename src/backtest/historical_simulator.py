"""Historical backtest simulator for the TradeEvaluator (D-0050 Phase 13).

Proves (or disproves) that the ranker + evaluator has real predictive
edge BEFORE we trust it with live paper trading. Replays the scorer
on each historical trading day in a window, picks the top-N each
day, and measures forward 30-day returns against a benchmark.

Design:
  - Reads a universe of symbols from a CSV or hard-coded list.
  - For each trading day in [start, end]:
      1. Build a SymbolResearch per candidate using ONLY data
         available up to and including that day (no look-ahead).
      2. Score each via TradeEvaluator (reusing production code).
      3. Record Top-N by score + their forward 30-day close-to-close
         return.
  - Produce aggregate stats: win rate, mean return, median, max
    drawdown per pick, Sharpe-like ratio.
  - Compare to a random-pick baseline (same N picks drawn randomly
    from the surviving universe).

Data source: historical closes come from a callable
``closes_provider(symbol, date) -> List[Tuple[date, dict]]`` which
production wires to PolygonSource.get_aggregates. Tests pass synthetic
providers to keep the suite deterministic.

Advisory only (CLAUDE.md §5). NEVER trades.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Dict, List, Optional, Sequence, Tuple


@dataclass
class DailyPick:
    pick_date: date
    symbol: str
    score: float
    entry_price: float
    forward_30d_close: Optional[float]
    forward_30d_return_pct: Optional[float]
    score_breakdown: Dict[str, float] = field(default_factory=dict)


@dataclass
class BacktestResult:
    window_start: date
    window_end: date
    universe_size: int
    picks_per_day: int
    picks: List[DailyPick]
    # Aggregate stats (populated by compute_stats):
    total_picks: int = 0
    completed_picks: int = 0   # picks that have forward-30d data
    win_rate_pct: float = 0.0
    mean_return_pct: float = 0.0
    median_return_pct: float = 0.0
    best_return_pct: float = 0.0
    worst_return_pct: float = 0.0
    sharpe_like: float = 0.0
    # Random baseline:
    baseline_win_rate_pct: float = 0.0
    baseline_mean_return_pct: float = 0.0

    def to_dict(self) -> dict:
        return {
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "universe_size": self.universe_size,
            "picks_per_day": self.picks_per_day,
            "total_picks": self.total_picks,
            "completed_picks": self.completed_picks,
            "win_rate_pct": round(self.win_rate_pct, 2),
            "mean_return_pct": round(self.mean_return_pct, 3),
            "median_return_pct": round(self.median_return_pct, 3),
            "best_return_pct": round(self.best_return_pct, 3),
            "worst_return_pct": round(self.worst_return_pct, 3),
            "sharpe_like": round(self.sharpe_like, 3),
            "baseline_win_rate_pct": round(self.baseline_win_rate_pct, 2),
            "baseline_mean_return_pct": round(self.baseline_mean_return_pct, 3),
            "edge_over_baseline_pct": round(
                self.mean_return_pct - self.baseline_mean_return_pct, 3),
        }


class HistoricalSimulator:
    """Walks every trading day in a window and replays the scorer.

    Zero look-ahead: on day D, we score using only closes up to D;
    forward returns use closes AFTER D.
    """

    def __init__(
        self,
        *,
        closes_provider: Callable[[str], List[Tuple[date, float]]],
        scorer: Callable[["ReplayFeatures"], Tuple[float, Dict[str, float]]],
        picks_per_day: int = 3,
        forward_days: int = 30,
        min_history_days: int = 100,
    ) -> None:
        self._closes_provider = closes_provider
        self._scorer = scorer
        self._picks_per_day = picks_per_day
        self._forward_days = forward_days
        self._min_history = min_history_days

    def run(self, *, universe: Sequence[str],
            start: date, end: date,
            seed: int = 42) -> BacktestResult:
        """Replay every trading day between start and end (inclusive).
        Returns picks + aggregate stats."""
        # Pre-load every symbol's full history once.
        histories: Dict[str, List[Tuple[date, float]]] = {}
        for sym in universe:
            try:
                h = self._closes_provider(sym)
            except Exception:  # noqa: BLE001
                h = []
            if h and len(h) >= self._min_history:
                histories[sym] = sorted(h, key=lambda t: t[0])

        picks: List[DailyPick] = []

        # Walk every day where SPY (as proxy for "trading day") has data.
        spy_hist = histories.get("SPY") or _any_history(histories)
        if not spy_hist:
            return BacktestResult(
                window_start=start, window_end=end,
                universe_size=len(histories),
                picks_per_day=self._picks_per_day,
                picks=[],
            )
        trading_days = [d for d, _ in spy_hist
                        if start <= d <= end]

        for day in trading_days:
            # Rank every symbol whose history covers [day - min_history, day + forward_days].
            scored: List[Tuple[str, float, Dict[str, float], float]] = []
            for sym, hist in histories.items():
                feats = _build_replay_features(sym, hist, day,
                                                self._min_history,
                                                histories.get("SPY"))
                if feats is None:
                    continue
                try:
                    score, breakdown = self._scorer(feats)
                except Exception:  # noqa: BLE001
                    continue
                scored.append((sym, score, breakdown, feats.price_on_day))

            scored.sort(key=lambda t: -t[1])
            top = scored[: self._picks_per_day]
            for sym, score, breakdown, entry_px in top:
                forward_close = _forward_close(histories[sym], day,
                                                self._forward_days)
                fwd_ret = None
                if forward_close is not None and entry_px > 0:
                    fwd_ret = (forward_close - entry_px) / entry_px * 100
                picks.append(DailyPick(
                    pick_date=day, symbol=sym, score=score,
                    entry_price=entry_px,
                    forward_30d_close=forward_close,
                    forward_30d_return_pct=fwd_ret,
                    score_breakdown=breakdown,
                ))

        result = BacktestResult(
            window_start=start, window_end=end,
            universe_size=len(histories),
            picks_per_day=self._picks_per_day,
            picks=picks,
        )
        _compute_stats(result)
        _compute_random_baseline(result, histories, trading_days,
                                  self._forward_days, seed)
        return result


# ---------------------------------------------------------------------------
# Zero-look-ahead feature builder
# ---------------------------------------------------------------------------

@dataclass
class ReplayFeatures:
    """A reduced SymbolResearch that only uses price data up to and
    including ``as_of_date``. The scorer in HistoricalSimulator uses
    these fields exclusively — no live API calls, no look-ahead."""
    symbol: str
    as_of_date: date
    price_on_day: float
    return_5d_pct: Optional[float]
    return_30d_pct: Optional[float]
    return_90d_pct: Optional[float]
    volatility_30d_pct: Optional[float]
    rel_strength_30d_pct: Optional[float]
    volume_ratio_30d: Optional[float] = None


def _build_replay_features(
    symbol: str,
    hist: List[Tuple[date, float]],
    day: date,
    min_history: int,
    spy_hist: Optional[List[Tuple[date, float]]],
) -> Optional[ReplayFeatures]:
    """Returns None if there's insufficient history BEFORE day."""
    # closes_at_or_before: everything on or before `day`.
    closes_before = [c for d, c in hist if d <= day]
    if len(closes_before) < min_history:
        return None
    latest = closes_before[-1]
    r5 = _ret(closes_before, 5)
    r30 = _ret(closes_before, 21)
    r90 = _ret(closes_before, 63)
    vol30 = _annualized_vol(closes_before, 22)

    rs30 = None
    if r30 is not None and spy_hist:
        spy_closes = [c for d, c in spy_hist if d <= day]
        spy_r30 = _ret(spy_closes, 21)
        if spy_r30 is not None:
            rs30 = r30 - spy_r30

    return ReplayFeatures(
        symbol=symbol, as_of_date=day, price_on_day=latest,
        return_5d_pct=r5, return_30d_pct=r30, return_90d_pct=r90,
        volatility_30d_pct=vol30, rel_strength_30d_pct=rs30,
    )


def _ret(closes: List[float], lookback: int) -> Optional[float]:
    if len(closes) < lookback + 1:
        return None
    old = closes[-lookback - 1]
    new = closes[-1]
    if old <= 0:
        return None
    return (new - old) / old * 100


def _annualized_vol(closes: List[float], window: int) -> Optional[float]:
    if len(closes) < window + 1:
        return None
    import math
    recent = closes[-(window + 1):]
    rets = [math.log(recent[i] / recent[i - 1])
            for i in range(1, len(recent)) if recent[i - 1] > 0]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252) * 100


def _forward_close(hist: List[Tuple[date, float]], from_day: date,
                   forward_days: int) -> Optional[float]:
    future = [c for d, c in hist if d > from_day]
    if len(future) < forward_days:
        return None
    return future[forward_days - 1]


def _any_history(histories: Dict[str, List[Tuple[date, float]]]
                 ) -> Optional[List[Tuple[date, float]]]:
    if not histories:
        return None
    return max(histories.values(), key=len)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def _compute_stats(result: BacktestResult) -> None:
    completed = [p for p in result.picks
                 if p.forward_30d_return_pct is not None]
    result.total_picks = len(result.picks)
    result.completed_picks = len(completed)
    if not completed:
        return
    returns = [p.forward_30d_return_pct for p in completed]
    result.win_rate_pct = (
        sum(1 for r in returns if r > 0) / len(returns) * 100)
    result.mean_return_pct = statistics.mean(returns)
    result.median_return_pct = statistics.median(returns)
    result.best_return_pct = max(returns)
    result.worst_return_pct = min(returns)
    if len(returns) >= 2:
        sd = statistics.stdev(returns)
        if sd > 0:
            result.sharpe_like = result.mean_return_pct / sd


def _compute_random_baseline(
    result: BacktestResult,
    histories: Dict[str, List[Tuple[date, float]]],
    trading_days: List[date],
    forward_days: int,
    seed: int,
) -> None:
    """What would N random picks per day have returned on the same
    universe? This is the bar our ranker must beat."""
    import random
    rng = random.Random(seed)
    picks = []
    symbols = list(histories.keys())
    for day in trading_days:
        eligible = []
        for sym in symbols:
            hist = histories[sym]
            closes_before = [c for d, c in hist if d <= day]
            if not closes_before:
                continue
            fc = _forward_close(hist, day, forward_days)
            if fc is None:
                continue
            px = closes_before[-1]
            if px <= 0:
                continue
            fwd = (fc - px) / px * 100
            eligible.append(fwd)
        if not eligible:
            continue
        rng.shuffle(eligible)
        for r in eligible[: result.picks_per_day]:
            picks.append(r)
    if not picks:
        return
    result.baseline_win_rate_pct = (
        sum(1 for r in picks if r > 0) / len(picks) * 100)
    result.baseline_mean_return_pct = statistics.mean(picks)
