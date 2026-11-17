import unittest
from datetime import datetime, timedelta, timezone

from spx0dte.greeks import OptionType, black_scholes_price
from spx0dte.live_pricing import (
    LivePricingUnavailableError, MEASURED_BIAS_PCT, get_live_entry_quote, reprice_known_symbol,
)


class FakeRatioProvider:
    def __init__(self, ratio: float):
        self._ratio = ratio

    def get_ratio(self, *, now=None) -> float:
        return self._ratio


class FakeClient:
    """Minimal stand-in exposing only what get_live_entry_quote needs."""

    def __init__(self, *, spy_bars, found_strike, option_bars):
        self._spy_bars = spy_bars
        self._found_strike = found_strike
        self._option_bars = option_bars

    def get_spy_minute_bars(self, date):
        return self._spy_bars

    def find_nearest_traded_strike(self, date, approx_spx_level, call_put, around_time_iso):
        return self._found_strike

    def get_option_bars(self, symbol, start_iso, end_iso, limit=50):
        return self._option_bars


def _bar(t: datetime, c: float) -> dict:
    return {"t": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "c": c}


class TestGetLiveEntryQuote(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc)  # well before 21:00 UTC close
        self.ratio = 10.0
        self.ratio_provider = FakeRatioProvider(self.ratio)
        self.K = 5900

        # Build a consistent scenario: SPY stable at 590 all session
        # (so live_S == stale_S == 5900 for a clean sanity check),
        # with a real stale quote 40 minutes before `now`.
        self.spy_bars = [
            _bar(self.now - timedelta(minutes=m), 590.0)
            for m in range(0, 120, 1)
        ][::-1]  # ascending time order, like real bars

        expiry = self.now.replace(hour=21, minute=0, second=0, microsecond=0)
        stale_time = self.now - timedelta(minutes=40)
        stale_T = max((expiry - stale_time).total_seconds(), 1) / 86400 / 365
        true_sigma = 0.15
        stale_price = black_scholes_price(5900, self.K, stale_T, 0.045, true_sigma, OptionType.CALL)

        self.option_bars = [_bar(stale_time, stale_price)]
        self.client = FakeClient(
            spy_bars=self.spy_bars, found_strike=(self.K, "SPXW261117C05900000"),
            option_bars=self.option_bars,
        )

    def test_produces_a_quote_with_bias_correction_applied(self):
        quote = get_live_entry_quote(self.client, self.ratio_provider, "call", now=self.now)
        self.assertEqual(quote.strike, self.K)
        self.assertEqual(quote.occ_symbol, "SPXW261117C05900000")
        expected_corrected = quote.raw_estimated_price / (1 + MEASURED_BIAS_PCT)
        self.assertAlmostEqual(quote.bias_corrected_price, expected_corrected, places=6)
        self.assertLess(quote.bias_corrected_price, quote.raw_estimated_price)

    def test_rejects_bad_direction(self):
        with self.assertRaises(ValueError):
            get_live_entry_quote(self.client, self.ratio_provider, "up", now=self.now)

    def test_no_spy_bars_raises(self):
        client = FakeClient(spy_bars=[], found_strike=(self.K, "x"), option_bars=self.option_bars)
        with self.assertRaises(LivePricingUnavailableError):
            get_live_entry_quote(client, self.ratio_provider, "call", now=self.now)

    def test_no_strike_found_raises(self):
        client = FakeClient(spy_bars=self.spy_bars, found_strike=None, option_bars=self.option_bars)
        with self.assertRaises(LivePricingUnavailableError):
            get_live_entry_quote(client, self.ratio_provider, "call", now=self.now)

    def test_no_option_bars_raises(self):
        client = FakeClient(spy_bars=self.spy_bars, found_strike=(self.K, "x"), option_bars=[])
        with self.assertRaises(LivePricingUnavailableError):
            get_live_entry_quote(client, self.ratio_provider, "call", now=self.now)

    def test_bad_stale_quote_raises(self):
        # A price far below intrinsic value -- implied_volatility()
        # cannot solve for this, so reprice_from_stale_quote returns
        # None and get_live_entry_quote must raise, not fabricate.
        client = FakeClient(
            spy_bars=self.spy_bars, found_strike=(5000, "x"),
            option_bars=[_bar(self.now - timedelta(minutes=40), 0.0001)],
        )
        with self.assertRaises(LivePricingUnavailableError):
            get_live_entry_quote(client, self.ratio_provider, "call", now=self.now)


class TestRepriceKnownSymbol(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc)
        self.ratio_provider = FakeRatioProvider(10.0)
        self.K = 5900

        self.spy_bars = [
            _bar(self.now - timedelta(minutes=m), 590.0)
            for m in range(0, 120, 1)
        ][::-1]

        expiry = self.now.replace(hour=21, minute=0, second=0, microsecond=0)
        stale_time = self.now - timedelta(minutes=40)
        stale_T = max((expiry - stale_time).total_seconds(), 1) / 86400 / 365
        self.true_sigma = 0.20
        stale_price = black_scholes_price(5900, self.K, stale_T, 0.045, self.true_sigma, OptionType.PUT)
        self.client = FakeClient(
            spy_bars=self.spy_bars, found_strike=None,  # must not be used -- no search here
            option_bars=[_bar(stale_time, stale_price)],
        )

    def test_does_not_search_uses_given_symbol(self):
        quote = reprice_known_symbol(
            self.client, self.ratio_provider, occ_symbol="SPXW261117P05900000",
            strike=self.K, direction="put", now=self.now,
        )
        self.assertEqual(quote.occ_symbol, "SPXW261117P05900000")
        self.assertEqual(quote.strike, self.K)

    def test_rejects_bad_direction(self):
        with self.assertRaises(ValueError):
            reprice_known_symbol(
                self.client, self.ratio_provider, occ_symbol="x", strike=self.K,
                direction="sideways", now=self.now,
            )


if __name__ == "__main__":
    unittest.main()
