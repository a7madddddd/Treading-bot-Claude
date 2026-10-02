"""Tests for AVCache + cached AlphaVantageSource (D-0050 Phase 11)."""

import json
import os
import tempfile
import unittest
from datetime import date

from marketdata.av_cache import AVCache, make_cache_key
from marketdata.alpha_vantage_source import (
    AlphaVantageSource, HttpResponse,
)


class _StubTransport:
    def __init__(self, responses):
        self._resps = list(responses)
        self.calls = 0
    def __call__(self, url, headers, timeout):
        self.calls += 1
        return self._resps.pop(0)


def _resp(status, obj):
    return HttpResponse(status, json.dumps(obj).encode())


class TestAVCache(unittest.TestCase):
    def test_set_and_get(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "c.sqlite")
            c = AVCache(db_path=path, default_ttl_seconds=3600)
            c.set("k", [1, 2, 3])
            self.assertEqual(c.get("k"), [1, 2, 3])

    def test_missing_key_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            c = AVCache(db_path=os.path.join(td, "c.sqlite"))
            self.assertIsNone(c.get("absent"))

    def test_ttl_expired_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            c = AVCache(db_path=os.path.join(td, "c.sqlite"),
                        default_ttl_seconds=0.01)
            c.set("k", {"v": 1})
            import time
            time.sleep(0.05)
            self.assertIsNone(c.get("k"))

    def test_date_in_payload_round_trips_as_iso_string(self):
        with tempfile.TemporaryDirectory() as td:
            c = AVCache(db_path=os.path.join(td, "c.sqlite"))
            c.set("k", [(date(2026, 10, 1), 51.5)])
            got = c.get("k")
            # Stored as JSON: tuple → list, date → ISO str
            self.assertEqual(got, [["2026-10-01", 51.5]])

    def test_make_cache_key_deterministic(self):
        k1 = make_cache_key("TSLA", "RSI", time_period=14, interval="daily")
        k2 = make_cache_key("TSLA", "RSI", interval="daily", time_period=14)
        self.assertEqual(k1, k2)

    def test_make_cache_key_differs_by_params(self):
        k1 = make_cache_key("TSLA", "RSI", time_period=14)
        k2 = make_cache_key("TSLA", "RSI", time_period=20)
        self.assertNotEqual(k1, k2)


class TestCachedAlphaVantageSource(unittest.TestCase):
    def test_rsi_hits_cache_on_second_call(self):
        body = {"Technical Analysis: RSI": {
            "2026-10-01": {"RSI": "51.5"},
        }}
        t = _StubTransport([_resp(200, body)])
        with tempfile.TemporaryDirectory() as td:
            cache = AVCache(db_path=os.path.join(td, "c.sqlite"))
            av = AlphaVantageSource(api_key="k", transport=t, cache=cache)
            got1 = av.rsi("TSLA")
            got2 = av.rsi("TSLA")
            self.assertEqual(got1, got2)
            self.assertEqual(got1, [(date(2026, 10, 1), 51.5)])
            # ONLY ONE real HTTP call — the second came from cache
            self.assertEqual(t.calls, 1)

    def test_macd_hits_cache_on_second_call(self):
        body = {"Technical Analysis: MACD": {
            "2026-10-01": {"MACD": "1.2", "MACD_Signal": "1.0", "MACD_Hist": "0.2"},
        }}
        t = _StubTransport([_resp(200, body)])
        with tempfile.TemporaryDirectory() as td:
            cache = AVCache(db_path=os.path.join(td, "c.sqlite"))
            av = AlphaVantageSource(api_key="k", transport=t, cache=cache)
            av.macd("TSLA")
            av.macd("TSLA")
            self.assertEqual(t.calls, 1)

    def test_cache_miss_still_makes_api_call(self):
        body = {"Technical Analysis: RSI": {
            "2026-10-01": {"RSI": "70.0"},
        }}
        t = _StubTransport([_resp(200, body)])
        av = AlphaVantageSource(api_key="k", transport=t)  # no cache
        av.rsi("TSLA")
        self.assertEqual(t.calls, 1)

    def test_empty_response_is_not_cached(self):
        """A rate-limited (empty-note) response must NOT poison the cache
        with an empty result the user then reads for 6 hours."""
        rate_limit = {"Note": "Thank you for using Alpha Vantage!"}
        then_ok = {"Technical Analysis: RSI": {"2026-10-01": {"RSI": "60.0"}}}
        t = _StubTransport([_resp(200, rate_limit), _resp(200, then_ok)])
        with tempfile.TemporaryDirectory() as td:
            cache = AVCache(db_path=os.path.join(td, "c.sqlite"))
            av = AlphaVantageSource(api_key="k", transport=t, cache=cache)
            self.assertEqual(av.rsi("TSLA"), [])
            # Second call must re-try (not read empty from cache)
            got = av.rsi("TSLA")
            self.assertEqual(got, [(date(2026, 10, 1), 60.0)])
            self.assertEqual(t.calls, 2)

    def test_different_symbols_are_cached_separately(self):
        body_a = {"Technical Analysis: RSI": {"2026-10-01": {"RSI": "10"}}}
        body_b = {"Technical Analysis: RSI": {"2026-10-01": {"RSI": "90"}}}
        t = _StubTransport([_resp(200, body_a), _resp(200, body_b)])
        with tempfile.TemporaryDirectory() as td:
            cache = AVCache(db_path=os.path.join(td, "c.sqlite"))
            av = AlphaVantageSource(api_key="k", transport=t, cache=cache)
            self.assertEqual(av.rsi("A")[0][1], 10.0)
            self.assertEqual(av.rsi("B")[0][1], 90.0)
            # Both cached now
            self.assertEqual(av.rsi("A")[0][1], 10.0)
            self.assertEqual(av.rsi("B")[0][1], 90.0)
            self.assertEqual(t.calls, 2)


if __name__ == "__main__":
    unittest.main()
