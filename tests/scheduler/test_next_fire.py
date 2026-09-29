"""Tests for scheduler.next_fire (B29)."""

import unittest
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from scheduler.next_fire import (
    ScheduleSpec, WEEKDAYS_MON_TO_FRI, is_fire_time, next_fire_at,
)


NY = ZoneInfo("America/New_York")
UTC = timezone.utc


def _d0021_spec() -> ScheduleSpec:
    return ScheduleSpec(
        tz=NY,
        times_of_day=tuple(time(h, 30) for h in range(9, 16)),
        weekdays=WEEKDAYS_MON_TO_FRI,
    )


class TestSpecValidation(unittest.TestCase):
    def test_empty_times_rejected(self):
        with self.assertRaises(ValueError):
            ScheduleSpec(tz=NY, times_of_day=(), weekdays=frozenset({0}))

    def test_unsorted_times_rejected(self):
        with self.assertRaises(ValueError):
            ScheduleSpec(
                tz=NY,
                times_of_day=(time(10, 0), time(9, 0)),
                weekdays=frozenset({0}),
            )

    def test_duplicate_times_rejected(self):
        with self.assertRaises(ValueError):
            ScheduleSpec(
                tz=NY,
                times_of_day=(time(10, 0), time(10, 0)),
                weekdays=frozenset({0}),
            )

    def test_out_of_range_weekday_rejected(self):
        with self.assertRaises(ValueError):
            ScheduleSpec(
                tz=NY,
                times_of_day=(time(10, 0),),
                weekdays=frozenset({7}),
            )


class TestNextFireBasics(unittest.TestCase):
    def test_next_slot_today(self):
        spec = _d0021_spec()
        # Monday 2026-02-02 09:00 ET (before 09:30 slot)
        now = datetime(2026, 2, 2, 9, 0, tzinfo=NY)
        nxt = next_fire_at(now, spec)
        self.assertEqual(nxt, datetime(2026, 2, 2, 9, 30, tzinfo=NY))

    def test_next_slot_next_hour(self):
        spec = _d0021_spec()
        # Monday 09:31 ET -- next is 10:30
        now = datetime(2026, 2, 2, 9, 31, tzinfo=NY)
        nxt = next_fire_at(now, spec)
        self.assertEqual(nxt, datetime(2026, 2, 2, 10, 30, tzinfo=NY))

    def test_after_last_slot_rolls_to_next_weekday(self):
        spec = _d0021_spec()
        # Friday 2026-02-06 15:31 ET -- next is Monday 09:30
        now = datetime(2026, 2, 6, 15, 31, tzinfo=NY)
        nxt = next_fire_at(now, spec)
        self.assertEqual(nxt, datetime(2026, 2, 9, 9, 30, tzinfo=NY))

    def test_saturday_skips_to_monday(self):
        spec = _d0021_spec()
        now = datetime(2026, 2, 7, 12, 0, tzinfo=NY)   # Saturday
        nxt = next_fire_at(now, spec)
        self.assertEqual(nxt, datetime(2026, 2, 9, 9, 30, tzinfo=NY))

    def test_strictly_after_now(self):
        spec = _d0021_spec()
        # Exactly 09:30 ET -- next is 10:30, not 09:30
        now = datetime(2026, 2, 2, 9, 30, tzinfo=NY)
        nxt = next_fire_at(now, spec)
        self.assertEqual(nxt, datetime(2026, 2, 2, 10, 30, tzinfo=NY))

    def test_utc_input_converted(self):
        spec = _d0021_spec()
        # 2026-02-02 14:00 UTC = 09:00 ET
        now = datetime(2026, 2, 2, 14, 0, tzinfo=UTC)
        nxt = next_fire_at(now, spec)
        self.assertEqual(nxt, datetime(2026, 2, 2, 9, 30, tzinfo=NY))

    def test_naive_now_rejected(self):
        with self.assertRaises(ValueError):
            next_fire_at(datetime(2026, 2, 2, 9, 0), _d0021_spec())


class TestDSTEdges(unittest.TestCase):
    def test_spring_forward_sunday_is_skipped_as_weekend(self):
        # 2026 US spring-forward: Sunday 2026-03-08. Weekday=6, so
        # skipped by the Mon-Fri weekdays rule regardless of the
        # missing 02:00-03:00 hour. Fire lands on Monday 09:30 ET.
        spec = _d0021_spec()
        now = datetime(2026, 3, 6, 15, 31, tzinfo=NY)  # Fri after last slot
        nxt = next_fire_at(now, spec)
        self.assertEqual(nxt, datetime(2026, 3, 9, 9, 30, tzinfo=NY))

    def test_utc_offset_correct_after_dst(self):
        # Sanity: a fire on Monday 2026-03-09 09:30 ET is in EDT
        # (UTC-4), not EST (UTC-5). Its UTC value is 13:30, not 14:30.
        spec = _d0021_spec()
        now = datetime(2026, 3, 9, 9, 0, tzinfo=NY)
        nxt = next_fire_at(now, spec)
        nxt_utc = nxt.astimezone(UTC)
        self.assertEqual(nxt_utc, datetime(2026, 3, 9, 13, 30, tzinfo=UTC))

    def test_utc_offset_correct_before_dst(self):
        # Same test in EST: 2026-02-02 09:30 ET is UTC-5 -> 14:30 UTC.
        spec = _d0021_spec()
        now = datetime(2026, 2, 2, 9, 0, tzinfo=NY)
        nxt = next_fire_at(now, spec)
        nxt_utc = nxt.astimezone(UTC)
        self.assertEqual(nxt_utc, datetime(2026, 2, 2, 14, 30, tzinfo=UTC))


class TestIsFireTime(unittest.TestCase):
    def test_exact_match(self):
        spec = _d0021_spec()
        now = datetime(2026, 2, 2, 9, 30, tzinfo=NY)
        self.assertTrue(is_fire_time(now, spec))

    def test_within_tolerance(self):
        spec = _d0021_spec()
        now = datetime(2026, 2, 2, 9, 30, 30, tzinfo=NY)
        self.assertTrue(is_fire_time(now, spec, tolerance_seconds=60))

    def test_outside_tolerance(self):
        spec = _d0021_spec()
        now = datetime(2026, 2, 2, 9, 32, tzinfo=NY)
        self.assertFalse(is_fire_time(now, spec, tolerance_seconds=60))

    def test_weekend_never_matches(self):
        spec = _d0021_spec()
        now = datetime(2026, 2, 7, 9, 30, tzinfo=NY)  # Saturday
        self.assertFalse(is_fire_time(now, spec))


if __name__ == "__main__":
    unittest.main()
