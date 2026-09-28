import unittest
from datetime import date

from backtesting.models import (
    Bar, BacktestMetrics, BacktestTrade, ExitReason,
)


class TestBar(unittest.TestCase):
    def test_valid(self):
        b = Bar(bar_date=date(2026,1,1), open=100, high=105, low=99, close=102, volume=1000)
        self.assertEqual(b.close, 102)

    def test_low_gt_high_rejected(self):
        with self.assertRaises(ValueError):
            Bar(bar_date=date(2026,1,1), open=100, high=90, low=95, close=92, volume=1000)

    def test_open_outside_range(self):
        with self.assertRaises(ValueError):
            Bar(bar_date=date(2026,1,1), open=200, high=105, low=99, close=102, volume=1000)

    def test_negative_volume(self):
        with self.assertRaises(ValueError):
            Bar(bar_date=date(2026,1,1), open=100, high=105, low=99, close=102, volume=-1)


class TestBacktestTrade(unittest.TestCase):
    def _trade(self, entry=100.0, exit_price=110.0, shares=10):
        return BacktestTrade(
            symbol="X", entry_date=date(2026,1,1), entry_price=entry,
            initial_shares=shares,
            ladder1_fill_price=None, ladder1_fill_qty=0,
            ladder2_fill_price=None, ladder2_fill_qty=0,
            exit_date=date(2026,2,1), exit_price=exit_price,
            exit_reason=ExitReason.TRAILING_FLOOR_HIT,
            final_shares=shares, weighted_avg_entry_price=entry,
            trailing_activated=True, trailing_peak_threshold=None,
        )

    def test_pnl(self):
        t = self._trade(entry=100, exit_price=110, shares=10)
        self.assertAlmostEqual(t.pnl(), 100.0)

    def test_pnl_loss(self):
        t = self._trade(entry=100, exit_price=95, shares=10)
        self.assertAlmostEqual(t.pnl(), -50.0)

    def test_return_fraction(self):
        t = self._trade(entry=100, exit_price=110, shares=10)
        self.assertAlmostEqual(t.return_fraction(), 0.10)


class TestTransactionCostModel(unittest.TestCase):
    def test_defaults_are_zero(self):
        from backtesting.models import TransactionCostModel
        m = TransactionCostModel()
        self.assertEqual(m.buy_fill(100.0), 100.0)
        self.assertEqual(m.sell_fill(100.0), 100.0)
        self.assertEqual(m.commission(10), 0.0)

    def test_slippage_buy_worse(self):
        from backtesting.models import TransactionCostModel
        m = TransactionCostModel(slippage_bps_buy=5.0)
        self.assertAlmostEqual(m.buy_fill(100.0), 100.05)

    def test_slippage_sell_worse(self):
        from backtesting.models import TransactionCostModel
        m = TransactionCostModel(slippage_bps_sell=5.0)
        self.assertAlmostEqual(m.sell_fill(100.0), 99.95)

    def test_commission_per_share(self):
        from backtesting.models import TransactionCostModel
        m = TransactionCostModel(commission_per_share=0.01)
        self.assertAlmostEqual(m.commission(100), 1.0)

    def test_negative_slippage_rejected(self):
        from backtesting.models import TransactionCostModel
        with self.assertRaises(ValueError):
            TransactionCostModel(slippage_bps_buy=-1.0)

    def test_pnl_subtracts_commission(self):
        from backtesting.models import BacktestTrade, ExitReason
        from datetime import date
        t = BacktestTrade(
            symbol="X", entry_date=date(2026,1,1), entry_price=100,
            initial_shares=10,
            ladder1_fill_price=None, ladder1_fill_qty=0,
            ladder2_fill_price=None, ladder2_fill_qty=0,
            exit_date=date(2026,2,1), exit_price=110,
            exit_reason=ExitReason.END_OF_PERIOD,
            final_shares=10, weighted_avg_entry_price=100,
            trailing_activated=False, trailing_peak_threshold=None,
            total_commission=5.0,
        )
        # gross = (110-100)*10 = 100; net = 100 - 5 = 95
        self.assertAlmostEqual(t.pnl(), 95.0)


if __name__ == "__main__":
    unittest.main()
