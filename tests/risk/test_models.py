import unittest

from risk.models import (
    PortfolioRiskLimits, PortfolioSnapshot, PositionView,
    RiskCheck, RiskCheckResult, RiskVerdict,
)


class TestPortfolioRiskLimits(unittest.TestCase):
    def test_defaults_match_D_0047(self):
        L = PortfolioRiskLimits()
        self.assertAlmostEqual(L.max_gross_exposure_fraction, 0.60)
        self.assertAlmostEqual(L.max_single_symbol_fraction, 0.10)
        self.assertAlmostEqual(L.daily_loss_kill_switch_fraction, 0.03)
        # D-0079 (2026-10-06): the two COUNTS are no longer literals.
        # concurrent = floor(0.60 / 0.05) = 12 -- D-0047's 5 could only
        # ever reach 25% of equity against its own 60% ceiling.
        # daily = floor(0.25 * 12) = 3 -- unchanged in value, derived
        # in source. Full reasoning and the float trap behind the
        # Decimal arithmetic: tests/risk/test_d0079_derived_limits.py
        self.assertEqual(L.max_concurrent_trades, 12)
        self.assertEqual(L.max_daily_new_trades, 3)

    def test_fraction_bounds(self):
        with self.assertRaises(ValueError):
            PortfolioRiskLimits(max_gross_exposure_fraction=0.0)
        with self.assertRaises(ValueError):
            PortfolioRiskLimits(max_gross_exposure_fraction=1.5)

    def test_concurrent_trades_lower_bound(self):
        with self.assertRaises(ValueError):
            PortfolioRiskLimits(max_concurrent_trades=0)


class TestPositionView(unittest.TestCase):
    def test_valid(self):
        p = PositionView(symbol="AAPL", qty=10, market_value=1000.0)
        self.assertEqual(p.symbol, "AAPL")

    def test_negative_market_value_rejected(self):
        with self.assertRaises(ValueError):
            PositionView(symbol="X", qty=1, market_value=-1.0)


class TestPortfolioSnapshot(unittest.TestCase):
    def test_gross_exposure(self):
        s = PortfolioSnapshot(
            equity_current=50000.0, equity_at_day_open=50000.0,
            positions=(PositionView("AAPL", 10, 2500.0),
                       PositionView("TSLA", 5, 1800.0)),
        )
        self.assertAlmostEqual(s.gross_exposure(), 4300.0)

    def test_exposure_for_symbol_is_case_insensitive(self):
        s = PortfolioSnapshot(
            equity_current=1.0, equity_at_day_open=1.0,
            positions=(PositionView("aapl", 1, 100.0),
                       PositionView("AAPL", 1, 200.0)),
        )
        self.assertAlmostEqual(s.exposure_for_symbol("AAPL"), 300.0)


class TestRiskCheckResult(unittest.TestCase):
    def test_allowed_when_no_failures(self):
        r = RiskCheckResult(
            verdict=RiskVerdict.ALLOWED,
            checks=(RiskCheck("a", True), RiskCheck("b", True)),
        )
        self.assertTrue(r.allowed)
        self.assertEqual(r.violations(), ())

    def test_first_violation_reason(self):
        r = RiskCheckResult(
            verdict=RiskVerdict.VIOLATED,
            checks=(RiskCheck("a", True),
                    RiskCheck("b", False, "b failed"),
                    RiskCheck("c", False, "c failed")),
        )
        self.assertFalse(r.allowed)
        self.assertEqual(r.first_violation_reason(), "b failed")
        self.assertEqual(len(r.violations()), 2)


if __name__ == "__main__":
    unittest.main()
