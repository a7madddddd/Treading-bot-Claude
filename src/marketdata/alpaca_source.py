"""Concrete Alpaca implementation of the market-data-neutral
MarketDataSource contract defined in src/marketdata/source.py.

Interface neutrality: this file is the ONLY place in the project that
knows Alpaca market-data URLs, headers, and JSON field names. The
Engine and everything above it stay strictly on the neutral
MarketDataSource contract; nothing outside this file should import an
Alpaca-specific symbol or read an Alpaca-specific field name.

Separate base URL from the broker: Alpaca serves market data on a
different domain than paper trading. This file uses `data.alpaca.markets`
by default; it must never be pointed at `paper-api.alpaca.markets` or at
any other broker endpoint. The URL is normalized so a value with or
without a trailing '/v2' works -- the same operational foot-gun that
motivated the broker's normalization.

Feed choice: Alpaca offers multiple data feeds (IEX is free; SIP
requires a paid subscription). The default is `iex` to stay on the
free tier appropriate for paper verification; a caller may override
via the `feed` constructor argument once the Controller approves a
paid tier in a future decision.

Never fabricate a price: every failure path raises
MarketDataUnavailableError. The abstract's docstring is explicit that
a missing price must NEVER be silently coerced to a stale, cached, or
default value.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from marketdata.source import MarketDataSource, MarketDataUnavailableError


HttpResponse = Tuple[int, bytes]
HttpTransport = Callable[..., HttpResponse]


@dataclass(frozen=True)
class _AlpacaDataConfig:
    base_url: str
    key_id: str
    secret_key: str
    feed: str
    timeout_seconds: float


class AlpacaMarketDataSource(MarketDataSource):
    def __init__(
        self,
        *,
        key_id: str,
        secret_key: str,
        base_url: str = "https://data.alpaca.markets",
        feed: str = "iex",
        timeout_seconds: float = 5.0,
        http_transport: Optional[HttpTransport] = None,
    ):
        if not base_url:
            raise ValueError("base_url must be a non-empty URL")
        if not key_id or not secret_key:
            raise ValueError("key_id and secret_key are required")
        if not feed:
            raise ValueError("feed must be a non-empty string")
        normalized = base_url.rstrip("/")
        if normalized.endswith("/v2"):
            normalized = normalized[: -len("/v2")]
        self._config = _AlpacaDataConfig(
            base_url=normalized,
            key_id=key_id,
            secret_key=secret_key,
            feed=feed,
            timeout_seconds=timeout_seconds,
        )
        self._http = http_transport or self._default_transport

    # ---- MarketDataSource contract ------------------------------------

    def get_last_trade(self, symbol: str) -> float:
        _validate_symbol(symbol)
        path = "/v2/stocks/{sym}/trades/latest?{qs}".format(
            sym=urllib.parse.quote(symbol, safe=""),
            qs=urllib.parse.urlencode({"feed": self._config.feed}),
        )
        try:
            status, payload = self._request("GET", path)
        except _AmbiguousTransport as ex:
            raise MarketDataUnavailableError(
                f"network error while fetching last trade for {symbol!r}: {ex}"
            ) from ex

        if status == 404:
            raise MarketDataUnavailableError(
                f"symbol {symbol!r} not found by data provider (HTTP 404)"
            )
        if not (200 <= status < 300):
            raise MarketDataUnavailableError(
                f"data provider returned HTTP {status} for {symbol!r}: "
                f"{_error_snippet(payload)}"
            )

        try:
            obj = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as ex:
            raise MarketDataUnavailableError(
                f"data provider payload for {symbol!r} was not valid JSON"
            ) from ex

        if not isinstance(obj, dict):
            raise MarketDataUnavailableError(
                f"data provider payload for {symbol!r} was not a JSON object"
            )

        trade = obj.get("trade")
        if not isinstance(trade, dict):
            raise MarketDataUnavailableError(
                f"data provider payload for {symbol!r} is missing the 'trade' object"
            )

        raw_price = trade.get("p")
        if raw_price is None:
            raise MarketDataUnavailableError(
                f"data provider payload for {symbol!r} is missing 'trade.p' (price)"
            )
        try:
            price = float(raw_price)
        except (TypeError, ValueError) as ex:
            raise MarketDataUnavailableError(
                f"data provider payload for {symbol!r} 'trade.p' was not numeric: {raw_price!r}"
            ) from ex

        if not (price > 0):
            raise MarketDataUnavailableError(
                f"data provider returned a non-positive price for {symbol!r}: {price}"
            )
        return price

    # ---- transport internals ------------------------------------------

    _AMBIGUOUS_NETWORK_EXCEPTIONS = (
        urllib.error.URLError,
        socket.timeout,
        ConnectionError,
        TimeoutError,
    )

    def _request(self, method: str, path: str) -> HttpResponse:
        url = self._config.base_url + path
        headers = {
            "APCA-API-KEY-ID": self._config.key_id,
            "APCA-API-SECRET-KEY": self._config.secret_key,
            "Accept": "application/json",
        }
        try:
            return self._http(
                method=method,
                url=url,
                headers=headers,
                body=None,
                timeout=self._config.timeout_seconds,
            )
        except self._AMBIGUOUS_NETWORK_EXCEPTIONS as ex:
            raise _AmbiguousTransport(str(ex)) from ex

    @staticmethod
    def _default_transport(
        *,
        method: str,
        url: str,
        headers: dict,
        body: Optional[bytes],
        timeout: float,
    ) -> HttpResponse:
        request = urllib.request.Request(url=url, method=method, data=body, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.getcode(), response.read()
        except urllib.error.HTTPError as http_err:
            try:
                payload = http_err.read()
            except Exception:  # noqa: BLE001 -- diagnostic path
                payload = b""
            return http_err.code, payload


# ---- private helpers --------------------------------------------------


class _AmbiguousTransport(Exception):
    """Internal marker for transport-level ambiguity, translated by
    get_last_trade() into MarketDataUnavailableError."""


def _error_snippet(payload: bytes) -> str:
    try:
        return payload.decode("utf-8", errors="replace")[:200]
    except Exception:  # noqa: BLE001
        return "<unreadable body>"


def _validate_symbol(symbol: str) -> None:
    if not isinstance(symbol, str) or not symbol or not symbol.isupper():
        raise ValueError(f"symbol must be a non-empty uppercase string, got {symbol!r}")
