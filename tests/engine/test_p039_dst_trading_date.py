"""P-039: the US Eastern trading date must be DST-correct.

Two helpers used a hardcoded -4.0h offset, which is Eastern only during
EDT. From 1 November 2026 Eastern is UTC-5.

These tests pin the behavior in BOTH halves of the year, and pin the
hours that actually matter so a future change cannot quietly move them.
`zoneinfo` is the reference: the point of the fix is to stop computing
what the system database already knows.
"""

import unittest
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from engine.snapshot_watchlist import _current_effective_date_et
from risk.portfolio_snapshot import _us_market_day_open_utc


_ET = ZoneInfo("America/New_York")

# 2026 transitions: EDT ends 1 Nov 2026, EST ends 14 Mar 2027.
_SUMMER_DAY = datetime(2026, 10, 6, tzinfo=timezone.utc)   # EDT, UTC-4
_WINTER_DAY = datetime(2026, 11, 10, tzinfo=timezone.utc)  # EST, UTC-5


def _old_effective_date(now_utc):
    """The pre-fix implementation, kept here so the tests below state a
    difference rather than merely asserting the new behavior."""
    return (now_utc + timedelta(hours=-4.0)).date()


class TestEffectiveDateMatchesZoneinfo(unittest.TestCase):
    def _sweep(self, day):
        return [day + timedelta(hours=h) for h in range(24)]

    def test_every_hour_of_a_summer_day_matches(self):
        for t in self._sweep(_SUMMER_DAY):
            self.assertEqual(_current_effective_date_et(t),
                             t.astimezone(_ET).date(), msg=str(t))

    def test_every_hour_of_a_winter_day_matches(self):
        for t in self._sweep(_WINTER_DAY):
            self.assertEqual(_current_effective_date_et(t),
                             t.astimezone(_ET).date(), msg=str(t))

    def test_the_old_code_really_was_wrong_in_winter(self):
        # Control. Without this, the winter test above could be passing
        # for reasons unrelated to the fix.
        wrong = [t for t in self._sweep(_WINTER_DAY)
                 if _old_effective_date(t) != t.astimezone(_ET).date()]
        self.assertEqual([t.hour for t in wrong], [4])

    def test_the_old_code_was_right_in_summer(self):
        wrong = [t for t in self._sweep(_SUMMER_DAY)
                 if _old_effective_date(t) != t.astimezone(_ET).date()]
        self.assertEqual(wrong, [])


class TestTheHoursThatMatter(unittest.TestCase):
    """Pins the three moments the trading day is built on, in winter --
    the season the old code got wrong."""

    def _check(self, hh, mm, expected_day):
        t = datetime(2026, 11, 10, hh, mm, tzinfo=timezone.utc)
        self.assertEqual(_current_effective_date_et(t),
                         expected_day.date())
        self.assertEqual(t.astimezone(_ET).date(), expected_day.date())

    def test_universe_run_0600_est(self):
        self._check(11, 0, datetime(2026, 11, 10))

    def test_market_open_0930_est(self):
        self._check(14, 30, datetime(2026, 11, 10))

    def test_market_close_1600_est(self):
        self._check(21, 0, datetime(2026, 11, 10))

    def test_the_one_hour_that_used_to_be_wrong_is_now_right(self):
        t = datetime(2026, 11, 10, 4, 0, tzinfo=timezone.utc)
        self.assertEqual(t.astimezone(_ET).date().isoformat(), "2026-11-09")
        self.assertEqual(_old_effective_date(t).isoformat(), "2026-11-10")
        self.assertEqual(_current_effective_date_et(t).isoformat(),
                         "2026-11-09")


class TestDailyCapAnchor(unittest.TestCase):
    """`_us_market_day_open_utc` is the cutoff for "new trades opened
    today", which feeds the D-0047 daily-cap check."""

    def _true_anchor(self, now_utc):
        et = now_utc.astimezone(_ET)
        return datetime.combine(et.date(), time(0, 0),
                                tzinfo=_ET).astimezone(timezone.utc)

    def test_anchor_matches_true_eastern_midnight_in_summer(self):
        for h in range(24):
            t = _SUMMER_DAY + timedelta(hours=h)
            self.assertEqual(_us_market_day_open_utc(t),
                             self._true_anchor(t), msg=str(t))

    def test_anchor_matches_true_eastern_midnight_in_winter(self):
        for h in range(24):
            t = _WINTER_DAY + timedelta(hours=h)
            self.assertEqual(_us_market_day_open_utc(t),
                             self._true_anchor(t), msg=str(t))

    def test_the_anchor_is_never_in_the_future(self):
        # An anchor later than "now" would exclude trades already made
        # today and let the daily cap admit an extra trade. This is the
        # under-count the old docstring claimed was impossible.
        for day in (_SUMMER_DAY, _WINTER_DAY):
            for h in range(24):
                t = day + timedelta(hours=h)
                self.assertLessEqual(_us_market_day_open_utc(t), t,
                                     msg=str(t))

    def test_the_old_code_undercounted_in_winter(self):
        # Control, proving the winter test above guards something real.
        #
        # At 04:30 UTC on 2026-11-10 it is 23:30 ET on 2026-11-09, so
        # the day being counted is the 9th and its true anchor is
        # 2026-11-09 05:00 UTC. The old code read the date as the 10th
        # and anchored at 2026-11-10 04:00 UTC -- 23 hours LATER, which
        # excludes essentially every trade actually opened that Eastern
        # day. The daily new-trade count then reads low, and the D-0047
        # cap admits trades it should refuse.
        t = datetime(2026, 11, 10, 4, 30, tzinfo=timezone.utc)
        off = timedelta(hours=-4.0)
        old_anchor = (datetime.combine((t + off).date(), time(0, 0),
                                       tzinfo=timezone.utc) - off)
        true_anchor = self._true_anchor(t)

        self.assertEqual(true_anchor,
                         datetime(2026, 11, 9, 5, 0, tzinfo=timezone.utc))
        self.assertEqual(old_anchor,
                         datetime(2026, 11, 10, 4, 0, tzinfo=timezone.utc))
        self.assertGreater(old_anchor, true_anchor)
        self.assertEqual((old_anchor - true_anchor), timedelta(hours=23))
        # The fixed code agrees with the true anchor.
        self.assertEqual(_us_market_day_open_utc(t), true_anchor)


class TestNaiveInputIsReadAsUtc(unittest.TestCase):
    def test_effective_date_accepts_a_naive_datetime(self):
        aware = datetime(2026, 11, 10, 14, 30, tzinfo=timezone.utc)
        self.assertEqual(_current_effective_date_et(aware),
                         _current_effective_date_et(
                             aware.replace(tzinfo=None)))

    def test_day_open_accepts_a_naive_datetime(self):
        aware = datetime(2026, 11, 10, 14, 30, tzinfo=timezone.utc)
        self.assertEqual(_us_market_day_open_utc(aware),
                         _us_market_day_open_utc(
                             aware.replace(tzinfo=None)))


if __name__ == "__main__":
    unittest.main()
