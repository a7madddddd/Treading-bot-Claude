import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from spx0dte.alpaca_client import AlpacaDataError
from spx0dte.vix_proxy import (
    MAX_PLAUSIBLE_RATIO, VixAnchor, VixProxyError, VixProxyProvider, measure_vix_anchor_now,
)


class FakeAlpacaForVix:
    """Minimal stand-in exposing only what vix_proxy needs."""

    def __init__(self, daily_bars=None, minute_bars=None, raise_on_minute=False):
        self._daily_bars = daily_bars or []
        self._minute_bars = minute_bars or []
        self._raise_on_minute = raise_on_minute

    def get_stock_daily_bars(self, symbol, start_date, end_date):
        return self._daily_bars

    def get_stock_minute_bars(self, symbol, date):
        if self._raise_on_minute:
            raise AlpacaDataError("simulated failure")
        return self._minute_bars


class TestMeasureVixAnchorNow(unittest.TestCase):
    @patch("spx0dte.vix_proxy.fetch_vixcls_closes")
    def test_finds_most_recent_prior_matched_day(self, mock_fetch):
        mock_fetch.return_value = {
            "2026-11-14": 15.0,
            "2026-11-13": 14.0,
        }
        client = FakeAlpacaForVix(daily_bars=[
            {"t": "2026-11-13T00:00:00Z", "c": 20.0},
            {"t": "2026-11-14T00:00:00Z", "c": 21.0},
        ])
        now = datetime(2026, 11, 17, 14, 0, tzinfo=timezone.utc)
        anchor = measure_vix_anchor_now(client, "fake-key", now=now)
        self.assertEqual(anchor.yesterday_vix_close, 15.0)
        self.assertEqual(anchor.yesterday_vixy_close, 21.0)
        self.assertEqual(anchor.date, "2026-11-17")

    @patch("spx0dte.vix_proxy.fetch_vixcls_closes")
    def test_skips_a_day_with_no_vixy_match(self, mock_fetch):
        mock_fetch.return_value = {"2026-11-14": 15.0, "2026-11-13": 14.0}
        client = FakeAlpacaForVix(daily_bars=[
            {"t": "2026-11-13T00:00:00Z", "c": 20.0},  # 11-14 has no VIXY bar
        ])
        now = datetime(2026, 11, 17, 14, 0, tzinfo=timezone.utc)
        anchor = measure_vix_anchor_now(client, "fake-key", now=now)
        self.assertEqual(anchor.yesterday_vix_close, 14.0)

    @patch("spx0dte.vix_proxy.fetch_vixcls_closes")
    def test_raises_when_nothing_matches(self, mock_fetch):
        mock_fetch.return_value = {"2099-01-01": 99.0}
        client = FakeAlpacaForVix(daily_bars=[])
        now = datetime(2026, 11, 17, 14, 0, tzinfo=timezone.utc)
        with self.assertRaises(VixProxyError):
            measure_vix_anchor_now(client, "fake-key", now=now)


class TestVixProxyProvider(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc)
        self.anchor = VixAnchor(date="2026-11-17", yesterday_vix_close=15.0, yesterday_vixy_close=20.0)

    def test_estimate_scales_anchor_by_live_ratio(self):
        client = FakeAlpacaForVix(minute_bars=[{"t": "2026-11-17T15:00:00Z", "c": 22.0}])  # +10% vs 20.0
        provider = VixProxyProvider(client=client, fred_api_key="fake-key")
        provider._anchor = self.anchor  # pre-seed, skip the fetch path
        level = provider.get_vix_level(now=self.now)
        self.assertAlmostEqual(level, 16.5, places=4)  # 15.0 * 1.1

    def test_returns_none_when_minute_bars_unavailable(self):
        client = FakeAlpacaForVix(raise_on_minute=True)
        provider = VixProxyProvider(client=client, fred_api_key="fake-key")
        provider._anchor = self.anchor
        self.assertIsNone(provider.get_vix_level(now=self.now))

    def test_returns_none_when_no_minute_bars_today(self):
        client = FakeAlpacaForVix(minute_bars=[])
        provider = VixProxyProvider(client=client, fred_api_key="fake-key")
        provider._anchor = self.anchor
        self.assertIsNone(provider.get_vix_level(now=self.now))

    def test_rejects_implausible_ratio_above_bound(self):
        # vixy_now / yesterday_vixy_close = 100/20 = 5.0 > MAX_PLAUSIBLE_RATIO
        client = FakeAlpacaForVix(minute_bars=[{"t": "2026-11-17T15:00:00Z", "c": 100.0}])
        provider = VixProxyProvider(client=client, fred_api_key="fake-key")
        provider._anchor = self.anchor
        self.assertIsNone(provider.get_vix_level(now=self.now))

    def test_rejects_implausible_ratio_below_bound(self):
        client = FakeAlpacaForVix(minute_bars=[{"t": "2026-11-17T15:00:00Z", "c": 1.0}])
        provider = VixProxyProvider(client=client, fred_api_key="fake-key")
        provider._anchor = self.anchor
        self.assertIsNone(provider.get_vix_level(now=self.now))

    def test_accepts_right_at_the_plausibility_bound(self):
        bound_price = self.anchor.yesterday_vixy_close * MAX_PLAUSIBLE_RATIO
        client = FakeAlpacaForVix(minute_bars=[{"t": "2026-11-17T15:00:00Z", "c": bound_price}])
        provider = VixProxyProvider(client=client, fred_api_key="fake-key")
        provider._anchor = self.anchor
        level = provider.get_vix_level(now=self.now)
        self.assertAlmostEqual(level, self.anchor.yesterday_vix_close * MAX_PLAUSIBLE_RATIO, places=4)

    @patch("spx0dte.vix_proxy.measure_vix_anchor_now")
    def test_remeasures_anchor_on_a_new_day(self, mock_measure):
        mock_measure.return_value = self.anchor
        client = FakeAlpacaForVix(minute_bars=[{"t": "2026-11-17T15:00:00Z", "c": 20.0}])
        provider = VixProxyProvider(client=client, fred_api_key="fake-key")
        provider.get_vix_level(now=self.now)
        mock_measure.assert_called_once()

        provider.get_vix_level(now=self.now + timedelta(minutes=5))  # same day
        mock_measure.assert_called_once()  # not called again

        provider.get_vix_level(now=self.now + timedelta(days=1))  # new day
        self.assertEqual(mock_measure.call_count, 2)

    @patch("spx0dte.vix_proxy.measure_vix_anchor_now")
    def test_returns_none_when_anchor_measurement_fails(self, mock_measure):
        mock_measure.side_effect = VixProxyError("no data")
        client = FakeAlpacaForVix(minute_bars=[{"t": "2026-11-17T15:00:00Z", "c": 20.0}])
        provider = VixProxyProvider(client=client, fred_api_key="fake-key")
        self.assertIsNone(provider.get_vix_level(now=self.now))


if __name__ == "__main__":
    unittest.main()
