"""Tests for HistoricalSimulator (D-0050 Phase 13)."""

import unittest
from datetime import date, timedelta
from typing import Dict, List, Tuple

from backtest.historical_simulator import (
    HistoricalSimulator, ReplayFeatures, BacktestResult,
    _build_replay_features, _ret, _annualized_vol, _forward_close,
)


def _ramp(start_px: float, end_px: float, n: int,
          start_date: date = date(2026, 1, 1)) -> List[Tuple[date, float]]:
    """Linear price ramp over n days."""
    return [(start_date + timedelta(days=i),
             start_px + (end_px - start_px) * i / max(1, n - 1))
            for i in range(n)]


def _flat(px: float, n: int,
          start_date: date = date(2026, 1, 1)) -> List[Tuple[date, float]]:
    return [(start_date + timedelta(days=i), px) for i in range(n)]


# --------------------------------------------------------------------
# Helper function tests
# --------------------------------------------------------------------

class TestHelpers(unittest.TestCase):
    def test_ret_simple(self):
        closes = [100.0, 101.0, 102.0, 105.0, 110.0]
        self.assertAlmostEqual(_ret(closes, 4), 10.0)

    def test_ret_insufficient_history(self):
        self.assertIsNone(_ret([100.0], 5))

    def test_annualized_vol_zero_on_flat(self):
        v = _annualized_vol([100.0] * 30, 22)
        self.assertAlmostEqual(v, 0.0, places=4)

    def test_annualized_vol_higher_on_choppy(self):
        import math
        choppy = [100.0 * (1 + 0.02 * (-1 if i % 2 else 1)) for i in range(30)]
        v = _annualized_vol(choppy, 22)
        self.assertGreater(v, 10.0)

    def test_forward_close_returns_none_without_enough_future(self):
        hist = _ramp(100, 110, 10)
        self.assertIsNone(_forward_close(hist, date(2026, 1, 8), 30))

    def test_forward_close_picks_correct_bar(self):
        hist = _ramp(100, 200, 50)
        got = _forward_close(hist, date(2026, 1, 5), 20)
        self.assertIsNotNone(got)


# --------------------------------------------------------------------
# ReplayFeatures (zero look-ahead guarantee)
# --------------------------------------------------------------------

class TestReplayFeatures(unittest.TestCase):
    def test_features_use_only_past_data(self):
        """Verify features computed on day D only see closes ≤ D."""
        hist = _ramp(100, 200, 200)  # 200-day climb
        day = hist[150][0]
        feats = _build_replay_features("TEST", hist, day,
                                        min_history=100, spy_hist=None)
        self.assertIsNotNone(feats)
        # price_on_day should match the close ON that day (not later)
        expected = next(c for d, c in hist if d == day)
        self.assertAlmostEqual(feats.price_on_day, expected)

    def test_insufficient_history_returns_none(self):
        hist = _ramp(100, 110, 50)
        feats = _build_replay_features("TEST", hist, hist[-1][0],
                                        min_history=100, spy_hist=None)
        self.assertIsNone(feats)

    def test_rel_strength_computed_when_spy_present(self):
        stock = _ramp(100, 120, 200)  # +20%
        spy = _ramp(400, 420, 200)    # +5%
        day = stock[150][0]
        feats = _build_replay_features("TEST", stock, day,
                                        min_history=100, spy_hist=spy)
        self.assertIsNotNone(feats.rel_strength_30d_pct)
        self.assertGreater(feats.rel_strength_30d_pct, 0)

    def test_rel_strength_none_without_spy(self):
        hist = _ramp(100, 120, 200)
        feats = _build_replay_features("TEST", hist, hist[150][0],
                                        min_history=100, spy_hist=None)
        self.assertIsNone(feats.rel_strength_30d_pct)


# --------------------------------------------------------------------
# Full backtest end-to-end
# --------------------------------------------------------------------

def _simple_scorer(feats: ReplayFeatures):
    """Rank purely by 30-day return (deterministic for the test)."""
    score = feats.return_30d_pct or 0.0
    return score, {"ret30": score}


class TestEndToEnd(unittest.TestCase):
    def test_backtest_picks_the_climbing_stock_over_falling_one(self):
        up = _ramp(100, 150, 300)        # +50%
        down = _ramp(100, 50, 300)       # -50%
        spy = _ramp(400, 420, 300)       # +5%

        histories = {"UP": up, "DOWN": down, "SPY": spy}

        def closes_provider(sym):
            return histories.get(sym, [])

        sim = HistoricalSimulator(
            closes_provider=closes_provider,
            scorer=_simple_scorer,
            picks_per_day=1,
            forward_days=10,
            min_history_days=100,
        )
        result = sim.run(
            universe=["UP", "DOWN", "SPY"],
            start=up[150][0], end=up[200][0],
        )
        # Every pick should be "UP" (the climbing stock)
        self.assertGreater(len(result.picks), 0)
        for p in result.picks:
            self.assertEqual(p.symbol, "UP")

    def test_stats_populated_after_run(self):
        up = _ramp(100, 200, 300)
        spy = _ramp(400, 440, 300)

        def closes_provider(sym):
            return {"UP": up, "SPY": spy}.get(sym, [])

        sim = HistoricalSimulator(
            closes_provider=closes_provider,
            scorer=_simple_scorer,
            picks_per_day=1,
            forward_days=20,
            min_history_days=100,
        )
        result = sim.run(universe=["UP", "SPY"],
                         start=up[150][0], end=up[200][0])
        self.assertGreater(result.completed_picks, 0)
        self.assertGreater(result.mean_return_pct, 0)
        self.assertGreater(result.win_rate_pct, 0)

    def test_scorer_exception_doesnt_crash_backtest(self):
        """A buggy scorer must not take the whole backtest down."""
        def bad_scorer(feats):
            raise RuntimeError("oops")

        up = _ramp(100, 150, 300)
        spy = _ramp(400, 420, 300)

        def closes_provider(sym):
            return {"UP": up, "SPY": spy}.get(sym, [])

        sim = HistoricalSimulator(
            closes_provider=closes_provider,
            scorer=bad_scorer,
            picks_per_day=1,
            forward_days=10,
            min_history_days=100,
        )
        result = sim.run(universe=["UP", "SPY"],
                         start=up[150][0], end=up[200][0])
        # No picks (every call raised, filtered out)
        self.assertEqual(result.total_picks, 0)

    def test_empty_universe_returns_empty_result(self):
        sim = HistoricalSimulator(
            closes_provider=lambda s: [],
            scorer=_simple_scorer,
            picks_per_day=3, forward_days=30, min_history_days=100,
        )
        result = sim.run(universe=[], start=date(2026, 1, 1),
                         end=date(2026, 2, 1))
        self.assertEqual(result.total_picks, 0)
        self.assertEqual(result.universe_size, 0)

    def test_to_dict_serializable(self):
        up = _ramp(100, 150, 300)
        spy = _ramp(400, 420, 300)
        sim = HistoricalSimulator(
            closes_provider=lambda s: {"UP": up, "SPY": spy}.get(s, []),
            scorer=_simple_scorer,
            picks_per_day=1, forward_days=10, min_history_days=100,
        )
        result = sim.run(universe=["UP", "SPY"],
                         start=up[150][0], end=up[160][0])
        import json
        s = json.dumps(result.to_dict())
        self.assertIn("mean_return_pct", s)
        self.assertIn("edge_over_baseline_pct", s)


if __name__ == "__main__":
    unittest.main()
