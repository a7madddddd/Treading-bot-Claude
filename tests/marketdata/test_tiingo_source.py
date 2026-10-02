"""Tests for TiingoSource (D-0050 Phase 5)."""

import json
import unittest
from datetime import date

from marketdata.tiingo_source import (
    TiingoSource, TiingoConfigError, HttpResponse,
)


class _Stub:
    def __init__(self, resp, *, raise_exc=None):
        self.resp = resp
        self.raise_exc = raise_exc
        self.last_headers = None
        self.last_url = None

    def __call__(self, url, headers, timeout):
        self.last_url = url
        self.last_headers = dict(headers)
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.resp


def _r(status, obj):
    return HttpResponse(status, json.dumps(obj).encode())


class TestTiingo(unittest.TestCase):
    def test_missing_key_rejected(self):
        with self.assertRaises(TiingoConfigError):
            TiingoSource(api_key="")

    def test_from_env_none_when_unset(self):
        import os
        os.environ.pop("TEST_TIINGO_UNSET", None)
        self.assertIsNone(TiingoSource.from_env(env_var="TEST_TIINGO_UNSET"))

    def test_eod_prices_parsed(self):
        body = [
            {"date": "2026-01-05T00:00:00.000Z", "close": 180.5,
             "open": 179.0, "high": 181.0, "low": 178.0, "volume": 1000000},
            {"date": "2026-01-06T00:00:00.000Z", "close": 182.0,
             "open": 180.5, "high": 183.0, "low": 180.0, "volume": 1200000},
        ]
        t = _Stub(_r(200, body))
        s = TiingoSource(api_key="k", transport=t)
        got = s.get_eod_prices("TSLA", date(2026, 1, 1), date(2026, 1, 10))
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0][0], date(2026, 1, 5))
        self.assertAlmostEqual(got[0][1]["close"], 180.5)

    def test_eod_prices_sorted_ascending(self):
        body = [
            {"date": "2026-01-07T00:00:00.000Z", "close": 1.0},
            {"date": "2026-01-05T00:00:00.000Z", "close": 2.0},
            {"date": "2026-01-06T00:00:00.000Z", "close": 3.0},
        ]
        s = TiingoSource(api_key="k", transport=_Stub(_r(200, body)))
        got = s.get_eod_prices("TSLA", date(2026, 1, 1), date(2026, 1, 10))
        self.assertEqual([d for d, _ in got], [
            date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)
        ])

    def test_news_parsed(self):
        body = [
            {"title": "A", "publishedDate": "2026-01-05"},
            {"title": "B", "publishedDate": "2026-01-04"},
        ]
        s = TiingoSource(api_key="k", transport=_Stub(_r(200, body)))
        got = s.get_news(["TSLA"])
        self.assertEqual(len(got), 2)

    def test_metadata_parsed(self):
        body = {"ticker": "TSLA", "name": "Tesla", "exchangeCode": "NASDAQ"}
        s = TiingoSource(api_key="k", transport=_Stub(_r(200, body)))
        got = s.get_metadata("TSLA")
        self.assertEqual(got["ticker"], "TSLA")

    def test_metadata_empty_returns_none(self):
        s = TiingoSource(api_key="k", transport=_Stub(_r(200, {})))
        self.assertIsNone(s.get_metadata("TSLA"))

    def test_http_error_returns_empty(self):
        s = TiingoSource(api_key="k", transport=_Stub(_r(401, {})))
        self.assertEqual(s.get_eod_prices("TSLA", date(2026, 1, 1),
                                          date(2026, 1, 2)), [])

    def test_transport_exception_returns_empty(self):
        s = TiingoSource(api_key="k",
                         transport=_Stub(None, raise_exc=OSError("x")))
        self.assertEqual(s.get_news(["TSLA"]), [])

    def test_api_key_in_header_not_url(self):
        t = _Stub(_r(200, []))
        s = TiingoSource(api_key="super-secret-token", transport=t)
        s.get_eod_prices("TSLA", date(2026, 1, 1), date(2026, 1, 2))
        self.assertNotIn("super-secret-token", t.last_url)
        self.assertIn("Token super-secret-token", t.last_headers["Authorization"])

    def test_empty_symbols_news_returns_empty_without_call(self):
        t = _Stub(_r(200, []))
        s = TiingoSource(api_key="k", transport=t)
        got = s.get_news([])
        self.assertEqual(got, [])
        self.assertIsNone(t.last_url)


if __name__ == "__main__":
    unittest.main()
