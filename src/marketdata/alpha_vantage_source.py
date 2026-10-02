"""Alpha Vantage REST client for D-0050 Phase 4 (technical indicators).

Standalone, fail-open HTTP client. NOT wired into the D-0026 ranking
pipeline (same reason as FinnhubSource: ranking composition still
PROPOSED, not approved).

API (minimal):
  - rsi(symbol, interval='daily', time_period=14, series_type='close')
      -> list[(date, value)]
  - macd(symbol, interval='daily', series_type='close')
      -> list[(date, {'macd': float, 'signal': float, 'hist': float})]
  - bbands(symbol, interval='daily', time_period=20)
      -> list[(date, {'upper': float, 'middle': float, 'lower': float})]

Alpha Vantage returns {indicator-specific-key: {date-string: {...}}}.
We normalize to (date, value) sequences, dropping any unparseable row.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from typing import Callable, List, Mapping, Optional, Tuple, Union


_DEFAULT_BASE_URL = "https://www.alphavantage.co/query"
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


class AlphaVantageConfigError(Exception):
    pass


def _parse_date(s: str) -> Optional[date]:
    try:
        return date.fromisoformat(s)
    except ValueError:
        try:
            return datetime.fromisoformat(s.replace(" ", "T")).date()
        except ValueError:
            return None


class AlphaVantageSource:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = _DEFAULT_BASE_URL,
        timeout_seconds: float = _DEFAULT_TIMEOUT,
        transport: HttpTransport = _urllib_transport,
    ) -> None:
        if not api_key:
            raise AlphaVantageConfigError("api_key is required")
        self._api_key = api_key
        self._base = base_url
        self._timeout = timeout_seconds
        self._transport = transport

    def _get(self, params: dict) -> Optional[dict]:
        q = dict(params)
        q["apikey"] = self._api_key
        url = self._base + "?" + urllib.parse.urlencode(q)
        try:
            resp = self._transport(url, {"Accept": "application/json"},
                                   self._timeout)
        except Exception:  # noqa: BLE001
            return None
        if resp.status != 200:
            return None
        try:
            data = json.loads(resp.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(data, dict) or "Error Message" in data or "Note" in data:
            # "Note" means rate limited; treat as outage.
            return None
        return data

    def rsi(self, symbol: str, *, interval: str = "daily",
            time_period: int = 14,
            series_type: str = "close") -> List[Tuple[date, float]]:
        data = self._get({
            "function": "RSI", "symbol": symbol, "interval": interval,
            "time_period": time_period, "series_type": series_type,
        })
        if data is None:
            return []
        series = data.get("Technical Analysis: RSI")
        if not isinstance(series, dict):
            return []
        out: List[Tuple[date, float]] = []
        for k, v in series.items():
            d = _parse_date(k)
            if d is None or not isinstance(v, dict):
                continue
            try:
                out.append((d, float(v.get("RSI"))))
            except (TypeError, ValueError):
                continue
        return sorted(out)

    def macd(self, symbol: str, *, interval: str = "daily",
             series_type: str = "close") -> List[Tuple[date, dict]]:
        data = self._get({
            "function": "MACD", "symbol": symbol,
            "interval": interval, "series_type": series_type,
        })
        if data is None:
            return []
        series = data.get("Technical Analysis: MACD")
        if not isinstance(series, dict):
            return []
        out: List[Tuple[date, dict]] = []
        for k, v in series.items():
            d = _parse_date(k)
            if d is None or not isinstance(v, dict):
                continue
            try:
                out.append((d, {
                    "macd": float(v.get("MACD")),
                    "signal": float(v.get("MACD_Signal")),
                    "hist": float(v.get("MACD_Hist")),
                }))
            except (TypeError, ValueError):
                continue
        return sorted(out, key=lambda t: t[0])

    def bbands(self, symbol: str, *, interval: str = "daily",
               time_period: int = 20,
               series_type: str = "close") -> List[Tuple[date, dict]]:
        data = self._get({
            "function": "BBANDS", "symbol": symbol,
            "interval": interval, "time_period": time_period,
            "series_type": series_type,
        })
        if data is None:
            return []
        series = data.get("Technical Analysis: BBANDS")
        if not isinstance(series, dict):
            return []
        out: List[Tuple[date, dict]] = []
        for k, v in series.items():
            d = _parse_date(k)
            if d is None or not isinstance(v, dict):
                continue
            try:
                out.append((d, {
                    "upper": float(v.get("Real Upper Band")),
                    "middle": float(v.get("Real Middle Band")),
                    "lower": float(v.get("Real Lower Band")),
                }))
            except (TypeError, ValueError):
                continue
        return sorted(out, key=lambda t: t[0])

    @classmethod
    def from_env(cls, env_var: str = "ALPHA_VANTAGE_API_KEY",
                 **kwargs) -> Optional["AlphaVantageSource"]:
        import os
        key = os.environ.get(env_var, "").strip()
        if not key:
            return None
        return cls(api_key=key, **kwargs)
