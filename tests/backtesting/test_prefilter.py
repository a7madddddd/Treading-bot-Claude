"""Tests for BacktestUniversePrefilter (B27d)."""

import unittest
from datetime import date, timedelta

from backtesting.models import Bar
from backtesting.prefilter import BacktestUniversePrefilter
from d0026.config import UniverseSelectionConfig


def _series(n, *, start_price=100.0, drift=0.5, high_off=1.0,
            low_off=1.0, vol=1_000_000, start_date=date(2026, 1, 1)):
    bars = []
    p = start_price
    for i in range(n):
        d = start_date + timedelta(days=i)
        o = p
        c = p + drift
        h = max(o, c) + high_off
        l = min(o, c) - low_off
        bars.append(Bar(bar_date=d, open=o, high=h, low=l,
                        close=c, volume=vol))
        p = c
    return bars


class TestBuildCandidates(unittest.TestCase):
    def test_rejects_insufficient_history(self):
        # 20 bars < 31 min bars
        pf = BacktestUniversePrefilter(
            bars_by_symbol={"AAA": _series(20)},
            config=UniverseSelectionConfig(),
        )
        approved = pf.approved_symbols_on(date(2026, 1, 20))
        self.assertEqual(approved, frozenset())

    def test_requires_bar_on_date(self):
        # Sufficient history but no bar on requested date
        bars = _series(35)
        pf = BacktestUniversePrefilter(
            bars_by_symbol={"AAA": bars},
            config=UniverseSelectionConfig(),
        )
        # Ask for a date beyond the series
        approved = pf.approved_symbols_on(date(2026, 12, 31))
        self.assertEqual(approved, frozenset())


class TestApprovedSet(unittest.TestCase):
    def test_accepts_good_symbol(self):
        # 60 bars, moderate ATR fraction and rising momentum.
        bars = _series(60, start_price=100.0, drift=0.5,
                       high_off=1.5, low_off=1.5)
        pf = BacktestUniversePrefilter(
            bars_by_symbol={"AAA": bars},
            config=UniverseSelectionConfig(),
        )
        approved = pf.approved_symbols_on(bars[-1].bar_date)
        self.assertIn("AAA", approved)

    def test_rejects_flat_zero_atr(self):
        # ATR/price ~= 0 (very tight range) => below min_atr_fraction 1%.
        bars = _series(60, start_price=100.0, drift=0.0,
                       high_off=0.001, low_off=0.001)
        pf = BacktestUniversePrefilter(
            bars_by_symbol={"AAA": bars},
            config=UniverseSelectionConfig(),
        )
        approved = pf.approved_symbols_on(bars[-1].bar_date)
        self.assertNotIn("AAA", approved)

    def test_rejects_extreme_atr(self):
        # ATR/price >> 5%
        bars = _series(60, start_price=100.0, drift=0.5,
                       high_off=20.0, low_off=20.0)
        pf = BacktestUniversePrefilter(
            bars_by_symbol={"AAA": bars},
            config=UniverseSelectionConfig(),
        )
        approved = pf.approved_symbols_on(bars[-1].bar_date)
        self.assertNotIn("AAA", approved)


class TestTopNAndRanking(unittest.TestCase):
    def test_top_n_caps_output(self):
        # 15 identical-ish symbols; top_n=3 caps output.
        cfg = UniverseSelectionConfig(top_n=3)
        bars_by = {}
        for i in range(15):
            bars_by[f"SYM{i:02d}"] = _series(
                60, start_price=100.0 + i, drift=0.5,
                high_off=1.5, low_off=1.5,
            )
        pf = BacktestUniversePrefilter(bars_by_symbol=bars_by, config=cfg)
        approved = pf.approved_symbols_on(bars_by["SYM00"][-1].bar_date)
        self.assertLessEqual(len(approved), 3)


class TestPortfolioIntegration(unittest.TestCase):
    def test_prefilter_gates_entries(self):
        from backtesting.portfolio_models import (
            PortfolioBacktestConfig, RejectionReason,
        )
        from backtesting.portfolio_simulator import PortfolioSimulator

        good = _series(60, start_price=100.0, drift=0.5,
                       high_off=1.5, low_off=1.5)
        # A "bad" symbol: sub-1% ATR band => will be filtered out.
        bad = _series(60, start_price=100.0, drift=0.0,
                      high_off=0.001, low_off=0.001)

        pf = BacktestUniversePrefilter(
            bars_by_symbol={"GOOD": good, "BAD": bad},
            config=UniverseSelectionConfig(),
        )
        cfg = PortfolioBacktestConfig(
            initial_cash=100_000.0,
            universe_prefilter=pf,
        )
        result = PortfolioSimulator(cfg).run({"GOOD": good, "BAD": bad})

        # BAD should be rejected as NOT_IN_APPROVED_UNIVERSE at least once.
        got_bad_rejection = any(
            r.symbol == "BAD"
            and r.reason == RejectionReason.NOT_IN_APPROVED_UNIVERSE
            for r in result.rejections
        )
        self.assertTrue(got_bad_rejection)


if __name__ == "__main__":
    unittest.main()
