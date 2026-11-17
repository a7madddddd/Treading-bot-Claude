import unittest
from datetime import datetime, timedelta, timezone

from spx0dte.live_exit import LiveExitTracker


def _t(entry: datetime, minutes: float) -> datetime:
    return entry + timedelta(minutes=minutes)


class TestLiveExitTracker(unittest.TestCase):
    def setUp(self):
        self.entry_time = datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc)
        self.entry_price = 1.0

    def _tracker(self, hold_cap_minutes=40.0) -> LiveExitTracker:
        return LiveExitTracker(
            entry_price=self.entry_price, entry_time=self.entry_time,
            hold_cap_minutes=hold_cap_minutes,
        )

    def test_rejects_nonpositive_entry_price(self):
        with self.assertRaises(ValueError):
            LiveExitTracker(entry_price=0, entry_time=self.entry_time, hold_cap_minutes=40.0)

    def test_rejects_nonpositive_hold_cap(self):
        with self.assertRaises(ValueError):
            LiveExitTracker(entry_price=1.0, entry_time=self.entry_time, hold_cap_minutes=0)

    def test_target_hit(self):
        tracker = self._tracker()
        reason = tracker.update(1.81, _t(self.entry_time, 5))  # +81% >= target 80%
        self.assertEqual(reason, "target")

    def test_stop_loss_never_armed(self):
        tracker = self._tracker()
        tracker.update(0.70, _t(self.entry_time, 2))
        reason = tracker.update(0.59, _t(self.entry_time, 4))  # -41% < stop -40%
        self.assertEqual(reason, "stop_loss")

    def test_breakeven_stop_after_arming(self):
        tracker = self._tracker()
        tracker.update(1.30, _t(self.entry_time, 2))  # arms at +30%
        reason = tracker.update(1.0, _t(self.entry_time, 4))  # falls to breakeven
        self.assertEqual(reason, "breakeven_stop")
        self.assertTrue(tracker.armed)

    def test_stall_after_arming(self):
        tracker = self._tracker()
        tracker.update(1.30, _t(self.entry_time, 2))  # arms
        tracker.update(1.29, _t(self.entry_time, 3))  # no new high, 1
        tracker.update(1.28, _t(self.entry_time, 4))  # 2
        reason = tracker.update(1.27, _t(self.entry_time, 5))  # 3 -> stall
        self.assertEqual(reason, "stall")

    def test_time_cap_fallthrough(self):
        tracker = self._tracker(hold_cap_minutes=40.0)
        # Flat +15%: never arms, and at time_ratio=1 progress_ratio
        # (0.15/0.25=0.6) still clears lag_threshold*time_ratio (0.5) --
        # never lags, falls through to time_cap.
        for minute in range(1, 41):
            reason = tracker.update(1.15, _t(self.entry_time, minute))
        self.assertEqual(reason, "time_cap")

    def test_lagging_pace_cuts_a_flat_trade_early(self):
        tracker = self._tracker(hold_cap_minutes=40.0)
        tracker.update(1.0, _t(self.entry_time, 1))
        tracker.update(1.0, _t(self.entry_time, 2))
        reason = tracker.update(1.0, _t(self.entry_time, 3))
        self.assertEqual(reason, "lagging_pace")

    def test_lagging_pace_guard_blocks_very_early_ticks(self):
        tracker = self._tracker(hold_cap_minutes=40.0)
        reason = tracker.update(1.0, _t(self.entry_time, 1))
        self.assertIsNone(reason)  # noise guard: < min_ticks and < min_elapsed_minutes

    def test_no_exit_returns_none(self):
        tracker = self._tracker(hold_cap_minutes=40.0)
        reason = tracker.update(1.10, _t(self.entry_time, 1))
        self.assertIsNone(reason)


if __name__ == "__main__":
    unittest.main()
