"""QuiverQuant REST client for Congressional trading data
(D-0050 Phase B.20).

QuiverQuant.com aggregates official Senate (STOCK Act) and House
(eDisclosure) disclosures into a clean JSON API. Faster + more
reliable than scraping third-party summaries.

Endpoints used:
  /beta/live/congresstrading  — recent 7-day trades across all
                                 Congress members
  /beta/historical/congresstrading/{SYMBOL}  — all historical trades
                                                 for a specific ticker

Auth: single Bearer token.

Same fail-open discipline as the other marketdata clients: any
network / HTTP / parse failure returns [] / None; the api_key never
appears in logs or returned data.

Free tier: 150 req/month. We ONLY call /live once per day in the
political universe builder, so a single daily tick stays well under
the cap. Historical lookups are opt-in and cached.
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List, Mapping, Optional


_DEFAULT_BASE_URL = "https://api.quiverquant.com"
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


class QuiverQuantConfigError(Exception):
    pass


class QuiverQuantSource:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = _DEFAULT_BASE_URL,
        timeout_seconds: float = _DEFAULT_TIMEOUT,
        transport: HttpTransport = _urllib_transport,
    ) -> None:
        if not api_key:
            raise QuiverQuantConfigError("api_key is required")
        self._api_key = api_key
        self._base = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._transport = transport

    def _get_json(self, path: str):
        """Returns a parsed JSON object or None on failure."""
        url = f"{self._base}{path}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }
        try:
            resp = self._transport(url, headers, self._timeout)
        except Exception:  # noqa: BLE001
            return None
        if resp.status != 200:
            return None
        try:
            return json.loads(resp.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    def recent_congress_trades(self) -> List[dict]:
        """Returns last 7 days of Congressional trades. Each item:
          {Representative, Transaction, Ticker, Range, TransactionDate,
           Chamber, Party, ReportDate, ...}
        Returns [] on failure.
        """
        data = self._get_json("/beta/live/congresstrading")
        if not isinstance(data, list):
            return []
        return [x for x in data if isinstance(x, dict)]

    def historical_trades(self, ticker: str) -> List[dict]:
        """All historical Congressional trades for one ticker."""
        if not ticker:
            return []
        quoted = urllib.parse.quote(ticker.upper())
        data = self._get_json(f"/beta/historical/congresstrading/{quoted}")
        if not isinstance(data, list):
            return []
        return [x for x in data if isinstance(x, dict)]

    @classmethod
    def from_env(cls, env_var: str = "QUIVER_QUANT_API_KEY",
                 **kwargs) -> Optional["QuiverQuantSource"]:
        key = os.environ.get(env_var, "").strip()
        if not key:
            return None
        return cls(api_key=key, **kwargs)
