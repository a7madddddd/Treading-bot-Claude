"""FRED economic data source (D-0050 Phase 1).

Reads FRED (Federal Reserve Economic Data) series for regime
detection. Specifically:
  - VIXCLS: VIX close (daily)
  - DFF: Federal Funds Effective Rate (daily)
  - T10Y2Y: 10Y - 2Y Treasury spread (daily)

Design:
  - stdlib HTTP only (urllib) — matches the rest of the project's
    zero-third-party discipline.
  - Fail-open: any error (network, HTTP non-200, bad JSON, empty
    series) returns an empty list. Caller must handle emptiness and
    fall back to the placeholder regime so the engine never crashes
    on a FRED outage.
  - Transport injectable for testing (same pattern as
    AlpacaFeatureEnricher).
  - The API key never appears in logs, exceptions, or returned data.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from typing import Callable, List, Mapping, Optional, Tuple


_DEFAULT_BASE_URL = "https://api.stlouisfed.org"
_DEFAULT_TIMEOUT_SECONDS = 15.0


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


class FredSourceConfigError(Exception):
    pass


class FredSource:
    """Minimal FRED REST client.

    One method today: ``get_series(series_id, start_date, end_date)``
    returning the list of (date, value) observations within the
    inclusive date range. Non-numeric values (FRED uses "." for
    missing observations) are silently dropped.

    Fail-open: on any error, returns [].
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = _DEFAULT_BASE_URL,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        transport: HttpTransport = _urllib_transport,
        retry_policy=None,  # Optional common.http_retry.RetryPolicy
    ) -> None:
        if not api_key:
            raise FredSourceConfigError("api_key is required")
        self._api_key = api_key
        self._base = base_url.rstrip("/")
        self._timeout = timeout_seconds
        if retry_policy is not None:
            from common.http_retry import with_retry
            transport = with_retry(transport, policy=retry_policy)
        self._transport = transport

    def get_series(
        self,
        series_id: str,
        start_date: date,
        end_date: date,
    ) -> List[Tuple[date, float]]:
        """Fetches daily observations for `series_id` in the inclusive
        range [start_date, end_date]. Returns [] on any failure.

        Never raises. Never prints or logs the api_key.
        """
        params = {
            "series_id": series_id,
            "api_key": self._api_key,
            "file_type": "json",
            "observation_start": start_date.isoformat(),
            "observation_end": end_date.isoformat(),
            "sort_order": "asc",
        }
        url = (
            f"{self._base}/fred/series/observations?"
            + urllib.parse.urlencode(params)
        )
        try:
            resp = self._transport(url, {"Accept": "application/json"},
                                   self._timeout)
        except Exception:  # noqa: BLE001 -- fail-open on any transport error
            return []
        if resp.status != 200:
            return []
        try:
            data = json.loads(resp.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return []
        observations = data.get("observations")
        if not isinstance(observations, list):
            return []

        out: List[Tuple[date, float]] = []
        for obs in observations:
            if not isinstance(obs, dict):
                continue
            raw_date = obs.get("date")
            raw_value = obs.get("value")
            if not isinstance(raw_date, str) or raw_value in (None, ".", ""):
                continue
            try:
                d = date.fromisoformat(raw_date)
                v = float(raw_value)
            except (ValueError, TypeError):
                continue
            out.append((d, v))
        return out

    @classmethod
    def from_env(cls, env_var: str = "FRED_API_KEY",
                 **kwargs) -> Optional["FredSource"]:
        """Convenience factory. Returns None if the env var is unset
        or empty (fail-open at construction so the caller can skip
        regime enrichment cleanly)."""
        import os
        key = os.environ.get(env_var, "").strip()
        if not key:
            return None
        return cls(api_key=key, **kwargs)
