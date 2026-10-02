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
        cache=None,
    ) -> None:
        """D-0050 Phase 11: optional AVCache dodges AV's 5-req/min
        free-tier limit. Daily indicators (RSI/MACD/BBANDS/SMA) only
        change once per trading day, so a 6h TTL is both safe and
        massively reduces real API calls when the ranker runs
        repeatedly against the same symbols."""
        if not api_key:
            raise AlphaVantageConfigError("api_key is required")
        self._api_key = api_key
        self._base = base_url
        self._timeout = timeout_seconds
        self._transport = transport
        self._cache = cache  # AVCache or None

    def _materialize_cached(self, cached) -> list:
        """Converts cached JSON back to the list[(date, value)] shape
        the rest of the code expects. Keeps string keys as-is for
        dict values (MACD/BBANDS)."""
        from datetime import date as _date
        out = []
        if not isinstance(cached, list):
            return []
        for item in cached:
            if not (isinstance(item, (list, tuple)) and len(item) == 2):
                continue
            d_raw, v = item
            try:
                d = _date.fromisoformat(d_raw) if isinstance(d_raw, str) else d_raw
            except ValueError:
                continue
            out.append((d, v))
        return out

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
        if self._cache is not None:
            from marketdata.av_cache import make_cache_key
            ck = make_cache_key(symbol, "RSI", interval=interval,
                                 time_period=time_period,
                                 series_type=series_type)
            cached = self._cache.get(ck)
            if cached is not None:
                return self._materialize_cached(cached)
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
        out = sorted(out)
        if self._cache is not None and out:
            self._cache.set(ck, out)
        return out

    def macd(self, symbol: str, *, interval: str = "daily",
             series_type: str = "close") -> List[Tuple[date, dict]]:
        if self._cache is not None:
            from marketdata.av_cache import make_cache_key
            ck = make_cache_key(symbol, "MACD", interval=interval,
                                 series_type=series_type)
            cached = self._cache.get(ck)
            if cached is not None:
                return self._materialize_cached(cached)
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
        out = sorted(out, key=lambda t: t[0])
        if self._cache is not None and out:
            self._cache.set(ck, out)
        return out

    def bbands(self, symbol: str, *, interval: str = "daily",
               time_period: int = 20,
               series_type: str = "close") -> List[Tuple[date, dict]]:
        if self._cache is not None:
            from marketdata.av_cache import make_cache_key
            ck = make_cache_key(symbol, "BBANDS", interval=interval,
                                 time_period=time_period,
                                 series_type=series_type)
            cached = self._cache.get(ck)
            if cached is not None:
                return self._materialize_cached(cached)
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
        out = sorted(out, key=lambda t: t[0])
        if self._cache is not None and out:
            self._cache.set(ck, out)
        return out

    def sma(self, symbol: str, *, time_period: int = 50,
            interval: str = "daily",
            series_type: str = "close") -> List[Tuple[date, float]]:
        if self._cache is not None:
            from marketdata.av_cache import make_cache_key
            ck = make_cache_key(symbol, "SMA", interval=interval,
                                 time_period=time_period,
                                 series_type=series_type)
            cached = self._cache.get(ck)
            if cached is not None:
                return self._materialize_cached(cached)
        data = self._get({
            "function": "SMA", "symbol": symbol, "interval": interval,
            "time_period": time_period, "series_type": series_type,
        })
        if data is None:
            return []
        series = data.get("Technical Analysis: SMA")
        if not isinstance(series, dict):
            return []
        out: List[Tuple[date, float]] = []
        for k, v in series.items():
            d = _parse_date(k)
            if d is None or not isinstance(v, dict):
                continue
            try:
                out.append((d, float(v.get("SMA"))))
            except (TypeError, ValueError):
                continue
        out = sorted(out)
        if self._cache is not None and out:
            self._cache.set(ck, out)
        return out

    @classmethod
    def from_env(cls, env_var: str = "ALPHA_VANTAGE_API_KEY",
                 *, with_cache: bool = True,
                 cache_path: str = "./av_cache.sqlite",
                 cache_ttl_seconds: float = 6 * 3600.0,
                 **kwargs) -> Optional["AlphaVantageSource"]:
        """Factory. On free tier the shared SQLite TTL cache is on by
        default (daily indicators change once per day, so a 6h TTL is
        safe and avoids the 5-req/min rate limit when ranking multiple
        symbols). Pass with_cache=False to disable."""
        import os
        key = os.environ.get(env_var, "").strip()
        if not key:
            return None
        cache = None
        if with_cache:
            try:
                from marketdata.av_cache import AVCache
                cache = AVCache(db_path=cache_path,
                                default_ttl_seconds=cache_ttl_seconds)
            except Exception:  # noqa: BLE001
                cache = None
        return cls(api_key=key, cache=cache, **kwargs)
