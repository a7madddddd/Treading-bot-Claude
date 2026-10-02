"""Tests for FinnhubSource (D-0050 Phase 3)."""

import json
import unittest
from datetime import date

from marketdata.finnhub_source import (
    FinnhubSource, FinnhubConfigError, HttpResponse,
)


class _Stub:
    def __init__(self, resp, *, raise_exc=None):
        self.resp = resp
        self.raise_exc = raise_exc
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append(url)
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.resp


def _resp(status, body_obj):
    return HttpResponse(status, json.dumps(body_obj).encode())


class TestFinnhub(unittest.TestCase):
    def test_missing_key_rejected(self):
        with self.assertRaises(FinnhubConfigError):
            FinnhubSource(api_key="")

    def test_from_env_none_when_unset(self):
        import os
        os.environ.pop("TEST_FINNHUB_UNSET", None)
        self.assertIsNone(FinnhubSource.from_env(env_var="TEST_FINNHUB_UNSET"))

    def test_company_profile_valid(self):
        t = _Stub(_resp(200, {"name": "Tesla", "ticker": "TSLA"}))
        s = FinnhubSource(api_key="k", transport=t)
        got = s.company_profile("TSLA")
        self.assertEqual(got["name"], "Tesla")

    def test_company_profile_empty_dict_returns_none(self):
        t = _Stub(_resp(200, {}))
        s = FinnhubSource(api_key="k", transport=t)
        self.assertIsNone(s.company_profile("UNKNOWN"))

    def test_basic_financials_without_metric_returns_none(self):
        t = _Stub(_resp(200, {"symbol": "X"}))
        s = FinnhubSource(api_key="k", transport=t)
        self.assertIsNone(s.basic_financials("X"))

    def test_basic_financials_with_metric(self):
        body = {"metric": {"52WeekHigh": 300.0, "peBasicExclExtraTTM": 85.0}}
        t = _Stub(_resp(200, body))
        s = FinnhubSource(api_key="k", transport=t)
        got = s.basic_financials("TSLA")
        self.assertEqual(got["metric"]["peBasicExclExtraTTM"], 85.0)

    def test_company_news_filters_non_dicts(self):
        t = _Stub(_resp(200, [
            {"headline": "A"}, "bad", {"headline": "B"},
        ]))
        s = FinnhubSource(api_key="k", transport=t)
        got = s.company_news("TSLA", date(2026, 1, 1), date(2026, 1, 2))
        self.assertEqual(len(got), 2)

    def test_http_error_returns_none_or_empty(self):
        t = _Stub(_resp(401, {"error": "x"}))
        s = FinnhubSource(api_key="super-secret", transport=t)
        self.assertIsNone(s.company_profile("TSLA"))
        self.assertEqual(s.company_news("TSLA", date(2026, 1, 1), date(2026, 1, 2)), [])

    def test_transport_exception_returns_none(self):
        t = _Stub(None, raise_exc=OSError("down"))
        s = FinnhubSource(api_key="k", transport=t)
        self.assertIsNone(s.company_profile("TSLA"))

    def test_malformed_json(self):
        t = _Stub(HttpResponse(200, b"garbage"))
        s = FinnhubSource(api_key="k", transport=t)
        self.assertIsNone(s.company_profile("TSLA"))


if __name__ == "__main__":
    unittest.main()
