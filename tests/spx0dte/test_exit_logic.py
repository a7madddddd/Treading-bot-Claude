import unittest
from datetime import datetime, timedelta, timezone

from spx0dte.exit_logic import (
    DailyPaceTracker, session_pace_width_pct, simulate_exit,
)


def _bars(entry: datetime, prices: list, step_minutes: int = 1) -> list:
    return [
        {"t": (entry + timedelta(minutes=i * step_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ"), "c": p}
        for i, p in enumerate(prices)
    ]


class TestSimulateExit(unittest.TestCase):
    def setUp(self):
        self.entry = datetime(2026, 1, 2, 14, 31, tzinfo=timezone.utc)

    def test_no_bars_returns_none(self):
        self.assertIsNone(simulate_exit([], self.entry, 40))

    def test_target_hit(self):
        bars = _bars(self.entry, [1.0, 1.5, 1.81])  # +81% >= target 80%
        result = simulate_exit(bars, self.entry, 40)
        self.assertEqual(result.reason, "target")
        self.assertAlmostEqual(result.exit_price, 1.81)

    def test_stop_loss_never_armed(self):
        bars = _bars(self.entry, [1.0, 0.7, 0.59])  # -41% < stop -40%, never reached +25%
        result = simulate_exit(bars, self.entry, 40)
        self.assertEqual(result.reason, "stop_loss")

    def test_breakeven_stop_after_arming(self):
        bars = _bars(self.entry, [1.0, 1.3, 1.0])  # arms at 1.3 (+30%), falls to breakeven
        result = simulate_exit(bars, self.entry, 40)
        self.assertEqual(result.reason, "breakeven_stop")
        self.assertTrue(result.armed)

    def test_stall_after_arming(self):
        # arms at bar 1 (1.3), then 3 data bars with no new high -> stall
        bars = _bars(self.entry, [1.0, 1.3, 1.29, 1.28, 1.27])
        result = simulate_exit(bars, self.entry, 40)
        self.assertEqual(result.reason, "stall")
        self.assertTrue(result.armed)

    def test_time_cap_fallthrough(self):
        # Flat +15% the whole way: stays under the +25% arm threshold
        # (never arms) and, even at time_ratio=1 (the very last minute
        # of the cap), progress_ratio (0.15/0.25=0.6) still clears
        # lag_threshold * time_ratio (0.5*1=0.5) -- never lags either.
        bars = _bars(self.entry, [1.0] + [1.15] * 39)
        result = simulate_exit(bars, self.entry, 40)
        self.assertEqual(result.reason, "time_cap")

    def test_lagging_pace_cuts_a_flat_trade_early(self):
        # Flat at P0 well past the noise guard (>=2 bars, >=3 min) ->
        # progress_ratio = 0 < lag_threshold * time_ratio as soon as
        # time_ratio > 0 -- exits well before the 40-minute cap.
        bars = _bars(self.entry, [1.0] * 10)
        result = simulate_exit(bars, self.entry, 40)
        self.assertEqual(result.reason, "lagging_pace")
        # Should exit at or after minute 3 (the noise guard), well before minute 40.
        elapsed = (result.exit_time - self.entry).total_seconds() / 60
        self.assertGreaterEqual(elapsed, 3)
        self.assertLess(elapsed, 40)

    def test_lagging_pace_guard_blocks_very_early_bars(self):
        # Only 2 bars, both within the 3-minute noise guard -> no lagging_pace
        # exit possible yet; falls through to time_cap at the last bar.
        bars = _bars(self.entry, [1.0, 1.0])
        result = simulate_exit(bars, self.entry, 40)
        self.assertEqual(result.reason, "time_cap")


class TestSessionPaceWidthPct(unittest.TestCase):
    def test_empty_bars(self):
        self.assertIsNone(session_pace_width_pct([]))

    def test_basic_range(self):
        bars = [{"c": 100.0}, {"c": 102.0}, {"c": 98.0}]
        pct = session_pace_width_pct(bars, minutes=3)
        self.assertAlmostEqual(pct, (102.0 - 98.0) / 100.0)


class TestDailyPaceTracker(unittest.TestCase):
    def test_first_day_uses_base_cap(self):
        tracker = DailyPaceTracker()
        cap = tracker.record_and_get_cap(0.01)
        self.assertEqual(cap, 40.0)

    def test_fast_day_shortens_cap_toward_floor(self):
        tracker = DailyPaceTracker()
        for _ in range(20):
            tracker.record_and_get_cap(0.01)  # establish a normal trailing pace
        cap = tracker.record_and_get_cap(0.10)  # 10x wider than trailing average
        self.assertEqual(cap, 25.0)  # clamped at the floor

    def test_slow_day_lengthens_cap_toward_ceiling(self):
        tracker = DailyPaceTracker()
        for _ in range(20):
            tracker.record_and_get_cap(0.01)
        cap = tracker.record_and_get_cap(0.0001)  # far narrower than trailing average
        self.assertEqual(cap, 55.0)  # clamped at the ceiling

    def test_no_look_ahead_lookback_window(self):
        tracker = DailyPaceTracker(lookback_days=3)
        tracker.record_and_get_cap(0.01)
        tracker.record_and_get_cap(0.01)
        tracker.record_and_get_cap(0.01)
        tracker.record_and_get_cap(0.01)  # pushes the first value out
        self.assertEqual(len(tracker._history), 3)


if __name__ == "__main__":
    unittest.main()
