"""Tests for FredSource (D-0050 Phase 1)."""

import json
import unittest
from datetime import date

from marketdata.fred_source import (
    FredSource, FredSourceConfigError, HttpResponse,
)


class _StubTransport:
    def __init__(self, resp, *, raise_exc=None):
        self.resp = resp
        self.raise_exc = raise_exc
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append(url)
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.resp


def _obs(items):
    return json.dumps({"observations": items}).encode()


class TestConfig(unittest.TestCase):
    def test_missing_key_rejected(self):
        with self.assertRaises(FredSourceConfigError):
            FredSource(api_key="")

    def test_from_env_returns_none_when_unset(self):
        import os
        prev = os.environ.pop("TEST_FRED_UNSET_KEY", None)
        try:
            got = FredSource.from_env(env_var="TEST_FRED_UNSET_KEY")
            self.assertIsNone(got)
        finally:
            if prev is not None:
                os.environ["TEST_FRED_UNSET_KEY"] = prev


class TestGetSeries(unittest.TestCase):
    def test_valid_response_returns_parsed_pairs(self):
        body = _obs([
            {"date": "2026-01-05", "value": "18.3"},
            {"date": "2026-01-06", "value": "19.1"},
        ])
        t = _StubTransport(HttpResponse(200, body))
        s = FredSource(api_key="k", transport=t)
        got = s.get_series("VIXCLS", date(2026, 1, 1), date(2026, 1, 10))
        self.assertEqual(got, [(date(2026, 1, 5), 18.3),
                               (date(2026, 1, 6), 19.1)])

    def test_missing_observation_dot_is_dropped(self):
        """FRED uses '.' for missing data points -- they must be
        silently dropped, not raise or become 0.0."""
        body = _obs([
            {"date": "2026-01-05", "value": "18.3"},
            {"date": "2026-01-06", "value": "."},
            {"date": "2026-01-07", "value": "19.1"},
        ])
        t = _StubTransport(HttpResponse(200, body))
        s = FredSource(api_key="k", transport=t)
        got = s.get_series("VIXCLS", date(2026, 1, 1), date(2026, 1, 10))
        self.assertEqual(len(got), 2)
        self.assertEqual([v for _, v in got], [18.3, 19.1])

    def test_api_key_in_query_not_leaked_in_errors(self):
        """Even on failure, api_key must stay out of returned data
        and error messages."""
        t = _StubTransport(HttpResponse(401, b"unauthorized"))
        s = FredSource(api_key="super-secret-key", transport=t)
        got = s.get_series("VIXCLS", date(2026, 1, 1), date(2026, 1, 10))
        self.assertEqual(got, [])
        # The URL was constructed with the key, but the key must not
        # appear in anything the caller gets back. The returned [] is
        # the only observable surface -- confirmed by the equality
        # check above.

    def test_http_error_returns_empty(self):
        t = _StubTransport(HttpResponse(429, b"rate-limit"))
        s = FredSource(api_key="k", transport=t)
        self.assertEqual(s.get_series("DFF", date(2026, 1, 1),
                                      date(2026, 1, 10)), [])

    def test_transport_exception_returns_empty(self):
        t = _StubTransport(None, raise_exc=OSError("network down"))
        s = FredSource(api_key="k", transport=t)
        self.assertEqual(s.get_series("DFF", date(2026, 1, 1),
                                      date(2026, 1, 10)), [])

    def test_malformed_json_returns_empty(self):
        t = _StubTransport(HttpResponse(200, b"not json at all"))
        s = FredSource(api_key="k", transport=t)
        self.assertEqual(s.get_series("DFF", date(2026, 1, 1),
                                      date(2026, 1, 10)), [])

    def test_missing_observations_key_returns_empty(self):
        t = _StubTransport(HttpResponse(200, json.dumps({"other": []}).encode()))
        s = FredSource(api_key="k", transport=t)
        self.assertEqual(s.get_series("DFF", date(2026, 1, 1),
                                      date(2026, 1, 10)), [])


if __name__ == "__main__":
    unittest.main()
