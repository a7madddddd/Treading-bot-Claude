"""Tests for PolygonSource + PolygonS3Config (D-0050 Phase 6)."""

import json
import os
import unittest
from datetime import date, datetime, timezone

from marketdata.polygon_source import (
    PolygonSource, PolygonS3Config, PolygonConfigError, HttpResponse,
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


class TestPolygonRest(unittest.TestCase):
    def test_missing_key_rejected(self):
        with self.assertRaises(PolygonConfigError):
            PolygonSource(api_key="")

    def test_from_env_none_when_unset(self):
        os.environ.pop("TEST_POLY_UNSET", None)
        self.assertIsNone(PolygonSource.from_env(env_var="TEST_POLY_UNSET"))

    def test_aggregates_parsed(self):
        ts1 = int(datetime(2026, 1, 5, tzinfo=timezone.utc).timestamp() * 1000)
        ts2 = int(datetime(2026, 1, 6, tzinfo=timezone.utc).timestamp() * 1000)
        body = {"status": "OK", "results": [
            {"t": ts1, "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.5, "v": 1e6},
            {"t": ts2, "o": 100.5, "h": 102.0, "l": 100.0, "c": 101.5, "v": 1.2e6},
        ]}
        s = PolygonSource(api_key="k", transport=_Stub(_r(200, body)))
        got = s.get_aggregates("TSLA", 1, "day",
                               date(2026, 1, 1), date(2026, 1, 10))
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0][0], date(2026, 1, 5))
        self.assertAlmostEqual(got[0][1]["c"], 100.5)

    def test_aggregates_error_status(self):
        body = {"status": "ERROR", "error": "bad request"}
        s = PolygonSource(api_key="k", transport=_Stub(_r(200, body)))
        self.assertEqual(
            s.get_aggregates("BAD", 1, "day",
                             date(2026, 1, 1), date(2026, 1, 2)), [])

    def test_snapshot_parsed(self):
        body = {"status": "OK", "ticker": {"ticker": "TSLA",
                                            "day": {"c": 250.0}}}
        s = PolygonSource(api_key="k", transport=_Stub(_r(200, body)))
        got = s.get_ticker_snapshot("TSLA")
        self.assertEqual(got["ticker"], "TSLA")

    def test_snapshot_falls_back_to_prev_on_free_tier(self):
        """Free-tier keys get 403/empty on /v2/snapshot. The method must
        fall back to /v2/aggs/ticker/{sym}/prev and shape the result like
        a snapshot so downstream consumers (research_hub, deep_research)
        read the same keys."""
        calls = []

        class _MultiStub:
            def __init__(self):
                self.last_url = None
            def __call__(self, url, headers, timeout):
                calls.append(url)
                if "snapshot/locale" in url:
                    # free-tier typical: 200 but empty ticker, or non-OK
                    return _r(200, {"status": "OK", "ticker": {}})
                if "aggs/ticker" in url and "/prev" in url:
                    return _r(200, {"status": "OK", "results": [
                        {"c": 250.0, "h": 252.0, "l": 248.0, "o": 249.0,
                         "v": 1_000_000, "t": 1700000000000},
                    ]})
                return _r(500, {})

        s = PolygonSource(api_key="k", transport=_MultiStub())
        got = s.get_ticker_snapshot("TSLA")
        self.assertIsNotNone(got)
        self.assertEqual(got["day"]["c"], 250.0)
        self.assertEqual(got["day"]["v"], 1_000_000)
        # prevDay must be EMPTY so research_hub doesn't compute a fake 0.00% change
        self.assertEqual(got["prevDay"], {})
        self.assertEqual(got.get("_fallback_source"), "aggs/prev")
        # Confirms both calls happened in order
        self.assertEqual(len(calls), 2)
        self.assertIn("snapshot/locale", calls[0])
        self.assertIn("/prev", calls[1])

    def test_snapshot_returns_none_when_both_fail(self):
        class _AllFail:
            def __call__(self, url, headers, timeout):
                return _r(500, {})
        s = PolygonSource(api_key="k", transport=_AllFail())
        self.assertIsNone(s.get_ticker_snapshot("TSLA"))

    def test_details_parsed(self):
        body = {"status": "OK",
                "results": {"ticker": "TSLA", "name": "Tesla, Inc."}}
        s = PolygonSource(api_key="k", transport=_Stub(_r(200, body)))
        got = s.get_ticker_details("TSLA")
        self.assertEqual(got["name"], "Tesla, Inc.")

    def test_news_parsed(self):
        body = {"status": "OK", "results": [
            {"title": "A"}, {"title": "B"},
        ]}
        s = PolygonSource(api_key="k", transport=_Stub(_r(200, body)))
        self.assertEqual(len(s.get_news("TSLA")), 2)

    def test_http_error_returns_empty(self):
        s = PolygonSource(api_key="k", transport=_Stub(_r(429, {})))
        self.assertEqual(
            s.get_aggregates("T", 1, "day",
                             date(2026, 1, 1), date(2026, 1, 2)), [])

    def test_transport_exception_returns_empty(self):
        s = PolygonSource(api_key="k",
                          transport=_Stub(None, raise_exc=OSError("x")))
        self.assertEqual(s.get_news("T"), [])

    def test_api_key_in_bearer_not_url(self):
        t = _Stub(_r(200, {"status": "OK", "results": {"ticker": "X"}}))
        s = PolygonSource(api_key="super-secret", transport=t)
        s.get_ticker_details("X")
        self.assertNotIn("super-secret", t.last_url)
        self.assertEqual(t.last_headers["Authorization"], "Bearer super-secret")


