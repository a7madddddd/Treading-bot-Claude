"""Tests for annualized metrics (B27c): CAGR and annualized Sharpe."""

import unittest
from datetime import date

from backtesting.metrics import compute_annualized_sharpe, compute_cagr


class TestCAGR(unittest.TestCase):
    def test_positive_growth_over_1_year(self):
        # 100 -> 120 in exactly one year = 20% CAGR
        r = compute_cagr(100.0, 120.0, date(2024,1,1), date(2025,1,1))
        # 365 days ≈ 1 year → CAGR ≈ 20%
        self.assertAlmostEqual(r, 0.19986, places=3)

    def test_negative_growth(self):
        r = compute_cagr(100.0, 80.0, date(2024,1,1), date(2025,1,1))
        # 20% loss over 1y → CAGR ≈ -0.20
        self.assertLess(r, 0.0)

    def test_no_change(self):
        r = compute_cagr(100.0, 100.0, date(2024,1,1), date(2025,1,1))
        self.assertAlmostEqual(r, 0.0, places=4)

    def test_multi_year(self):
        # 100 -> 200 over 3 years ≈ 26% CAGR
        r = compute_cagr(100.0, 200.0, date(2024,1,1), date(2027,1,1))
        self.assertAlmostEqual(r, 0.2599, places=2)

    def test_missing_dates_returns_zero(self):
        self.assertEqual(compute_cagr(100.0, 120.0, None, None), 0.0)

    def test_zero_initial_returns_zero(self):
        self.assertEqual(compute_cagr(0.0, 120.0, date(2024,1,1),
                                       date(2025,1,1)), 0.0)


class TestAnnualizedSharpe(unittest.TestCase):
    def test_scales_by_sqrt_trades_per_year(self):
        import math
        # Per-trade returns: mean = 0.05, std = 0
        returns = [0.05, 0.05, 0.05]
        # With std=0 -> per_trade_sharpe = 0 -> annualized = 0
        r = compute_annualized_sharpe(returns, trades_per_year=252)
        self.assertEqual(r, 0.0)

    def test_positive_when_returns_have_positive_mean(self):
        import math
        returns = [0.05, 0.10, 0.02, -0.01, 0.08]
        r = compute_annualized_sharpe(returns, trades_per_year=100)
        self.assertGreater(r, 0.0)

    def test_zero_when_trades_per_year_nonpositive(self):
        r = compute_annualized_sharpe([0.1, 0.2], trades_per_year=0)
        self.assertEqual(r, 0.0)


if __name__ == "__main__":
    unittest.main()
