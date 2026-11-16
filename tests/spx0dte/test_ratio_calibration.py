import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from spx0dte.ratio_calibration import (
    BOOTSTRAP_RATIO, RatioCalibrationError, RatioProvider, measure_ratio_now,
)


class FakeAlpacaForRatio:
    """Minimal stand-in exposing only get_spy_daily_bars, as
    measure_ratio_now needs."""

    def __init__(self, bars):
        self._bars = bars

    def get_spy_daily_bars(self, start_date, end_date):
        return self._bars


class TestMeasureRatioNow(unittest.TestCase):
    @patch("spx0dte.ratio_calibration.fetch_sp500_closes")
    def test_computes_median_of_matched_days(self, mock_fetch):
        mock_fetch.return_value = {
            "2026-11-10": 7000.0,
            "2026-11-11": 7100.0,
            "2026-11-12": 7050.0,
        }
        client = FakeAlpacaForRatio([
            {"t": "2026-11-10T00:00:00Z", "c": 700.0},   # ratio 10.0
            {"t": "2026-11-11T00:00:00Z", "c": 710.0},   # ratio 10.0
            {"t": "2026-11-12T00:00:00Z", "c": 700.0},   # ratio 10.07...
        ])
        ratio = measure_ratio_now(client, "fake-key")
        self.assertAlmostEqual(ratio, 10.0, places=2)

    @patch("spx0dte.ratio_calibration.fetch_sp500_closes")
    def test_raises_when_no_dates_match(self, mock_fetch):
        mock_fetch.return_value = {"2099-01-01": 1234.0}
        client = FakeAlpacaForRatio([{"t": "2026-11-10T00:00:00Z", "c": 700.0}])
        with self.assertRaises(RatioCalibrationError):
            measure_ratio_now(client, "fake-key")

    @patch("spx0dte.ratio_calibration.fetch_sp500_closes")
    def test_skips_unmatched_days(self, mock_fetch):
        mock_fetch.return_value = {"2026-11-10": 7000.0}
        client = FakeAlpacaForRatio([
            {"t": "2026-11-10T00:00:00Z", "c": 700.0},
            {"t": "2026-11-11T00:00:00Z", "c": 710.0},  # no matching SP500 date
        ])
        ratio = measure_ratio_now(client, "fake-key")
        self.assertAlmostEqual(ratio, 10.0, places=2)


class TestRatioProvider(unittest.TestCase):
    def setUp(self):
        self.client = FakeAlpacaForRatio([{"t": "2026-11-10T00:00:00Z", "c": 700.0}])
        self.now = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)

    @patch("spx0dte.ratio_calibration.measure_ratio_now")
    def test_first_call_measures(self, mock_measure):
        mock_measure.return_value = 10.03
        provider = RatioProvider(client=self.client, fred_api_key="fake-key")
        ratio = provider.get_ratio(now=self.now)
        self.assertEqual(ratio, 10.03)
        mock_measure.assert_called_once()

    @patch("spx0dte.ratio_calibration.measure_ratio_now")
    def test_cached_within_max_age_does_not_remeasure(self, mock_measure):
        mock_measure.return_value = 10.03
        provider = RatioProvider(client=self.client, fred_api_key="fake-key", max_age=timedelta(hours=24))
        provider.get_ratio(now=self.now)
        provider.get_ratio(now=self.now + timedelta(hours=1))
        mock_measure.assert_called_once()

    @patch("spx0dte.ratio_calibration.measure_ratio_now")
    def test_remeasures_after_stale(self, mock_measure):
        mock_measure.side_effect = [10.03, 10.10]
        provider = RatioProvider(client=self.client, fred_api_key="fake-key", max_age=timedelta(hours=1))
        first = provider.get_ratio(now=self.now)
        second = provider.get_ratio(now=self.now + timedelta(hours=2))
        self.assertEqual(first, 10.03)
        self.assertEqual(second, 10.10)
        self.assertEqual(mock_measure.call_count, 2)

    @patch("spx0dte.ratio_calibration.measure_ratio_now")
    def test_falls_back_to_last_known_good_on_failure(self, mock_measure):
        mock_measure.side_effect = [10.03, RatioCalibrationError("no data")]
        provider = RatioProvider(client=self.client, fred_api_key="fake-key", max_age=timedelta(hours=1))
        first = provider.get_ratio(now=self.now)
        second = provider.get_ratio(now=self.now + timedelta(hours=2))
        self.assertEqual(first, 10.03)
        self.assertEqual(second, 10.03)  # fell back, not a guess

    @patch("spx0dte.ratio_calibration.measure_ratio_now")
    def test_bootstrap_only_on_first_failed_attempt(self, mock_measure):
        mock_measure.side_effect = RatioCalibrationError("no data")
        provider = RatioProvider(client=self.client, fred_api_key="fake-key")
        ratio = provider.get_ratio(now=self.now)
        self.assertEqual(ratio, BOOTSTRAP_RATIO)


if __name__ == "__main__":
    unittest.main()
