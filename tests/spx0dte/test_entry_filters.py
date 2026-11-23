import unittest
from datetime import datetime, timezone

from spx0dte.entry_filters import (
    time_quality_window, vix_in_tradeable_range, liquidity_ok, run_all_filters,
)


class TestVixInTradeableRange(unittest.TestCase):
    def test_none_fails_safe_rejects(self):
        # 2026-11-22 fix: unknown VIX is now treated as outside the
        # tradeable range (reject), not as "filter skipped" -- this filter
        # exists to keep the bot out of real VIX>35 crisis conditions,
        # which is also when a data source is most likely to be flaky.
        self.assertFalse(vix_in_tradeable_range(None).passed)

    def test_too_low_rejected(self):
        self.assertFalse(vix_in_tradeable_range(10.0).passed)

    def test_too_high_rejected(self):
        self.assertFalse(vix_in_tradeable_range(40.0).passed)

    def test_in_range_passes(self):
        self.assertTrue(vix_in_tradeable_range(20.0).passed)


class TestTimeQualityWindow(unittest.TestCase):
    def test_lunch_window_blocked_summer_dst(self):
        # 12:30 ET during DST (UTC-4) = 16:30 UTC
        entry = datetime(2026, 7, 1, 16, 30, tzinfo=timezone.utc)
        self.assertFalse(time_quality_window(entry).passed)

    def test_morning_open_not_blocked(self):
        # 09:45 ET during DST = 13:45 UTC
        entry = datetime(2026, 7, 1, 13, 45, tzinfo=timezone.utc)
        self.assertTrue(time_quality_window(entry).passed)


class TestLiquidityOk(unittest.TestCase):
    def test_invalid_quote_rejected(self):
        self.assertFalse(liquidity_ok(0, 1.0).passed)
        self.assertFalse(liquidity_ok(1.0, 0.9).passed)  # ask < bid

    def test_narrow_spread_passes(self):
        self.assertTrue(liquidity_ok(1.00, 1.02).passed)

    def test_wide_spread_rejected(self):
        self.assertFalse(liquidity_ok(1.00, 1.50).passed)


class TestRunAllFilters(unittest.TestCase):
    def test_runs_exactly_the_two_backtested_filters(self):
        # 2026-11-23 (Controller-approved): time_quality_window measured
        # against three independent real periods and found no real
        # discriminating power (docs/trading/decisions.md D-0100) -- removed
        # from run_all_filters, same disposition as volume_confirmed/
        # cross_market_confirmed.
        entry = datetime(2026, 7, 1, 13, 45, tzinfo=timezone.utc)
        results = run_all_filters(vix_level=20.0, entry_time=entry, hold_cap_minutes=40)
        # vix_in_tradeable_range, economic_calendar_clear
        self.assertEqual(len(results), 2)


if __name__ == "__main__":
    unittest.main()
