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

    def test_as_of_date_propagates(self):
        body = json.dumps([_asset("AAPL")]).encode()
        p = self._provider(HttpResponse(200, body))
        [r] = p.get_raw_candidates(date(2027, 3, 15))
        self.assertEqual(r.as_of_date, date(2027, 3, 15))


if __name__ == "__main__":
    unittest.main()
