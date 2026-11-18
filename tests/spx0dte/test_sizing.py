import unittest
from datetime import date, datetime, timedelta, timezone

from spx0dte.sizing import (
    CONTRACT_MULTIPLIER, CONTRACTS_PER_TRADE, DAILY_LOSS_LIMIT_PCT, MAX_CONCURRENT_POSITIONS,
    ConcurrentPositionTracker, DailyLossLimitTracker, DailyTradeCounter,
    contracts_for_trade, percentile, realized_pnl_dollars,
)


class TestContractsForTrade(unittest.TestCase):
    def test_returns_approved_fixed_size(self):
        self.assertEqual(contracts_for_trade(), 1)
        self.assertEqual(contracts_for_trade(), CONTRACTS_PER_TRADE)


class TestConcurrentPositionTracker(unittest.TestCase):
    def setUp(self):
        self.t0 = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)

    def test_default_cap_matches_approved_value(self):
        tracker = ConcurrentPositionTracker()
        self.assertEqual(tracker.max_concurrent, MAX_CONCURRENT_POSITIONS)

    def test_rejects_invalid_cap(self):
        with self.assertRaises(ValueError):
            ConcurrentPositionTracker(max_concurrent=0)

    def test_accepts_up_to_cap(self):
        tracker = ConcurrentPositionTracker(max_concurrent=2)
        self.assertTrue(tracker.can_open(self.t0))
        tracker.record_open(self.t0, self.t0 + timedelta(minutes=40))
        self.assertTrue(tracker.can_open(self.t0))
        tracker.record_open(self.t0, self.t0 + timedelta(minutes=40))
        self.assertFalse(tracker.can_open(self.t0))

    def test_slot_frees_up_after_exit(self):
        tracker = ConcurrentPositionTracker(max_concurrent=1)
        exit_time = self.t0 + timedelta(minutes=10)
        tracker.record_open(self.t0, exit_time)
        self.assertFalse(tracker.can_open(self.t0 + timedelta(minutes=5)))
        self.assertTrue(tracker.can_open(exit_time + timedelta(seconds=1)))


class TestDailyTradeCounter(unittest.TestCase):
    def test_unbounded_by_default(self):
        counter = DailyTradeCounter()
        today = date(2026, 1, 2)
        for _ in range(50):
            self.assertTrue(counter.can_trade(today))
            counter.record_trade(today)

    def test_respects_explicit_limit(self):
        counter = DailyTradeCounter(max_per_day=2)
        today = date(2026, 1, 2)
        self.assertTrue(counter.can_trade(today))
        counter.record_trade(today)
        self.assertTrue(counter.can_trade(today))
        counter.record_trade(today)
        self.assertFalse(counter.can_trade(today))


class TestPercentile(unittest.TestCase):
    def test_matches_known_values(self):
        values = list(range(1, 11))  # 1..10, already sorted
        self.assertEqual(percentile(values, 0.0), 1)
        self.assertEqual(percentile(values, 1.0), 10)
        self.assertAlmostEqual(percentile(values, 0.5), 5.5)

    def test_single_value(self):
        self.assertEqual(percentile([7.0], 0.9), 7.0)

    def test_rejects_empty_sequence(self):
        with self.assertRaises(ValueError):
            percentile([], 0.5)


class TestRealizedPnlDollars(unittest.TestCase):
    def test_applies_contract_multiplier_and_quantity(self):
        # +1.00/share, 1 contract -> +$100 (not +1.00 -- the bug this corrects)
        self.assertEqual(realized_pnl_dollars(entry_price=2.00, exit_price=3.00, quantity=1), 100.0)
        self.assertEqual(realized_pnl_dollars(entry_price=2.00, exit_price=3.00, quantity=2), 200.0)
        self.assertEqual(CONTRACT_MULTIPLIER, 100)

    def test_negative_pnl_on_a_loss(self):
        self.assertEqual(realized_pnl_dollars(entry_price=3.00, exit_price=2.00, quantity=1), -100.0)


class TestDailyLossLimitTracker(unittest.TestCase):
    def test_default_pct_matches_approved_value(self):
        tracker = DailyLossLimitTracker()
        self.assertEqual(tracker.limit_pct, DAILY_LOSS_LIMIT_PCT)
        self.assertEqual(DAILY_LOSS_LIMIT_PCT, 0.02)

    def test_rejects_invalid_pct(self):
        with self.assertRaises(ValueError):
            DailyLossLimitTracker(limit_pct=0.0)
        with self.assertRaises(ValueError):
            DailyLossLimitTracker(limit_pct=1.0)

    def test_not_reached_before_a_day_is_locked(self):
        tracker = DailyLossLimitTracker()
        self.assertFalse(tracker.limit_reached())
        self.assertIsNone(tracker.locked_equity)

    def test_limit_reached_once_losses_hit_the_locked_percentage(self):
        tracker = DailyLossLimitTracker(limit_pct=0.02)
        tracker.start_new_day("2026-01-02", equity=100_000.0)
        self.assertFalse(tracker.limit_reached())

        tracker.record_realized_pnl(-1_500.0)
        self.assertFalse(tracker.limit_reached())  # -1.5% < 2%

        tracker.record_realized_pnl(-600.0)  # cumulative -$2,100 = -2.1%
        self.assertTrue(tracker.limit_reached())

    def test_wins_offset_losses_before_the_limit(self):
        tracker = DailyLossLimitTracker(limit_pct=0.02)
        tracker.start_new_day("2026-01-02", equity=100_000.0)
        tracker.record_realized_pnl(-2_500.0)
        self.assertTrue(tracker.limit_reached())
        tracker.record_realized_pnl(+1_000.0)  # net -$1,500 = -1.5%
        self.assertFalse(tracker.limit_reached())

    def test_new_day_resets_realized_pnl_and_relocks_equity(self):
        tracker = DailyLossLimitTracker(limit_pct=0.02)
        tracker.start_new_day("2026-01-02", equity=100_000.0)
        tracker.record_realized_pnl(-2_500.0)
        self.assertTrue(tracker.limit_reached())

        tracker.start_new_day("2026-01-03", equity=97_593.76)
        self.assertFalse(tracker.limit_reached())
        self.assertEqual(tracker.realized_pnl_today, 0.0)
        self.assertEqual(tracker.locked_equity, 97_593.76)

    def test_same_day_call_is_idempotent(self):
        tracker = DailyLossLimitTracker()
        tracker.start_new_day("2026-01-02", equity=100_000.0)
        tracker.record_realized_pnl(-500.0)
        tracker.start_new_day("2026-01-02", equity=999_999.0)  # must be ignored -- same day
        self.assertEqual(tracker.locked_equity, 100_000.0)
        self.assertEqual(tracker.realized_pnl_today, -500.0)

    def test_rejects_nonpositive_equity(self):
        tracker = DailyLossLimitTracker()
        with self.assertRaises(ValueError):
            tracker.start_new_day("2026-01-02", equity=0.0)


if __name__ == "__main__":
    unittest.main()