class TestPolygonS3Config(unittest.TestCase):
    def test_missing_endpoint_rejected(self):
        with self.assertRaises(PolygonConfigError):
            PolygonS3Config(endpoint="", access_key_id="k", secret_key="s")

    def test_missing_credentials_rejected(self):
        with self.assertRaises(PolygonConfigError):
            PolygonS3Config(endpoint="https://x", access_key_id="",
                            secret_key="s")

    def test_repr_hides_secrets(self):
        c = PolygonS3Config(endpoint="https://files.polygon.io",
                            access_key_id="AKIAEXAMPLE",
                            secret_key="super-secret-value")
        r = repr(c)
        self.assertNotIn("super-secret-value", r)
        self.assertNotIn("AKIAEXAMPLE", r)
        self.assertIn("***", r)

    def test_from_env_none_when_any_missing(self):
        for leave_out in ("POLYGON_S3_ENDPOINT",
                          "POLYGON_S3_ACCESS_KEY_ID",
                          "POLYGON_S3_SECRET_KEY"):
            saved = {}
            for k in ("POLYGON_S3_ENDPOINT", "POLYGON_S3_ACCESS_KEY_ID",
                      "POLYGON_S3_SECRET_KEY"):
                saved[k] = os.environ.pop(k, None)
                if k != leave_out:
                    os.environ[k] = "x"
            try:
                self.assertIsNone(PolygonS3Config.from_env(),
                                  f"should fail-open with {leave_out} missing")
            finally:
                for k, v in saved.items():
                    os.environ.pop(k, None)
                    if v is not None:
                        os.environ[k] = v

    def test_from_env_all_present(self):
        saved = {}
        for k in ("POLYGON_S3_ENDPOINT", "POLYGON_S3_ACCESS_KEY_ID",
                  "POLYGON_S3_SECRET_KEY"):
            saved[k] = os.environ.get(k)
        os.environ["POLYGON_S3_ENDPOINT"] = "https://files.polygon.io"
        os.environ["POLYGON_S3_ACCESS_KEY_ID"] = "k"
        os.environ["POLYGON_S3_SECRET_KEY"] = "s"
        try:
            got = PolygonS3Config.from_env()
            self.assertIsNotNone(got)
            self.assertEqual(got.endpoint, "https://files.polygon.io")
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
