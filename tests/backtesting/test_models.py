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


if __name__ == "__main__":
    unittest.main()
