"""Tests for walk-forward validation (B27b)."""

import unittest
from datetime import date, timedelta

from backtesting.models import Bar
from backtesting.walk_forward import (
    run_walk_forward, split_windows, _mean, _median, _stddev,
)


def _flat(days, price=100.0):
    return [Bar(bar_date=date(2026,1,1)+timedelta(days=i),
                open=price, high=price+0.5, low=price-0.5,
                close=price, volume=1000)
            for i in range(days)]


class TestSplitWindows(unittest.TestCase):
    def test_exact_two_windows(self):
        bars = {"X": _flat(20)}
        windows = split_windows(bars, window_trading_days=10)
        self.assertEqual(len(windows), 2)
        # Each window has 10 bars
        for start, end, per_sym in windows:
            self.assertEqual(len(per_sym["X"]), 10)

    def test_trailing_partial_dropped(self):
        # 25 bars, window=10 -> 2 windows of 10, trailing 5 dropped
        bars = {"X": _flat(25)}
        windows = split_windows(bars, window_trading_days=10)
        self.assertEqual(len(windows), 2)

    def test_too_few_bars(self):
        bars = {"X": _flat(5)}
        windows = split_windows(bars, window_trading_days=10)
        self.assertEqual(len(windows), 0)

    def test_multi_symbol_alignment(self):
        # Two symbols with slightly different date ranges
        bars = {
            "A": _flat(20),  # dates 0..19
            "B": [Bar(bar_date=date(2026,1,3)+timedelta(days=i),
                      open=100, high=100.5, low=99.5, close=100, volume=1000)
                  for i in range(20)],  # dates 2..21
        }
        windows = split_windows(bars, window_trading_days=11)
        self.assertGreaterEqual(len(windows), 1)


class TestHelpers(unittest.TestCase):
    def test_mean(self):
        self.assertAlmostEqual(_mean([1, 2, 3]), 2.0)
        self.assertEqual(_mean([]), 0.0)

    def test_median_odd(self):
        self.assertAlmostEqual(_median([3, 1, 2]), 2.0)

    def test_median_even(self):
        self.assertAlmostEqual(_median([1, 2, 3, 4]), 2.5)

    def test_stddev(self):
        # variance = ((1-2)^2 + (2-2)^2 + (3-2)^2) / 2 = 1 → std=1
        self.assertAlmostEqual(_stddev([1, 2, 3]), 1.0)

    def test_stddev_single(self):
        self.assertEqual(_stddev([5]), 0.0)


class TestRunWalkForward(unittest.TestCase):
    def test_empty_bars_empty_summary(self):
        s = run_walk_forward({}, window_trading_days=10)
        self.assertEqual(s.windows, ())

    def test_flat_market_produces_windows(self):
        bars = {"X": _flat(30)}
        s = run_walk_forward(bars, window_trading_days=10)
        self.assertEqual(len(s.windows), 3)
        # Each window: flat market, ~zero return
        for w in s.windows:
            self.assertAlmostEqual(w.total_return, 0.0, places=3)

    def test_summary_stats_populated(self):
        bars = {"X": _flat(30)}
        s = run_walk_forward(bars, window_trading_days=10)
        # All ~0 returns
        self.assertAlmostEqual(s.mean_return, 0.0, places=3)
        self.assertGreaterEqual(s.best_return, s.worst_return)


if __name__ == "__main__":
    unittest.main()
