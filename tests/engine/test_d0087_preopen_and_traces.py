"""D-0087 — the first check of the day stops being thrown away, and a
cycle that scores nothing stops being invisible.

Observed live on 2026-10-07. The 09:30 check has a +/-90s tolerance, so
it fired at 09:28:31 ET -- ninety seconds before the bell. The broker
correctly answered "closed". Two things then went wrong:

  1. That answer consumed the day's single `nothing_to_trade` message,
     so every later and genuinely informative reason for the same date
     was silenced. The only message the Controller received all day was
     "the market is CLOSED", which is not news at 09:28:31.
  2. The cycle returned before scoring, so `cycle_metrics` held ZERO
     rows -- indistinguishable from an engine that had died. It was in
     fact read that way, until the notification log settled it.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from engine.engine import Engine
from engine.schedule import D0021_TIMEZONE_ET, DEFAULT_TOLERANCE_SECONDS

from .test_engine import (
    StaticWatchlistSource, _make_engine, _repos,
)


def _et(y, m, d, hh, mm, ss=0):
    return datetime(y, m, d, hh, mm, ss, tzinfo=D0021_TIMEZONE_ET)


class TestThePreOpenWindowIsNotNews(unittest.TestCase):
    """2026-10-07 was a Wednesday; 2026-10-10 is the Saturday after."""

    def test_the_exact_moment_from_the_incident(self):
        self.assertTrue(Engine._is_pre_open_tolerance(
            now=_et(2026, 10, 7, 9, 28, 31)))

    def test_the_whole_tolerance_window_counts_as_pre_open(self):
        open_dt = _et(2026, 10, 7, 9, 30)
        earliest = open_dt - timedelta(seconds=DEFAULT_TOLERANCE_SECONDS)
        for offset in (0, 1, 30, 89):
            with self.subTest(offset=offset):
                self.assertTrue(Engine._is_pre_open_tolerance(
                    now=earliest + timedelta(seconds=offset)))

    def test_the_bell_itself_is_NOT_pre_open(self):
        self.assertFalse(Engine._is_pre_open_tolerance(
            now=_et(2026, 10, 7, 9, 30, 0)))

    def test_a_moment_before_the_window_IS_still_news(self):
        """Earlier than the tolerance means the schedule did not put us
        here, so a closed market is a real observation."""
        self.assertFalse(Engine._is_pre_open_tolerance(
            now=_et(2026, 10, 7, 9, 28, 29)))

    def test_a_HOLIDAY_closure_at_midday_is_still_reported(self):
        """The failure this guard must not introduce: going quiet on a
        real, unscheduled closure."""
        for hh, mm in ((11, 0), (13, 30), (15, 59)):
            with self.subTest(time=f"{hh}:{mm}"):
                self.assertFalse(Engine._is_pre_open_tolerance(
                    now=_et(2026, 10, 7, hh, mm)))

    def test_a_weekend_is_never_pre_open(self):
        self.assertFalse(Engine._is_pre_open_tolerance(
            now=_et(2026, 10, 10, 9, 29)))      # Saturday
        self.assertFalse(Engine._is_pre_open_tolerance(
            now=_et(2026, 10, 11, 9, 29)))      # Sunday


class _ClosedBroker:
    def is_market_open(self):
        return False


class TestTheDaysMessageIsNotBurned(unittest.TestCase):
    def _engine(self):
        repos = _repos()
        engine, _b, _md, _d, notifier, _es = _make_engine(
            *repos, watchlist=StaticWatchlistSource(("A",)))
        engine._execution_service._broker = _ClosedBroker()
        engine._lock.acquire(now=_et(2026, 10, 7, 9, 28, 31))
        notifier.events.clear()
        return engine, notifier

    def _nothing_events(self, notifier):
        return [e for e in notifier.events
                if e.event == "nothing_to_trade_today"]

    def test_the_pre_open_check_sends_NOTHING(self):
        engine, notifier = self._engine()
        engine.run_trigger_check(now=_et(2026, 10, 7, 9, 28, 31))
        self.assertEqual(self._nothing_events(notifier), [])

    def test_so_a_LATER_real_reason_can_still_be_reported(self):
        """The whole point. Before this, the 09:28 non-event consumed
        the per-date dedup slot and silenced the rest of the day."""
        engine, notifier = self._engine()
        engine.run_trigger_check(now=_et(2026, 10, 7, 9, 28, 31))
        engine._notify_nothing_to_trade("a real reason later that day",
                                        now=_et(2026, 10, 7, 11, 30))
        events = self._nothing_events(notifier)
        self.assertEqual(len(events), 1)
        self.assertIn("a real reason later", events[0].message)

    def test_a_midday_closure_is_STILL_announced(self):
        engine, notifier = self._engine()
        engine.run_trigger_check(now=_et(2026, 10, 7, 11, 30))
        events = self._nothing_events(notifier)
        self.assertEqual(len(events), 1)
        self.assertIn("CLOSED", events[0].message)


class TestAnUnscoredCycleStillLeavesATrace(unittest.TestCase):
    def test_the_pre_open_cycle_is_recorded_as_unscored(self):
        """Zero rows for a day reads as a dead engine. That is exactly
        how 2026-10-07 was read."""
        repos = _repos()
        engine, _b, _md, _d, _n, _es = _make_engine(
            *repos, watchlist=StaticWatchlistSource(("A",)))
        engine._execution_service._broker = _ClosedBroker()
        rows: list = []
        engine._cycle_metrics_recorder = rows.append
        engine._lock.acquire(now=_et(2026, 10, 7, 9, 28, 31))
        engine.run_trigger_check(now=_et(2026, 10, 7, 9, 28, 31))

        self.assertEqual(len(rows), 1)
        m = rows[0]
        self.assertFalse(m.scored)
        self.assertEqual(m.candidates_evaluated, 0)
        self.assertIsNone(m.above_min_score,
                          msg="0 would read as 'nothing was good enough'")
        self.assertEqual(m.proposals_created, 0)


if __name__ == "__main__":
    unittest.main()
