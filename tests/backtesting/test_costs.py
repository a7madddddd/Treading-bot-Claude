"""Integration tests: cost model reduces P&L in both simulators."""

import unittest
from datetime import date, timedelta

from backtesting.models import (
    Bar, BacktestConfig, TransactionCostModel,
)
from backtesting.simulator import BacktestSimulator
from backtesting.portfolio_simulator import PortfolioSimulator
from backtesting.portfolio_models import PortfolioBacktestConfig


def _rally_bars(days=30, start_price=100.0, start=date(2026,1,1)):
    return [Bar(bar_date=start+timedelta(days=i),
                open=start_price+i, high=start_price+i+1,
                low=start_price+i-1, close=start_price+i, volume=1000)
            for i in range(days)]


class TestSingleSymbolCosts(unittest.TestCase):
    def test_no_cost_baseline(self):
        r = BacktestSimulator(BacktestConfig()).run(
            "X", _rally_bars(30) + _rally_bars(
                15, start_price=125, start=date(2026,2,1))
        )
        self.assertGreater(len(r.trades), 0)
        # No commissions with default zero cost model
        for t in r.trades:
            self.assertEqual(t.total_commission, 0.0)

    def test_slippage_reduces_pnl(self):
        bars = _rally_bars(30) + _rally_bars(
            15, start_price=125, start=date(2026,2,1)
        )
        r_zero = BacktestSimulator(BacktestConfig()).run("X", bars)
        r_slip = BacktestSimulator(BacktestConfig(
            cost_model=TransactionCostModel(
                slippage_bps_buy=50.0,  # 0.50%
                slippage_bps_sell=50.0,
            )
        )).run("X", bars)
        gross_zero = sum(t.pnl() for t in r_zero.trades)
        gross_slip = sum(t.pnl() for t in r_slip.trades)
        self.assertLess(gross_slip, gross_zero)

    def test_commission_added_to_total_commission(self):
        bars = _rally_bars(30)
        r = BacktestSimulator(BacktestConfig(
            cost_model=TransactionCostModel(commission_per_share=0.10)
        )).run("X", bars)
        self.assertGreater(len(r.trades), 0)
        # Commission: entry 10 shares + exit 10 shares = 20 * 0.10 = 2.0
        # (with no ladder fills)
        for t in r.trades:
            expected = (t.initial_shares + t.ladder1_fill_qty
                        + t.ladder2_fill_qty
                        + t.final_shares) * 0.10
            self.assertAlmostEqual(t.total_commission, expected)


class TestPortfolioCosts(unittest.TestCase):
    def test_slippage_reduces_portfolio_equity(self):
        bars = {"X": _rally_bars(30)}
        r_zero = PortfolioSimulator(PortfolioBacktestConfig()).run(bars)
        r_slip = PortfolioSimulator(PortfolioBacktestConfig(
            cost_model=TransactionCostModel(
                slippage_bps_buy=50.0, slippage_bps_sell=50.0,
            )
        )).run(bars)
        self.assertLess(r_slip.metrics.final_equity,
                        r_zero.metrics.final_equity)


if __name__ == "__main__":
    unittest.main()
