import json
import unittest
from unittest.mock import patch

from spx0dte.alpaca_client import AlpacaDataError, AlpacaOptionsClient, _occ_symbol


class FakeTransport:
    """Injectable stand-in for AlpacaOptionsClient's http transport.
    `responses` is a list of (status, body_dict_or_bytes) consumed in
    order per call; `calls` records every (method, url) for assertions."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, *, method, url, headers, body, timeout):
        self.calls.append((method, url))
        status, payload = self.responses.pop(0)
        if isinstance(payload, dict):
            payload = json.dumps(payload).encode("utf-8")
        return status, payload


class TestOccSymbol(unittest.TestCase):
    def test_format(self):
        self.assertEqual(_occ_symbol("2026-11-04", "C", 6900), "SPXW261104C06900000")

    def test_put(self):
        self.assertEqual(_occ_symbol("2026-01-02", "P", 5250), "SPXW260102P05250000")


class TestAlpacaOptionsClientGet(unittest.TestCase):
    def test_requires_credentials(self):
        with self.assertRaises(ValueError):
            AlpacaOptionsClient(key_id="", secret_key="x")

    def test_successful_spy_bars(self):
        transport = FakeTransport([(200, {"bars": {"SPY": [{"t": "2026-01-02T14:30:00Z", "c": 500.0}]}})])
        client = AlpacaOptionsClient(key_id="k", secret_key="s", http_transport=transport)
        bars = client.get_spy_minute_bars("2026-01-02")
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["c"], 500.0)

    def test_successful_stock_minute_bars_for_other_symbols(self):
        transport = FakeTransport([(200, {"bars": {"QQQ": [{"t": "2026-01-02T14:30:00Z", "c": 400.0}]}})])
        client = AlpacaOptionsClient(key_id="k", secret_key="s", http_transport=transport)
        bars = client.get_stock_minute_bars("QQQ", "2026-01-02")
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["c"], 400.0)
        self.assertIn("symbols=QQQ", transport.calls[0][1])

    def test_successful_spy_daily_bars(self):
        transport = FakeTransport([(200, {"bars": {"SPY": [
            {"t": "2026-01-02T00:00:00Z", "c": 500.0},
            {"t": "2026-01-05T00:00:00Z", "c": 505.0},
        ]}})])
        client = AlpacaOptionsClient(key_id="k", secret_key="s", http_transport=transport)
        bars = client.get_spy_daily_bars("2026-01-01", "2026-01-06")
        self.assertEqual(len(bars), 2)
        self.assertIn("feed=iex", transport.calls[0][1])
        self.assertIn("timeframe=1Day", transport.calls[0][1])

    def test_empty_bars_for_holiday(self):
        transport = FakeTransport([(200, {"bars": {}})])
        client = AlpacaOptionsClient(key_id="k", secret_key="s", http_transport=transport)
        self.assertEqual(client.get_spy_minute_bars("2026-01-01"), [])

    def test_4xx_raises_immediately_no_retry(self):
        transport = FakeTransport([(401, b"unauthorized")])
        client = AlpacaOptionsClient(key_id="k", secret_key="s", http_transport=transport, max_retries=5)
        with self.assertRaises(AlpacaDataError):
            client.get_spy_minute_bars("2026-01-02")
        self.assertEqual(len(transport.calls), 1)  # no retry on a non-retryable status

    @patch("time.sleep")
    def test_retries_then_succeeds_on_5xx(self, mock_sleep):
        transport = FakeTransport([
            (503, b"unavailable"),
            (200, {"bars": {"SPY": []}}),
        ])
        client = AlpacaOptionsClient(key_id="k", secret_key="s", http_transport=transport, max_retries=3)
        bars = client.get_spy_minute_bars("2026-01-02")
        self.assertEqual(bars, [])
        self.assertEqual(len(transport.calls), 2)
        mock_sleep.assert_called_once()

    @patch("time.sleep")
    def test_exhausts_retries_and_raises(self, mock_sleep):
        transport = FakeTransport([(500, b"e1"), (500, b"e2")])
        client = AlpacaOptionsClient(key_id="k", secret_key="s", http_transport=transport, max_retries=2)
        with self.assertRaises(AlpacaDataError):
            client.get_option_bars("SPXW261104C06900000", "2026-11-04T00:00:00Z", "2026-11-04T23:59:59Z")


class TestFindNearestTradedStrike(unittest.TestCase):
    def test_finds_a_strike_with_trades_on_coarse_pass(self):
        # Every call returns empty EXCEPT the one matching the coarse
        # center exactly (approx_spx_level rounds to a 100-step center).
        def transport(*, method, url, headers, body, timeout):
            if "SPXW261104C06900000" in url:
                return 200, json.dumps({"bars": {"SPXW261104C06900000": [{"t": "x", "c": 1.0}]}}).encode()
            return 200, json.dumps({"bars": {}}).encode()

        client = AlpacaOptionsClient(key_id="k", secret_key="s", http_transport=transport)
        result = client.find_nearest_traded_strike(
            "2026-11-04", approx_spx_level=6900.0, call_put="C",
            around_time_iso="2026-11-04T14:45:00Z",
        )
        self.assertIsNotNone(result)
        strike, symbol = result
        self.assertEqual(strike, 6900)
        self.assertEqual(symbol, "SPXW261104C06900000")

    def test_returns_none_when_nothing_traded(self):
        transport = lambda **kw: (200, json.dumps({"bars": {}}).encode())
        client = AlpacaOptionsClient(key_id="k", secret_key="s", http_transport=transport)
        result = client.find_nearest_traded_strike(
            "2026-11-04", approx_spx_level=6900.0, call_put="C",
            around_time_iso="2026-11-04T14:45:00Z",
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
