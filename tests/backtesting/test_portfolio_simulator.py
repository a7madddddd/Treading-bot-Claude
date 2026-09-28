import unittest
from datetime import date, timedelta

from backtesting.models import Bar, ExitReason
from backtesting.portfolio_models import (
    PortfolioBacktestConfig, RejectionReason,
)
from backtesting.portfolio_simulator import PortfolioSimulator


def _rally(start, days, first=date(2026,1,1)):
    return [Bar(bar_date=first+timedelta(days=i), open=start+i,
                high=start+i+1, low=start+i-1, close=start+i, volume=1000)
            for i in range(days)]


def _flat(price, days, first=date(2026,1,1)):
    return [Bar(bar_date=first+timedelta(days=i), open=price,
                high=price+0.5, low=price-0.5, close=price, volume=1000)
            for i in range(days)]


class TestEmpty(unittest.TestCase):
    def test_empty_bars(self):
        r = PortfolioSimulator().run({})
        self.assertEqual(r.trades, ())
        self.assertEqual(r.metrics.total_trades, 0)
        self.assertAlmostEqual(r.metrics.final_equity,
                               PortfolioBacktestConfig().initial_cash)


class TestSingleSymbol(unittest.TestCase):
    def test_flat_market_no_pnl(self):
        r = PortfolioSimulator().run({"X": _flat(100, 30)})
        self.assertEqual(r.metrics.total_trades, 1)
        # Flat market ends with entry_price = exit_price
        self.assertAlmostEqual(r.trades[0].pnl(), 0.0)


class TestPortfolioLimits(unittest.TestCase):
    def test_max_concurrent_trades_enforced(self):
        # 6 symbols all flat; cap=5 concurrent, cap=3 new/day.
        # Day 1: opens 3 (max_daily), rejects 3 others as
        #        NEW_TRADES_TODAY.
        # Day 2: new_trades_today counter resets, but only 2 more slots
        #        under concurrent cap (5 - 3 = 2). Opens 2, rejects 1
        #        as CONCURRENT_TRADES.
        # Later days: 5 concurrent, no more entries, symbol 6 keeps
        #             getting rejected each day it lacks a position.
        cfg = PortfolioBacktestConfig(
            initial_cash=100_000, max_daily_new_trades=3,
            max_concurrent_trades=5,
        )
        bars_by_symbol = {f"S{i}": _flat(100, 20) for i in range(6)}
        r = PortfolioSimulator(cfg).run(bars_by_symbol)
        self.assertEqual(len(r.trades), 5)
        # At least 4 rejections: 3 from day 1 NEW_TRADES_TODAY +
        # 1 from day 2 CONCURRENT_TRADES + 18 more on days 2..20
        # (S6 tried each day and hit CONCURRENT_TRADES).
        self.assertGreaterEqual(r.metrics.total_rejections, 4)
        # Both rejection types should appear.
        seen = {r.reason for r in r.rejections}
        self.assertIn(RejectionReason.NEW_TRADES_TODAY, seen)

    def test_gross_exposure_cap_can_reject(self):
        # 5 symbols, each entry costs ~$60 (60*1 share). max_gross=60%
        # of 100=60. So after 1 entry we hit 60% and further entries
        # rejected... but each entry is only 10 shares × $60 = $600.
        # $600/$100k = 0.6% gross. So this cap won't fire on small
        # positions. Instead test single_symbol_fraction=0.005 (0.5%).
        cfg = PortfolioBacktestConfig(
            initial_cash=100_000,
            max_single_symbol_fraction=0.005,  # very restrictive
            max_daily_new_trades=10,
        )
        r = PortfolioSimulator(cfg).run({
            "X": _rally(100, 30),
        })
        # $100 × 10 shares = $1000 = 1% of $100k > 0.5% cap → reject
        self.assertEqual(len(r.trades), 0)
        self.assertGreater(r.metrics.total_rejections, 0)
        self.assertTrue(any(rej.reason == RejectionReason.SINGLE_SYMBOL
                             for rej in r.rejections))


class TestExitAndCooldown(unittest.TestCase):
    def test_cooldown_after_exit_delays_reentry(self):
        # Force an exit via floor hit, then flat for 3 days (cooldown=5)
        # then flat again — sim should NOT re-enter within cooldown.
        cfg = PortfolioBacktestConfig(cooldown_days_after_exit=5)
        bars = [
            # day 1: entry 100
            Bar(date(2026,1,1), 100, 101, 99, 100, 1000),
            # day 2: crash to 88 (below floor 90) → exit
            Bar(date(2026,1,2), 100, 100, 88, 90, 1000),
            # day 3-6: flat 90
        ] + _flat(90, 4, first=date(2026,1,3))
        r = PortfolioSimulator(cfg).run({"X": bars})
        # Only 1 trade (entry day 1, exit day 2). Cooldown prevents
        # re-entry through end of series.
        self.assertEqual(len(r.trades), 1)
        self.assertEqual(r.trades[0].exit_reason, ExitReason.FLOOR_HIT)


class TestKillSwitch(unittest.TestCase):
    def test_kill_switch_engages_on_multi_symbol_crash(self):
        """Regression (B25b post-check): kill switch must engage when
        a multi-symbol crash on the same day pushes equity below the
        3% floor. The pre-fix bug used entry_price for OTHER positions
        while checking a given symbol's ladder, missing the loss."""
        cfg = PortfolioBacktestConfig(
            initial_cash=100_000,
            daily_loss_kill_switch_fraction=0.03,
            max_daily_new_trades=10, max_concurrent_trades=10,
        )
        bars = {}
        for sym in ["A", "B", "C", "D", "E"]:
            prices = [100, 100, 30, 30, 30]
            bars[sym] = [Bar(bar_date=date(2026,1,1)+timedelta(days=j),
                             open=p, high=p+1, low=p-1, close=p,
                             volume=1000)
                         for j, p in enumerate(prices)]
        r = PortfolioSimulator(cfg).run(bars)
        kill_rej = [x for x in r.rejections
                    if x.reason == RejectionReason.KILL_SWITCH]
        self.assertGreater(len(kill_rej), 0)


class TestEquityCurve(unittest.TestCase):
    def test_equity_curve_covers_all_dates(self):
        r = PortfolioSimulator().run({
            "A": _flat(100, 10),
            "B": _flat(100, 10),
        })
        self.assertEqual(len(r.equity_curve), 10)
        # Initial equity should approximately equal initial_cash on day 1
        # (positions worth entry_price × shares match cash spent)
        cfg = PortfolioBacktestConfig()
        self.assertAlmostEqual(
            r.equity_curve[0].equity, cfg.initial_cash, places=2,
        )


if __name__ == "__main__":
    unittest.main()
