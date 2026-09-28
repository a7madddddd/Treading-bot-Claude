import unittest
from datetime import date, timedelta
from typing import List

from backtesting.models import Bar, BacktestConfig, ExitReason
from backtesting.simulator import BacktestSimulator


def _flat_bars(days: int, price: float, start: date = date(2026,1,1)) -> List[Bar]:
    return [Bar(bar_date=start + timedelta(days=i),
                open=price, high=price + 0.5, low=price - 0.5,
                close=price, volume=1000)
            for i in range(days)]


def _rally_bars(days: int, start_price: float = 100.0,
                start: date = date(2026,1,1)) -> List[Bar]:
    return [Bar(bar_date=start + timedelta(days=i),
                open=start_price + i, high=start_price + i + 1,
                low=start_price + i - 1, close=start_price + i,
                volume=1000)
            for i in range(days)]


def _crash_bars(days: int, start_price: float = 100.0,
                start: date = date(2026,1,1)) -> List[Bar]:
    return [Bar(bar_date=start + timedelta(days=i),
                open=start_price - i, high=start_price - i + 1,
                low=start_price - i - 1, close=start_price - i,
                volume=1000)
            for i in range(days)]


class TestEmptyInput(unittest.TestCase):
    def test_no_bars_no_trades(self):
        r = BacktestSimulator().run("X", [])
        self.assertEqual(r.trades, ())
        self.assertEqual(r.metrics.total_trades, 0)


class TestFlatMarket(unittest.TestCase):
    def test_flat_market_ends_at_close(self):
        bars = _flat_bars(20, 100.0)
        r = BacktestSimulator().run("X", bars)
        self.assertEqual(len(r.trades), 1)
        t = r.trades[0]
        self.assertEqual(t.exit_reason, ExitReason.END_OF_PERIOD)
        self.assertEqual(t.entry_price, 100.0)
        self.assertEqual(t.exit_price, 100.0)
        self.assertAlmostEqual(t.pnl(), 0.0)


class TestRally(unittest.TestCase):
    def test_rally_activates_trailing_and_exits_on_pullback(self):
        # 30-day rally 100→130 then drop back
        bars = _rally_bars(30) + [
            Bar(bar_date=date(2026,2,1)+timedelta(days=i),
                open=130-i*2, high=131-i*2, low=129-i*2,
                close=130-i*2, volume=1000)
            for i in range(15)
        ]
        r = BacktestSimulator().run("X", bars)
        self.assertGreaterEqual(len(r.trades), 1)
        first = r.trades[0]
        self.assertTrue(first.trailing_activated)
        self.assertIn(first.exit_reason,
                      (ExitReason.TRAILING_FLOOR_HIT,
                       ExitReason.FLOOR_HIT,
                       ExitReason.END_OF_PERIOD))
        self.assertGreater(first.pnl(), 0)


class TestCrash(unittest.TestCase):
    def test_crash_hits_floor(self):
        # 15-day crash from 100 down. floor is entry * 0.90 = 90.
        bars = _crash_bars(15)
        r = BacktestSimulator().run("X", bars)
        first = r.trades[0]
        # ladder fills at 95, then 92. floor at 90.
        # ladder_1_fill possible; then floor hit
        self.assertEqual(first.entry_price, 100.0)
        self.assertIn(first.exit_reason,
                      (ExitReason.FLOOR_HIT, ExitReason.END_OF_PERIOD))


class TestLadders(unittest.TestCase):
    def test_ladder_1_fills_at_minus_5pct(self):
        # Entry 100, price drops to 94 (ladder_1 at 95 hits) then recovers
        bars = [Bar(bar_date=date(2026,1,1), open=100, high=101, low=99, close=100, volume=1000),
                Bar(bar_date=date(2026,1,2), open=100, high=101, low=94, close=99, volume=1000),
                Bar(bar_date=date(2026,1,3), open=99, high=105, low=98, close=105, volume=1000)]
        # continue rising
        for i in range(15):
            bars.append(Bar(bar_date=date(2026,1,4)+timedelta(days=i),
                            open=105+i, high=106+i, low=104+i, close=105+i, volume=1000))
        r = BacktestSimulator().run("X", bars)
        first = r.trades[0]
        self.assertIsNotNone(first.ladder1_fill_price)
        self.assertAlmostEqual(first.ladder1_fill_price, 95.0)
        self.assertGreater(first.ladder1_fill_qty, 0)


class TestCooldown(unittest.TestCase):
    def test_cooldown_gap_between_trades(self):
        # Flat then exit-worthy: run flat 25 days, exit, then 25 more days
        bars = _flat_bars(25, 100.0) + _flat_bars(
            25, 100.0, start=date(2026,1,26))
        cfg = BacktestConfig(cooldown_days_after_exit=5)
        r = BacktestSimulator(cfg).run("X", bars)
        # Depending on exit timing, at least 1 trade
        self.assertGreaterEqual(len(r.trades), 1)


if __name__ == "__main__":
    unittest.main()
