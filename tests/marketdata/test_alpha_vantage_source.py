"""Tests for AlphaVantageSource (D-0050 Phase 4)."""

import json
import unittest
from datetime import date

from marketdata.alpha_vantage_source import (
    AlphaVantageSource, AlphaVantageConfigError, HttpResponse,
)


class _Stub:
    def __init__(self, resp, *, raise_exc=None):
        self.resp = resp
        self.raise_exc = raise_exc

    def __call__(self, url, headers, timeout):
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.resp


def _r(status, obj):
    return HttpResponse(status, json.dumps(obj).encode())


class TestAlphaVantage(unittest.TestCase):
    def test_missing_key_rejected(self):
        with self.assertRaises(AlphaVantageConfigError):
            AlphaVantageSource(api_key="")

    def test_from_env_none_when_unset(self):
        import os
        os.environ.pop("TEST_AV_UNSET", None)
        self.assertIsNone(AlphaVantageSource.from_env(env_var="TEST_AV_UNSET"))

    def test_rsi_parsed(self):
        body = {"Technical Analysis: RSI": {
            "2026-10-01": {"RSI": "55.3"},
            "2026-10-02": {"RSI": "58.1"},
        }}
        s = AlphaVantageSource(api_key="k", transport=_Stub(_r(200, body)))
        got = s.rsi("TSLA")
        self.assertEqual(got, [
            (date(2026, 10, 1), 55.3),
            (date(2026, 10, 2), 58.1),
        ])

    def test_rate_limit_note_returns_empty(self):
        """Alpha Vantage returns {'Note': '...'} when rate limited; we
        must treat it as an outage, not propagate garbage."""
        body = {"Note": "Thank you for using Alpha Vantage!"}
        s = AlphaVantageSource(api_key="k", transport=_Stub(_r(200, body)))
        self.assertEqual(s.rsi("TSLA"), [])

    def test_macd_parsed(self):
        body = {"Technical Analysis: MACD": {
            "2026-10-01": {"MACD": "1.5", "MACD_Signal": "1.3",
                            "MACD_Hist": "0.2"},
        }}
        s = AlphaVantageSource(api_key="k", transport=_Stub(_r(200, body)))
        got = s.macd("TSLA")
        self.assertEqual(len(got), 1)
        self.assertAlmostEqual(got[0][1]["macd"], 1.5)
        self.assertAlmostEqual(got[0][1]["hist"], 0.2)

    def test_bbands_parsed(self):
        body = {"Technical Analysis: BBANDS": {
            "2026-10-01": {"Real Upper Band": "260.0",
                            "Real Middle Band": "250.0",
                            "Real Lower Band": "240.0"},
        }}
        s = AlphaVantageSource(api_key="k", transport=_Stub(_r(200, body)))
        got = s.bbands("TSLA")
        self.assertAlmostEqual(got[0][1]["upper"], 260.0)
        self.assertAlmostEqual(got[0][1]["lower"], 240.0)

    def test_http_error_returns_empty(self):
        s = AlphaVantageSource(api_key="k", transport=_Stub(_r(500, {})))
        self.assertEqual(s.rsi("TSLA"), [])

    def test_transport_exception_returns_empty(self):
        s = AlphaVantageSource(api_key="k",
                               transport=_Stub(None, raise_exc=OSError("x")))
        self.assertEqual(s.macd("TSLA"), [])

    def test_malformed_json_returns_empty(self):
        s = AlphaVantageSource(api_key="k",
                               transport=_Stub(HttpResponse(200, b"junk")))
        self.assertEqual(s.bbands("TSLA"), [])

    def test_error_message_treated_as_outage(self):
        body = {"Error Message": "Invalid API call"}
        s = AlphaVantageSource(api_key="k", transport=_Stub(_r(200, body)))
        self.assertEqual(s.rsi("BAD_SYMBOL"), [])


if __name__ == "__main__":
    unittest.main()
