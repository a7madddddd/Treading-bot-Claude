"""Tests for AlpacaAssetsProvider (B24)."""

import json
import unittest
from datetime import date

from d0026.alpaca_provider import (
    AlpacaAssetsProvider, AlpacaAssetsProviderConfigError,
    AlpacaAssetsProviderFetchError, HttpResponse,
)


def _asset(symbol, *, tradable=True, fractionable=True, status="active"):
    return {
        "id": f"id-{symbol}", "class": "us_equity",
        "exchange": "NASDAQ", "symbol": symbol, "name": f"{symbol} Inc",
        "status": status, "tradable": tradable, "marginable": True,
        "fractionable": fractionable,
    }


class _StubTransport:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []
    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers), timeout))
        return self.resp


class TestConfig(unittest.TestCase):
    def test_missing_creds(self):
        with self.assertRaises(AlpacaAssetsProviderConfigError):
            AlpacaAssetsProvider(key_id="", secret_key="s")
        with self.assertRaises(AlpacaAssetsProviderConfigError):
            AlpacaAssetsProvider(key_id="k", secret_key="")


class TestFetch(unittest.TestCase):
    def _provider(self, resp, **kwargs):
        return AlpacaAssetsProvider(
            key_id="k", secret_key="s",
            transport=_StubTransport(resp), **kwargs,
        )

    def test_all_tradable_returned(self):
        body = json.dumps([_asset("AAPL"), _asset("TSLA"),
                           _asset("SPY")]).encode()
        p = self._provider(HttpResponse(200, body))
        result = p.get_raw_candidates(date(2026, 1, 5))
        tickers = {r.ticker for r in result}
        self.assertEqual(tickers, {"AAPL", "TSLA", "SPY"})

    def test_non_tradable_excluded(self):
        body = json.dumps([
            _asset("AAPL"),
            _asset("DEAD", tradable=False),
        ]).encode()
        p = self._provider(HttpResponse(200, body))
        result = p.get_raw_candidates(date(2026, 1, 5))
        self.assertEqual([r.ticker for r in result], ["AAPL"])

    def test_whitelist_intersection(self):
        body = json.dumps([_asset("AAPL"), _asset("TSLA"),
                           _asset("SPY")]).encode()
        p = self._provider(HttpResponse(200, body),
                           symbol_whitelist=("TSLA", "AAPL"))
        tickers = {r.ticker for r in p.get_raw_candidates(date(2026, 1, 5))}
        self.assertEqual(tickers, {"TSLA", "AAPL"})

    def test_require_fractionable(self):
        body = json.dumps([_asset("AAPL"), _asset("X", fractionable=False)]).encode()
        p = self._provider(HttpResponse(200, body), require_fractionable=True)
        self.assertEqual([r.ticker for r in p.get_raw_candidates(date(2026,1,5))],
                         ["AAPL"])

    def test_http_error_raises(self):
        p = self._provider(HttpResponse(500, b'{"error":"oops"}'))
        with self.assertRaises(AlpacaAssetsProviderFetchError):
            p.get_raw_candidates(date(2026, 1, 5))

    def test_garbage_json_raises_fetch_error(self):
        """Regression: malformed body must raise AlpacaAssetsProviderFetchError
        (uniform error type), not a raw JSONDecodeError."""
        p = self._provider(HttpResponse(200, b"not-json"))
        with self.assertRaises(AlpacaAssetsProviderFetchError):
            p.get_raw_candidates(date(2026, 1, 5))

    def test_non_list_body_raises_fetch_error(self):
        p = self._provider(HttpResponse(200, b'{"error": "wat"}'))
        with self.assertRaises(AlpacaAssetsProviderFetchError):
            p.get_raw_candidates(date(2026, 1, 5))

    def test_as_of_date_propagates(self):
        body = json.dumps([_asset("AAPL")]).encode()
        p = self._provider(HttpResponse(200, body))
        [r] = p.get_raw_candidates(date(2027, 3, 15))
        self.assertEqual(r.as_of_date, date(2027, 3, 15))


if __name__ == "__main__":
    unittest.main()


def _named_asset(symbol, name, *, tradable=True):
    return {
        "id": f"id-{symbol}", "class": "us_equity",
        "exchange": "NASDAQ", "symbol": symbol, "name": name,
        "status": "active", "tradable": tradable, "marginable": True,
        "fractionable": True,
    }


