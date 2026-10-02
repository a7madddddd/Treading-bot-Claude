"""Finnhub REST client for D-0050 Phase 3 (fundamentals + news).

Standalone, fail-open HTTP client. NOT wired into the D-0026 ranking
pipeline — the ranking composition is still PROPOSED / not approved
(see docs/trading/universe-selection-analysis.md §7.5). This module
exists so that when the Controller approves a scoring recipe that uses
Finnhub inputs, the integration is a one-line wire-in rather than a
weeks-long rebuild.

API:
  - company_profile(symbol) -> dict | None
  - basic_financials(symbol) -> dict | None  (metrics bundle)
  - company_news(symbol, from_date, to_date) -> list[dict]

Design (same as FredSource):
  - stdlib urllib only
  - fail-open: any error returns None/[]
  - transport injectable for tests
  - api key never leaked in logs or returned data
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from typing import Any, Callable, List, Mapping, Optional


_DEFAULT_BASE_URL = "https://finnhub.io/api/v1"
_DEFAULT_TIMEOUT = 10.0


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


class FinnhubConfigError(Exception):
    pass


class FinnhubSource:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = _DEFAULT_BASE_URL,
        timeout_seconds: float = _DEFAULT_TIMEOUT,
        transport: HttpTransport = _urllib_transport,
    ) -> None:
        if not api_key:
            raise FinnhubConfigError("api_key is required")
        self._api_key = api_key
        self._base = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._transport = transport

    def _get_json(self, path: str, params: dict) -> Any:
        q = dict(params)
        q["token"] = self._api_key
        url = f"{self._base}{path}?" + urllib.parse.urlencode(q)
        try:
            resp = self._transport(url, {"Accept": "application/json"},
                                   self._timeout)
        except Exception:  # noqa: BLE001
            return None
        if resp.status != 200:
            return None
        try:
            return json.loads(resp.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    def company_profile(self, symbol: str) -> Optional[dict]:
        data = self._get_json("/stock/profile2", {"symbol": symbol})
        if isinstance(data, dict) and data:
            return data
        return None

    def basic_financials(self, symbol: str) -> Optional[dict]:
        data = self._get_json("/stock/metric", {"symbol": symbol, "metric": "all"})
        if isinstance(data, dict) and data.get("metric"):
            return data
        return None

    def company_news(self, symbol: str, from_date: date,
                     to_date: date) -> List[dict]:
        data = self._get_json("/company-news", {
            "symbol": symbol,
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
        })
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        return []

    def earnings_calendar(self, symbol: str, from_date: date,
                          to_date: date) -> List[dict]:
        """Upcoming earnings in [from_date, to_date]. Returns [] on failure."""
        data = self._get_json("/calendar/earnings", {
            "symbol": symbol,
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
        })
        if isinstance(data, dict):
            lst = data.get("earningsCalendar")
            if isinstance(lst, list):
                return [x for x in lst if isinstance(x, dict)]
        return []

    def recommendation_trends(self, symbol: str) -> List[dict]:
        """Analyst recommendation trend buckets per month."""
        data = self._get_json("/stock/recommendation", {"symbol": symbol})
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        return []

    def insider_sentiment(self, symbol: str, from_date: date,
                          to_date: date) -> Optional[dict]:
        """Monthly insider-sentiment MSPR / change buckets."""
        data = self._get_json("/stock/insider-sentiment", {
            "symbol": symbol,
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
        })
        if isinstance(data, dict) and data.get("data"):
            return data
        return None

    @classmethod
    def from_env(cls, env_var: str = "FINNHUB_API_KEY",
                 **kwargs) -> Optional["FinnhubSource"]:
        import os
        key = os.environ.get(env_var, "").strip()
        if not key:
            return None
        return cls(api_key=key, **kwargs)
