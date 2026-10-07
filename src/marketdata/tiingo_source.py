"""Tiingo REST client for D-0050 Phase 5 (EOD prices + news).

Standalone, fail-open HTTP client, same contract as FredSource /
FinnhubSource / AlphaVantageSource:
  - stdlib urllib only
  - never raises; returns [] / None on any failure
  - api key never leaked in logs or returned data
  - transport injectable for tests

API surface (minimal):
  - get_eod_prices(symbol, start_date, end_date)
      → list[(date, dict)] with keys open/high/low/close/volume/adjClose
  - get_news(symbols, limit=50)
      → list[dict] with keys title/url/publishedDate/source
  - get_metadata(symbol)
      → dict | None (ticker, name, exchange, startDate, endDate)

NOT wired into the D-0026 ranking pipeline yet (ranking composition is
still PROPOSED / not approved).
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from typing import Callable, List, Mapping, Optional, Tuple


_DEFAULT_BASE_URL = "https://api.tiingo.com"
_DEFAULT_TIMEOUT = 15.0


class HttpResponse:
    __slots__ = ("status", "body")

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body


HttpTransport = Callable[[str, Mapping[str, str], float], HttpResponse]


def _urllib_transport(url: str, headers: Mapping[str, str],
                      timeout: float) -> HttpResponse:
    req = urllib.request.Request(url, headers=dict(headers))
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return HttpResponse(r.status, r.read())
    except urllib.error.HTTPError as ex:
        return HttpResponse(ex.code, ex.read() if hasattr(ex, "read") else b"")


class TiingoConfigError(Exception):
    pass


class TiingoHTTPError(RuntimeError):
    """The endpoint answered with a non-200 status.

    D-0084. Raised ONLY by `get_news`, deliberately. Every other method
    keeps its fail-open "return empty" contract, because for prices an
    empty result and a failed result lead to the same safe behaviour --
    the candidate simply has no data and is not proposed.

    News is different: `SymbolResearchHub` recorded a 403 and a quiet
    "this symbol has no headlines" as the identical string "tiingo" in
    `sources_failed`. On 2026-10-06 that hid a PERMANENT 403 (the news
    endpoint is not included in our subscription) behind what looked
    like thin coverage, and it took a hand-written probe to find it.
    Carrying the status makes the difference visible in the recorded
    metrics the next time it happens.
    """

    def __init__(self, status: int) -> None:
        super().__init__(f"tiingo responded with HTTP {status}")
        self.status = status


class TiingoSource:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = _DEFAULT_BASE_URL,
        timeout_seconds: float = _DEFAULT_TIMEOUT,
        transport: HttpTransport = _urllib_transport,
    ) -> None:
        if not api_key:
            raise TiingoConfigError("api_key is required")
        self._api_key = api_key
        self._base = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._transport = transport

    def _headers(self) -> dict:
        # Tiingo supports Authorization: Token <key> OR ?token=.
        # Using header keeps the key out of URLs printed in logs.
        return {
            "Authorization": f"Token {self._api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _get_json(self, path: str, params: Optional[dict] = None):
        url = f"{self._base}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        try:
            resp = self._transport(url, self._headers(), self._timeout)
        except Exception:  # noqa: BLE001
            return None
        if resp.status != 200:
            return None
        try:
            return json.loads(resp.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    def get_eod_prices(
        self, symbol: str, start_date: date, end_date: date,
    ) -> List[Tuple[date, dict]]:
        """Returns a list of (date, OHLCV-dict) tuples sorted
        ascending. Empty list on any failure."""
        data = self._get_json(
            f"/tiingo/daily/{symbol}/prices",
            params={
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
            },
        )
        if not isinstance(data, list):
            return []
        out: List[Tuple[date, dict]] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            raw_date = row.get("date")
            if not isinstance(raw_date, str):
                continue
            try:
                # Tiingo returns ISO 8601 with time (e.g. "2026-01-05T00:00:00.000Z").
                d = date.fromisoformat(raw_date.split("T", 1)[0])
            except ValueError:
                continue
            out.append((d, row))
        return sorted(out, key=lambda t: t[0])

    def get_news(
        self, symbols: List[str], *, limit: int = 50,
    ) -> List[dict]:
        """An empty list means the endpoint answered and had nothing.
        A non-200 raises TiingoHTTPError -- see that class for why this
        one method does not fail open (D-0084)."""
        if not symbols:
            return []
        url = f"{self._base}/tiingo/news?" + urllib.parse.urlencode({
            "tickers": ",".join(symbols),
            "limit": max(1, min(limit, 1000)),
        })
        try:
            resp = self._transport(url, self._headers(), self._timeout)
        except Exception as exc:  # noqa: BLE001
            raise TiingoHTTPError(0) from exc
        if resp.status != 200:
            raise TiingoHTTPError(resp.status)
        try:
            data = json.loads(resp.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return []
        if not isinstance(data, list):
            return []
        return [x for x in data if isinstance(x, dict)]

    def get_metadata(self, symbol: str) -> Optional[dict]:
        data = self._get_json(f"/tiingo/daily/{symbol}")
        if isinstance(data, dict) and data:
            return data
        return None

    @classmethod
    def from_env(cls, env_var: str = "TIINGO_API_KEY",
                 **kwargs) -> Optional["TiingoSource"]:
        import os
        key = os.environ.get(env_var, "").strip()
        if not key:
            return None
        return cls(api_key=key, **kwargs)
