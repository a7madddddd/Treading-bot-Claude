import json
import unittest
from typing import List, Optional, Tuple

from marketdata.alpaca_source import AlpacaMarketDataSource
from marketdata.source import MarketDataUnavailableError


class _RecordedCall:
    def __init__(self, method: str, url: str, headers: dict, body: Optional[bytes]):
        self.method = method
        self.url = url
        self.headers = headers
        self.body = body


class _StubTransport:
    def __init__(self):
        self._queue: List = []
        self.calls: List[_RecordedCall] = []

    def queue(self, response: Tuple[int, bytes]) -> None:
        self._queue.append(response)

    def queue_exception(self, exc: BaseException) -> None:
        self._queue.append(exc)

    def __call__(self, *, method: str, url: str, headers: dict, body: Optional[bytes], timeout: float):
        self.calls.append(_RecordedCall(method, url, headers, body))
        if not self._queue:
            raise AssertionError(f"unexpected transport call: {method} {url}")
        nxt = self._queue.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt


def _source(transport: _StubTransport, *, feed: str = "iex") -> AlpacaMarketDataSource:
    return AlpacaMarketDataSource(
        key_id="TEST_KEY",
        secret_key="TEST_SECRET",
        base_url="https://data.alpaca.markets",
        feed=feed,
        http_transport=transport,
    )


def _trade_payload(price=175.25) -> bytes:
    return json.dumps({
        "symbol": "AAPL",
        "trade": {
            "t": "2026-09-23T12:00:00Z",
            "x": "V",
            "p": price,
            "s": 100,
        },
    }).encode("utf-8")


class TestConstruction(unittest.TestCase):
    def test_rejects_empty_credentials(self):
        with self.assertRaises(ValueError):
            AlpacaMarketDataSource(key_id="", secret_key="s")
        with self.assertRaises(ValueError):
            AlpacaMarketDataSource(key_id="k", secret_key="")

    def test_rejects_empty_base_url(self):
        with self.assertRaises(ValueError):
            AlpacaMarketDataSource(key_id="k", secret_key="s", base_url="")

    def test_rejects_empty_feed(self):
        with self.assertRaises(ValueError):
            AlpacaMarketDataSource(key_id="k", secret_key="s", feed="")

    def test_strips_trailing_slash_and_v2(self):
        for base in (
            "https://data.alpaca.markets",
            "https://data.alpaca.markets/",
            "https://data.alpaca.markets/v2",
            "https://data.alpaca.markets/v2/",
        ):
            transport = _StubTransport()
            transport.queue((200, _trade_payload(175.0)))
            source = AlpacaMarketDataSource(
                key_id="k", secret_key="s",
                base_url=base, http_transport=transport,
            )
            source.get_last_trade("AAPL")
            self.assertTrue(
                transport.calls[0].url.startswith(
                    "https://data.alpaca.markets/v2/stocks/AAPL/trades/latest"
                ),
                msg=f"URL normalization failed for base_url={base!r}: got {transport.calls[0].url!r}",
            )


class TestGetLastTrade(unittest.TestCase):
    def test_happy_path_returns_price(self):
        transport = _StubTransport()
        transport.queue((200, _trade_payload(price=175.25)))
        price = _source(transport).get_last_trade("AAPL")
        self.assertEqual(price, 175.25)

    def test_uses_correct_endpoint_and_headers(self):
        transport = _StubTransport()
        transport.queue((200, _trade_payload()))
        _source(transport).get_last_trade("AAPL")
        call = transport.calls[0]
        self.assertEqual(call.method, "GET")
        self.assertTrue(
            call.url.startswith("https://data.alpaca.markets/v2/stocks/AAPL/trades/latest"),
            msg=f"unexpected URL: {call.url!r}",
        )
        self.assertIn("feed=iex", call.url)
        self.assertEqual(call.headers["APCA-API-KEY-ID"], "TEST_KEY")
        self.assertEqual(call.headers["APCA-API-SECRET-KEY"], "TEST_SECRET")
        self.assertIsNone(call.body)

    def test_feed_param_reflects_config(self):
        transport = _StubTransport()
        transport.queue((200, _trade_payload()))
        _source(transport, feed="sip").get_last_trade("AAPL")
        self.assertIn("feed=sip", transport.calls[0].url)

    def test_404_raises_unavailable(self):
        transport = _StubTransport()
        transport.queue((404, b'{"message":"symbol not found"}'))
        with self.assertRaises(MarketDataUnavailableError):
            _source(transport).get_last_trade("ZZZZ")

    def test_5xx_raises_unavailable(self):
        transport = _StubTransport()
        transport.queue((503, b'{"message":"service unavailable"}'))
        with self.assertRaises(MarketDataUnavailableError):
            _source(transport).get_last_trade("AAPL")

    def test_timeout_raises_unavailable(self):
        transport = _StubTransport()
        transport.queue_exception(TimeoutError("simulated timeout"))
        with self.assertRaises(MarketDataUnavailableError):
            _source(transport).get_last_trade("AAPL")

    def test_connection_reset_raises_unavailable(self):
        transport = _StubTransport()
        transport.queue_exception(ConnectionError("connection reset"))
        with self.assertRaises(MarketDataUnavailableError):
            _source(transport).get_last_trade("AAPL")

    def test_malformed_json_raises_unavailable(self):
        transport = _StubTransport()
        transport.queue((200, b"not json"))
        with self.assertRaises(MarketDataUnavailableError):
            _source(transport).get_last_trade("AAPL")

    def test_missing_trade_object_raises_unavailable(self):
        transport = _StubTransport()
        transport.queue((200, b'{"symbol":"AAPL"}'))
        with self.assertRaises(MarketDataUnavailableError):
            _source(transport).get_last_trade("AAPL")

    def test_missing_price_raises_unavailable(self):
        transport = _StubTransport()
        transport.queue((200, json.dumps({"symbol": "AAPL", "trade": {}}).encode("utf-8")))
        with self.assertRaises(MarketDataUnavailableError):
            _source(transport).get_last_trade("AAPL")

    def test_non_numeric_price_raises_unavailable(self):
        transport = _StubTransport()
        transport.queue((200, json.dumps({
            "symbol": "AAPL",
            "trade": {"p": "not-a-number"},
        }).encode("utf-8")))
        with self.assertRaises(MarketDataUnavailableError):
            _source(transport).get_last_trade("AAPL")

    def test_zero_price_raises_unavailable(self):
        transport = _StubTransport()
        transport.queue((200, _trade_payload(price=0.0)))
        with self.assertRaises(MarketDataUnavailableError):
            _source(transport).get_last_trade("AAPL")

    def test_negative_price_raises_unavailable(self):
        transport = _StubTransport()
        transport.queue((200, _trade_payload(price=-10.0)))
        with self.assertRaises(MarketDataUnavailableError):
            _source(transport).get_last_trade("AAPL")

    def test_rejects_lowercase_symbol(self):
        transport = _StubTransport()
        with self.assertRaises(ValueError):
            _source(transport).get_last_trade("aapl")

    def test_rejects_empty_symbol(self):
        transport = _StubTransport()
        with self.assertRaises(ValueError):
            _source(transport).get_last_trade("")


if __name__ == "__main__":
    unittest.main()
