"""Tests for QuiverQuantSource (D-0050 Phase B.20)."""

import json
import os
import unittest
from marketdata.quiverquant_source import (
    QuiverQuantSource, QuiverQuantConfigError, HttpResponse,
)


class _Stub:
    def __init__(self, resp, raise_exc=None):
        self.resp = resp; self.raise_exc = raise_exc
        self.last_headers = None; self.last_url = None
    def __call__(self, url, headers, timeout):
        self.last_url = url; self.last_headers = dict(headers)
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.resp


def _r(status, body):
    return HttpResponse(status, json.dumps(body).encode())


class TestQuiverQuant(unittest.TestCase):
    def test_missing_key_rejected(self):
        with self.assertRaises(QuiverQuantConfigError):
            QuiverQuantSource(api_key="")

    def test_from_env_none_when_unset(self):
        os.environ.pop("TEST_QQ_UNSET", None)
        self.assertIsNone(QuiverQuantSource.from_env(env_var="TEST_QQ_UNSET"))

    def test_recent_trades_parsed(self):
        body = [{
            "Representative": "Nancy Pelosi",
            "Transaction": "Purchase",
            "Ticker": "NVDA",
            "Range": "$1,000,001 - $5,000,000",
            "TransactionDate": "2026-09-28",
            "Chamber": "House",
        }]
        s = QuiverQuantSource(api_key="k", transport=_Stub(_r(200, body)))
        rows = s.recent_congress_trades()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Ticker"], "NVDA")

    def test_http_error_returns_empty(self):
        s = QuiverQuantSource(api_key="k",
                               transport=_Stub(_r(401, {"error":"x"})))
        self.assertEqual(s.recent_congress_trades(), [])

    def test_transport_exception(self):
        s = QuiverQuantSource(api_key="k",
                               transport=_Stub(None, raise_exc=OSError("x")))
        self.assertEqual(s.recent_congress_trades(), [])

    def test_malformed_json(self):
        s = QuiverQuantSource(api_key="k",
                               transport=_Stub(HttpResponse(200, b"garbage")))
        self.assertEqual(s.recent_congress_trades(), [])

    def test_api_key_in_bearer_header_not_url(self):
        t = _Stub(_r(200, []))
        s = QuiverQuantSource(api_key="super-secret", transport=t)
        s.recent_congress_trades()
        self.assertNotIn("super-secret", t.last_url)
        self.assertEqual(t.last_headers["Authorization"], "Bearer super-secret")

    def test_historical_trades_empty_ticker(self):
        s = QuiverQuantSource(api_key="k", transport=_Stub(_r(200, [])))
        self.assertEqual(s.historical_trades(""), [])


if __name__ == "__main__":
    unittest.main()