class TestLeveragedInverseExclusion(unittest.TestCase):
    """P-021, Controller-approved 2026-10-05: leveraged and inverse
    products must never reach the pipeline. DXD is the live case that
    caused this filter -- it was selected into the Controller's real
    2026-10-03 snapshot on the Oracle VM."""

    LIVE_SNAPSHOT = (
        ("WBD", "Warner Bros. Discovery, Inc."),
        ("MUFG", "Mitsubishi UFJ Financial Group, Inc."),
        ("DXD", "ProShares UltraShort Dow30"),          # the -2x inverse
        ("VOD", "Vodafone Group Plc"),
        ("MAGS", "Roundhill Magnificent Seven ETF"),
        ("QQQI", "NEOS Nasdaq-100 High Income ETF"),
        ("PFE", "Pfizer, Inc."),
        ("ILF", "iShares Latin America 40 ETF"),
        ("CGGR", "Capital Group Growth ETF"),
        ("BCI", "abrdn Bloomberg All Commodity Strategy K-1 Free ETF"),
    )

    def _provider(self, assets, **kwargs):
        body = json.dumps(assets).encode()
        return AlpacaAssetsProvider(
            key_id="k", secret_key="s",
            transport=_StubTransport(HttpResponse(200, body)),
            **kwargs,
        )

    def _tickers(self, provider):
        return [c.ticker for c in
                provider.get_raw_candidates(date(2026, 10, 6))]

    def test_dxd_is_dropped_from_the_real_snapshot_universe(self):
        assets = [_named_asset(s, n) for s, n in self.LIVE_SNAPSHOT]
        tickers = self._tickers(self._provider(assets))
        self.assertNotIn("DXD", tickers)

    def test_the_other_nine_all_survive(self):
        """The filter must remove ONLY the leveraged/inverse one. The
        ordinary funds are P-024's deferred question, not this one's."""
        assets = [_named_asset(s, n) for s, n in self.LIVE_SNAPSHOT]
        tickers = self._tickers(self._provider(assets))
        expected = [s for s, _ in self.LIVE_SNAPSHOT if s != "DXD"]
        self.assertEqual(tickers, expected)

    def test_exclusion_reason_is_reported_not_silent(self):
        assets = [_named_asset(s, n) for s, n in self.LIVE_SNAPSHOT]
        provider = self._provider(assets)
        self._tickers(provider)
        self.assertEqual(len(provider.last_excluded), 1)
        self.assertIn("DXD", provider.last_excluded[0])
        self.assertIn("ULTRASHORT", provider.last_excluded[0])

    def test_filter_is_on_by_default(self):
        """A safety filter must be opted OUT of, never opted into."""
        assets = [_named_asset("DXD", "ProShares UltraShort Dow30")]
        self.assertEqual(self._tickers(self._provider(assets)), [])

    def test_can_be_disabled_explicitly(self):
        assets = [_named_asset("DXD", "ProShares UltraShort Dow30")]
        provider = self._provider(assets, exclude_leveraged_inverse=False)
        self.assertEqual(self._tickers(provider), ["DXD"])
        self.assertEqual(provider.last_excluded, ())

    def test_last_excluded_resets_between_calls(self):
        """A stale count would misreport a later clean run."""
        assets = [_named_asset("DXD", "ProShares UltraShort Dow30"),
                  _named_asset("AAPL", "Apple Inc.")]
        provider = self._provider(assets)
        self._tickers(provider)
        self.assertEqual(len(provider.last_excluded), 1)
        clean = json.dumps([_named_asset("AAPL", "Apple Inc.")]).encode()
        provider._transport = _StubTransport(HttpResponse(200, clean))
        self._tickers(provider)
        self.assertEqual(provider.last_excluded, ())

    def test_asset_without_a_name_is_kept_not_dropped(self):
        """Missing data is not evidence of leverage."""
        asset = _named_asset("AAPL", "Apple Inc.")
        del asset["name"]
        self.assertEqual(self._tickers(self._provider([asset])), ["AAPL"])

    def test_non_tradable_still_filtered_first(self):
        assets = [_named_asset("AAPL", "Apple Inc.", tradable=False)]
        self.assertEqual(self._tickers(self._provider(assets)), [])

    def test_whitelist_and_eligibility_compose(self):
        """A whitelisted leveraged fund is still excluded -- the safety
        filter is not overridable by naming the symbol."""
        assets = [_named_asset("DXD", "ProShares UltraShort Dow30"),
                  _named_asset("AAPL", "Apple Inc.")]
        provider = self._provider(assets, symbol_whitelist=["DXD", "AAPL"])
        self.assertEqual(self._tickers(provider), ["AAPL"])
