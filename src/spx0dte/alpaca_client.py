"""Alpaca historical options-data client for the SPX 0DTE module (D-0095).

A separate client from `marketdata.alpaca_source.AlpacaMarketDataSource`
on purpose: that class implements the project's neutral
`MarketDataSource` contract (stocks, live last-trade/snapshot) used by
the Engine. This module reads Alpaca's free Basic-tier OPTIONS bars
endpoint (`/v1beta1/options/bars`) plus SPY stock bars for the ORB
signal -- a different data shape (OHLCV bars over a date range, not a
single live quote), used only by this module's own backtest and
candidate-generation path, not by the Engine's live pricing.

SPXW is the correct 0DTE option root -- plain "SPX" returns no data on
Alpaca's options endpoints (verified empirically, D-0095). Alpaca does
not serve SPX/VIX index-level bars at all (only SPY, a tradable
security, returns real stock-bar data) -- callers of
find_nearest_traded_strike supply a SPY:SPX ratio to seed the search,
measured from real data by `ratio_calibration.RatioProvider` rather
than a constant in this file (a hardcoded ratio here drifted +11.82%
within weeks before this was caught -- see D-0095-A's follow-up
decision entry). The ratio is an ESTIMATE used only to narrow a strike
search, never trusted as an absolute conversion.

Stdlib only (urllib), per this project's existing dependency policy,
following the same transport pattern as marketdata.alpaca_source.
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, List, Optional, Tuple

HttpResponse = Tuple[int, bytes]
HttpTransport = Callable[..., HttpResponse]

DATA_BASE_URL = "https://data.alpaca.markets"

_COARSE_STRIKE_RANGE = 1500
_COARSE_STRIKE_STEP = 100
_FINE_STRIKE_RANGE = 100
_FINE_STRIKE_STEP = 5
_STRIKE_SEARCH_WINDOW_MINUTES = 20
_BAR_FETCH_WINDOW_MINUTES = 15


class AlpacaDataError(Exception):
    """Raised on any failure to retrieve usable options/stock bar
    data. Never silently substituted with a fabricated price."""


@dataclass(frozen=True)
class _ClientConfig:
    key_id: str
    secret_key: str
    base_url: str
    timeout_seconds: float


class AlpacaOptionsClient:
    def __init__(
        self,
        *,
        key_id: str,
        secret_key: str,
        base_url: str = DATA_BASE_URL,
        timeout_seconds: float = 30.0,
        http_transport: Optional[HttpTransport] = None,
        max_retries: int = 6,
    ):
        if not key_id or not secret_key:
            raise ValueError("key_id and secret_key are required")
        if max_retries < 1:
            raise ValueError("max_retries must be >= 1")
        self._config = _ClientConfig(
            key_id=key_id, secret_key=secret_key,
            base_url=base_url.rstrip("/"), timeout_seconds=timeout_seconds,
        )
        self._max_retries = max_retries
        self._http = http_transport or self._default_transport

    # ---- public API -----------------------------------------------------

    def get_spy_minute_bars(self, date: str) -> List[dict]:
        """SPY 1-minute bars for `date` (YYYY-MM-DD). Thin wrapper
        around `get_stock_minute_bars` -- kept as its own method
        since SPY is the ORB+VWAP signal's own underlying and every
        existing caller already names it explicitly; new callers that
        need a different symbol (e.g. QQQ/IWM for cross-market
        confirmation measurement) should call `get_stock_minute_bars`
        directly rather than adding more SPY-shaped wrappers."""
        return self.get_stock_minute_bars("SPY", date)

    def get_stock_minute_bars(self, symbol: str, date: str) -> List[dict]:
        """1-minute bars for any stock `symbol` (e.g. "SPY", "QQQ",
        "IWM") on `date` (YYYY-MM-DD), market hours only. Returns an
        empty list on a weekend/holiday (no data), raises
        AlpacaDataError on a genuine fetch failure.

        `feed=iex` is required for RECENT/today's data: Alpaca's
        default feed is SIP, which this account's free Basic tier
        cannot query for data less than 15 minutes old -- same free-
        tier restriction already documented in
        marketdata.alpaca_source (which defaults to "iex" for the
        identical reason). A past, already-completed trading day
        (the backtest's own usage) does not hit this restriction, so
        it went unnoticed until the first live/today call
        (D-0095-A's manual verification, 2026-11-16: HTTP 403
        "subscription does not permit querying recent SIP data")."""
        qs = urllib.parse.urlencode({
            "symbols": symbol, "timeframe": "1Min", "feed": "iex",
            "start": f"{date}T00:00:00Z", "end": f"{date}T23:59:59Z", "limit": 1000,
        })
        body = self._get(f"/v2/stocks/bars?{qs}")
        return body.get("bars", {}).get(symbol, [])

    def get_spy_daily_bars(self, start_date: str, end_date: str) -> List[dict]:
        """SPY 1-day bars between `start_date`/`end_date` (YYYY-MM-DD,
        inclusive). Thin wrapper around `get_stock_daily_bars` -- kept
        for existing callers (`ratio_calibration.py`) that already
        name it explicitly; new callers needing a different symbol
        (e.g. VIXY/VXX for the VIX-proxy measurement) should call
        `get_stock_daily_bars` directly."""
        return self.get_stock_daily_bars("SPY", start_date, end_date)

    def get_stock_daily_bars(self, symbol: str, start_date: str, end_date: str) -> List[dict]:
        """1-day bars for any stock `symbol` between `start_date`/
        `end_date` (YYYY-MM-DD, inclusive). `feed=iex` for the same
        free-tier reason as `get_stock_minute_bars`; a daily bar for
        a day that already closed is not "recent" in Alpaca's sense
        regardless, but specifying it explicitly costs nothing and
        removes any ambiguity for a range that includes today."""
        qs = urllib.parse.urlencode({
            "symbols": symbol, "timeframe": "1Day", "feed": "iex",
            "start": f"{start_date}T00:00:00Z", "end": f"{end_date}T23:59:59Z", "limit": 1000,
        })
        body = self._get(f"/v2/stocks/bars?{qs}")
        return body.get("bars", {}).get(symbol, [])

    def get_option_bars(self, symbol: str, start_iso: str, end_iso: str, limit: int = 200) -> List[dict]:
        """Bars for one OCC option `symbol` (e.g. SPXW261104C06900000)
        between `start_iso`/`end_iso` (ISO timestamps). Last-TRADE
        prices (o/h/l/c/v/n/vw), not a continuous bid/ask quote --
        sparse by nature, many minutes may have no print at all."""
        qs = urllib.parse.urlencode({
            "symbols": symbol, "timeframe": "1Min", "start": start_iso, "end": end_iso, "limit": limit,
        })
        body = self._get(f"/v1beta1/options/bars?{qs}")
        return body.get("bars", {}).get(symbol, [])

    def find_nearest_traded_strike(
        self, date: str, approx_spx_level: float, call_put: str, around_time_iso: str,
    ) -> Optional[Tuple[int, str]]:
        """Two-phase search because the SPY:SPX ratio drifts materially
        over time and a narrow search around a stale ratio estimate
        can miss the true level entirely (D-0095 finding): first a
        wide COARSE scan that doesn't trust the ratio's absolute scale,
        then a FINE scan around whatever coarse hit was found. Returns
        (strike, occ_symbol) for the first strike with any trade print
        in the search window, or None if nothing traded."""
        window_start = (_parse_ts(around_time_iso) - timedelta(minutes=_STRIKE_SEARCH_WINDOW_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")
        window_end = (_parse_ts(around_time_iso) + timedelta(minutes=_STRIKE_SEARCH_WINDOW_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")

        coarse_center = round(approx_spx_level / _COARSE_STRIKE_STEP) * _COARSE_STRIKE_STEP
        coarse_hit = self._scan_strikes(
            date, call_put, coarse_center, _COARSE_STRIKE_RANGE, _COARSE_STRIKE_STEP,
            window_start, window_end,
        )
        if coarse_hit is None:
            return None

        fine_center = coarse_hit[0]
        fine_hit = self._scan_strikes(
            date, call_put, fine_center, _FINE_STRIKE_RANGE, _FINE_STRIKE_STEP,
            window_start, window_end,
        )
        return fine_hit or coarse_hit

    # ---- strike search internals ----------------------------------------

    def _scan_strikes(
        self, date: str, call_put: str, center: int, span: int, step: int,
        window_start: str, window_end: str,
    ) -> Optional[Tuple[int, str]]:
        offsets = sorted(range(-span, span + 1, step), key=abs)
        for offset in offsets:
            strike = center + offset
            if strike <= 0:
                continue
            symbol = _occ_symbol(date, call_put, strike)
            try:
                bars = self.get_option_bars(symbol, window_start, window_end, limit=5)
            except AlpacaDataError:
                continue
            if bars:
                return strike, symbol
        return None

    # ---- transport internals --------------------------------------------

    _TRANSIENT_NETWORK_EXCEPTIONS = (
        urllib.error.URLError, socket.timeout, ConnectionError, TimeoutError,
    )

    def _get(self, path: str) -> dict:
        url = self._config.base_url + path
        headers = {
            "APCA-API-KEY-ID": self._config.key_id,
            "APCA-API-SECRET-KEY": self._config.secret_key,
            "Accept": "application/json",
        }
        last_error: Optional[str] = None
        for attempt in range(1, self._max_retries + 1):
            try:
                status, payload = self._http(
                    method="GET", url=url, headers=headers, body=None,
                    timeout=self._config.timeout_seconds,
                )
            except self._TRANSIENT_NETWORK_EXCEPTIONS as ex:
                last_error = str(ex)
                if attempt < self._max_retries:
                    import time
                    time.sleep(min(2 ** attempt, 20))
                continue

            if 200 <= status < 300:
                try:
                    return json.loads(payload.decode("utf-8"))
                except (ValueError, UnicodeDecodeError) as ex:
                    raise AlpacaDataError(f"non-JSON response from {path}: {ex}") from ex

            if status == 429 or 500 <= status < 600:
                last_error = f"HTTP {status}"
                if attempt < self._max_retries:
                    import time
                    time.sleep(min(2 ** attempt, 20))
                continue

            raise AlpacaDataError(f"HTTP {status} from {path}: {_snippet(payload)}")

        raise AlpacaDataError(f"exhausted {self._max_retries} retries for {path}: {last_error}")

    @staticmethod
    def _default_transport(*, method: str, url: str, headers: dict, body, timeout: float) -> HttpResponse:
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


def _occ_symbol(date: str, call_put: str, strike: int) -> str:
    """OCC option symbol: {ROOT}{YYMMDD}{C|P}{strike*1000, 8 digits}."""
    y, m, d = date.split("-")
    yymmdd = y[2:] + m + d
    strike_field = f"{strike * 1000:08d}"
    return f"SPXW{yymmdd}{call_put}{strike_field}"


def _snippet(payload: bytes, limit: int = 200) -> str:
    try:
        text = payload.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return "<unreadable payload>"
    return text[:limit]


def _parse_ts(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))
