"""Tests for scheduler.daemon (B29)."""

import unittest
from datetime import datetime, time, timedelta, timezone
from typing import List
from zoneinfo import ZoneInfo

from scheduler.daemon import (
    FireEvent, ListFireSink, ScheduledJob, SchedulerDaemon,
)
from scheduler.next_fire import ScheduleSpec, WEEKDAYS_MON_TO_FRI


NY = ZoneInfo("America/New_York")
UTC = timezone.utc


class _FakeClock:
    """Advances a virtual clock. `sleep(gap)` jumps forward by `gap`
    seconds; the daemon sees each `now()` at the current virtual
    position."""

    def __init__(self, start: datetime) -> None:
        self._now = start
        self.sleeps: List[float] = []

    def now(self) -> datetime:
        return self._now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._now = self._now + timedelta(seconds=seconds)


def _minute_spec(hour: int, minute: int) -> ScheduleSpec:
    return ScheduleSpec(
        tz=NY,
        times_of_day=(time(hour, minute),),
        weekdays=WEEKDAYS_MON_TO_FRI,
    )


class TestDaemonBasics(unittest.TestCase):
    def test_rejects_empty_jobs(self):
        with self.assertRaises(ValueError):
            SchedulerDaemon(jobs=[], install_signal_handlers=False)

    def test_rejects_duplicate_names(self):
        j = ScheduledJob(
            name="x", spec=_minute_spec(9, 30),
            fn=lambda _: None,
        )
        with self.assertRaises(ValueError):
            SchedulerDaemon(jobs=[j, j], install_signal_handlers=False)


class TestFires(unittest.TestCase):
    def test_fires_once_at_scheduled_time(self):
        fired: List[datetime] = []
        job = ScheduledJob(
            name="j1", spec=_minute_spec(9, 30),
            fn=lambda when: fired.append(when),
        )
        # Start Monday 2026-02-02 09:29 ET
        start = datetime(2026, 2, 2, 9, 29, tzinfo=NY)
        clock = _FakeClock(start)
        sink = ListFireSink()
        d = SchedulerDaemon(
            jobs=[job], sink=sink, clock=clock.now,
            sleep_fn=clock.sleep, install_signal_handlers=False,
        )
        # Advance ~2 min so we cover the 09:30 slot.
        deadline = start + timedelta(minutes=2)
        d.run_until(deadline)
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0],
                         datetime(2026, 2, 2, 9, 30, tzinfo=NY))
        self.assertEqual(len(sink.events), 1)
        self.assertTrue(sink.events[0].ok)
        self.assertIsNone(sink.events[0].error)

    def test_multi_job_earliest_wins(self):
        fired: List[str] = []
        j_a = ScheduledJob(
            name="a", spec=_minute_spec(9, 30),
            fn=lambda _w: fired.append("a"),
        )
        j_b = ScheduledJob(
            name="b", spec=_minute_spec(10, 30),
            fn=lambda _w: fired.append("b"),
        )
        start = datetime(2026, 2, 2, 9, 0, tzinfo=NY)
        clock = _FakeClock(start)
        d = SchedulerDaemon(
            jobs=[j_a, j_b], clock=clock.now,
            sleep_fn=clock.sleep, install_signal_handlers=False,
        )
        d.run_until(start + timedelta(minutes=45))  # covers 09:30 only
        self.assertEqual(fired, ["a"])

    def test_job_exception_is_captured_daemon_continues(self):
        def _bad(_w):
            raise RuntimeError("kaboom")

        two_slot_spec = ScheduleSpec(
            tz=NY,
            times_of_day=(time(9, 30), time(10, 30)),
            weekdays=WEEKDAYS_MON_TO_FRI,
        )
        j = ScheduledJob(name="bad", spec=two_slot_spec, fn=_bad)
        start = datetime(2026, 2, 2, 9, 29, tzinfo=NY)
        clock = _FakeClock(start)
        sink = ListFireSink()
        d = SchedulerDaemon(
            jobs=[j], sink=sink, clock=clock.now,
            sleep_fn=clock.sleep, install_signal_handlers=False,
        )
        # Two fires: 09:30 and 10:30
        d.run_until(start + timedelta(hours=1, minutes=5))
        self.assertGreaterEqual(len(sink.events), 2)
        self.assertFalse(sink.events[0].ok)
        self.assertIn("kaboom", sink.events[0].error or "")
        self.assertFalse(sink.events[1].ok)

    def test_stop_interrupts_loop(self):
        fired: List[str] = []
        j = ScheduledJob(
            name="a", spec=_minute_spec(9, 30),
            fn=lambda _w: fired.append("a"),
        )
        start = datetime(2026, 2, 2, 9, 0, tzinfo=NY)
        clock = _FakeClock(start)
        d = SchedulerDaemon(
            jobs=[j], clock=clock.now, sleep_fn=clock.sleep,
            install_signal_handlers=False,
        )
        d.stop()
        d.run_until(start + timedelta(hours=1))
        self.assertEqual(fired, [])  # never fired


if __name__ == "__main__":
    unittest.main()
