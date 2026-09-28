import unittest
from datetime import date

from backtesting.metrics import compute_metrics
from backtesting.models import BacktestTrade, ExitReason


def _t(entry: float, exit_price: float, shares: int = 10) -> BacktestTrade:
    return BacktestTrade(
        symbol="X", entry_date=date(2026,1,1), entry_price=entry,
        initial_shares=shares,
        ladder1_fill_price=None, ladder1_fill_qty=0,
        ladder2_fill_price=None, ladder2_fill_qty=0,
        exit_date=date(2026,2,1), exit_price=exit_price,
        exit_reason=ExitReason.END_OF_PERIOD,
        final_shares=shares, weighted_avg_entry_price=entry,
        trailing_activated=False, trailing_peak_threshold=None,
    )


class TestComputeMetrics(unittest.TestCase):
    def test_empty(self):
        m = compute_metrics(())
        self.assertEqual(m.total_trades, 0)
        self.assertEqual(m.total_pnl, 0.0)

    def test_all_winners(self):
        m = compute_metrics([_t(100, 110), _t(100, 105)])
        self.assertEqual(m.total_trades, 2)
        self.assertEqual(m.winning_trades, 2)
        self.assertEqual(m.losing_trades, 0)
        self.assertAlmostEqual(m.total_pnl, 150.0)
        self.assertAlmostEqual(m.win_rate, 1.0)

    def test_mixed(self):
        m = compute_metrics([_t(100, 110), _t(100, 90)])
        self.assertEqual(m.winning_trades, 1)
        self.assertEqual(m.losing_trades, 1)
        self.assertAlmostEqual(m.total_pnl, 0.0)  # +100 - 100
        self.assertAlmostEqual(m.win_rate, 0.5)
        # profit_factor = gross_win / gross_loss = 100 / 100 = 1
        self.assertAlmostEqual(m.profit_factor, 1.0)

    def test_max_drawdown(self):
        # Equity curve: +100, -50, -50, +100 => peaks/troughs
        # equity: 100, 50, 0, 100. peak=100 at end; max_dd=100 (100→0)
        m = compute_metrics([_t(100, 110), _t(100, 95), _t(100, 95), _t(100, 110)])
        self.assertAlmostEqual(m.max_drawdown, 100.0)

    def test_sharpe_positive_when_positive_mean(self):
        m = compute_metrics([_t(100, 110), _t(100, 108), _t(100, 105)])
        self.assertGreater(m.sharpe_ratio, 0.0)

    def test_profit_factor_infinite_no_losses(self):
        m = compute_metrics([_t(100, 110), _t(100, 105)])
        self.assertEqual(m.profit_factor, float("inf"))


if __name__ == "__main__":
    unittest.main()
