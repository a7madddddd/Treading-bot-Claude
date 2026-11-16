import unittest
from datetime import date, datetime, timedelta, timezone

from spx0dte.sizing import (
    CONTRACTS_PER_TRADE, MAX_CONCURRENT_POSITIONS,
    ConcurrentPositionTracker, DailyTradeCounter, contracts_for_trade,
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


if __name__ == "__main__":
    unittest.main()
