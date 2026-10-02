"""Polygon.io client for D-0050 Phase 6.

Two surfaces here, both standalone and fail-open:

1. **PolygonSource** — REST client over https://api.polygon.io
   - get_aggregates(symbol, multiplier, timespan, from_date, to_date)
   - get_ticker_snapshot(symbol)
   - get_ticker_details(symbol)
   - get_news(symbol, limit=10)
   Same discipline as FredSource / FinnhubSource: stdlib urllib,
   returns [] / None on any failure, api_key never leaked in logs.

2. **PolygonS3Config** — bulk flat-files via Polygon's S3-compatible
   endpoint (files.polygon.io). The config object only READS the env
   vars and validates them; the actual bulk download is deferred to a
   future worker that uses boto3 (or hand-rolled SigV4 if we decide to
   stay stdlib-only). This keeps the env-var surface complete today
   while being honest that the download path is not implemented yet.

Both are NOT wired into the D-0026 ranking pipeline — ranking
composition is still PROPOSED / not approved.
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from typing import Callable, List, Mapping, Optional, Tuple


_DEFAULT_BASE_URL = "https://api.polygon.io"
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


class PolygonConfigError(Exception):
    pass


# ---------------------------------------------------------------------------
# REST client
# ---------------------------------------------------------------------------

class PolygonSource:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = _DEFAULT_BASE_URL,
        timeout_seconds: float = _DEFAULT_TIMEOUT,
        transport: HttpTransport = _urllib_transport,
    ) -> None:
        if not api_key:
            raise PolygonConfigError("api_key is required")
        self._api_key = api_key
        self._base = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._transport = transport

    def _get_json(self, path: str, params: Optional[dict] = None):
        """Polygon accepts the API key as a header (`Authorization: Bearer`)
        OR a query param (`apiKey`). Header is strongly preferred because
        it keeps the key out of any log that captures URLs."""
        url = f"{self._base}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
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
            data = json.loads(resp.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        # Polygon returns {"status": "OK"|"ERROR", ...}
        if isinstance(data, dict) and data.get("status") == "ERROR":
            return None
        return data

    def get_aggregates(
        self,
        symbol: str,
        multiplier: int,
        timespan: str,       # "day", "hour", "minute", "week"
        from_date: date,
        to_date: date,
        *,
        adjusted: bool = True,
        limit: int = 5000,
    ) -> List[Tuple[date, dict]]:
        """OHLCV bars. Returns a list of (bar-start-date, bar-dict) sorted
        ascending. Empty on any failure."""
        path = (
            f"/v2/aggs/ticker/{symbol}/range/{multiplier}/{timespan}"
            f"/{from_date.isoformat()}/{to_date.isoformat()}"
        )
        data = self._get_json(path, params={
            "adjusted": "true" if adjusted else "false",
            "sort": "asc",
            "limit": max(1, min(limit, 50000)),
        })
        if not isinstance(data, dict):
            return []
        results = data.get("results")
        if not isinstance(results, list):
            return []
        from datetime import datetime, timezone
        out: List[Tuple[date, dict]] = []
        for row in results:
            if not isinstance(row, dict):
                continue
            ts = row.get("t")  # ms since epoch (bar START)
            if not isinstance(ts, (int, float)):
                continue
            try:
                d = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date()
            except (OSError, ValueError):
                continue
            out.append((d, row))
        return out

    def get_ticker_snapshot(self, symbol: str) -> Optional[dict]:
        data = self._get_json(
            f"/v2/snapshot/locale/us/markets/stocks/tickers/{symbol}"
        )
        if isinstance(data, dict):
            ticker = data.get("ticker")
            if isinstance(ticker, dict):
                return ticker
        return None

    def get_ticker_details(self, symbol: str) -> Optional[dict]:
        data = self._get_json(f"/v3/reference/tickers/{symbol}")
        if isinstance(data, dict):
            results = data.get("results")
            if isinstance(results, dict):
                return results
        return None

    def get_news(self, symbol: str, *, limit: int = 10) -> List[dict]:
        data = self._get_json("/v2/reference/news", params={
            "ticker": symbol,
            "limit": max(1, min(limit, 1000)),
            "order": "desc",
        })
        if not isinstance(data, dict):
            return []
        results = data.get("results")
        if not isinstance(results, list):
            return []
        return [x for x in results if isinstance(x, dict)]

    @classmethod
    def from_env(cls, env_var: str = "POLYGON_API_KEY",
                 **kwargs) -> Optional["PolygonSource"]:
        key = os.environ.get(env_var, "").strip()
        if not key:
            return None
        return cls(api_key=key, **kwargs)


# ---------------------------------------------------------------------------
# S3 bulk-flat-files config
# ---------------------------------------------------------------------------

class PolygonS3Config:
    """Config object for Polygon's S3-compatible bulk-data endpoint.

    This class READS and VALIDATES the env vars so the runner can log
    "Polygon bulk access configured" at startup. The actual S3 download
    is NOT implemented in this class — adding boto3 (or hand-rolled
    SigV4) is a separate, future work item (see D-0050 phase 6b TODO).

    Env vars:
      POLYGON_S3_ENDPOINT        e.g. https://files.polygon.io
      POLYGON_S3_ACCESS_KEY_ID
      POLYGON_S3_SECRET_KEY
    """

    __slots__ = ("endpoint", "access_key_id", "secret_key")

    def __init__(self, *, endpoint: str, access_key_id: str,
                 secret_key: str) -> None:
        if not endpoint:
            raise PolygonConfigError("endpoint is required")
        if not access_key_id or not secret_key:
            raise PolygonConfigError(
                "both access_key_id and secret_key are required"
            )
        self.endpoint = endpoint.rstrip("/")
        self.access_key_id = access_key_id
        self.secret_key = secret_key

    @classmethod
    def from_env(cls) -> Optional["PolygonS3Config"]:
        """Returns None when any of the three env vars is missing.
        Logs nothing (silent fail-open at construction)."""
        endpoint = os.environ.get("POLYGON_S3_ENDPOINT", "").strip()
        access = os.environ.get("POLYGON_S3_ACCESS_KEY_ID", "").strip()
        secret = os.environ.get("POLYGON_S3_SECRET_KEY", "").strip()
        if not (endpoint and access and secret):
            return None
        try:
            return cls(endpoint=endpoint, access_key_id=access,
                       secret_key=secret)
        except PolygonConfigError:
            return None

    def __repr__(self) -> str:
        # Never print the secret.
        return (f"PolygonS3Config(endpoint={self.endpoint!r}, "
                f"access_key_id='***', secret_key='***')")
