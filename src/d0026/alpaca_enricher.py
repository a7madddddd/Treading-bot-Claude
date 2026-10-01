"""Concrete FeatureEnricher backed by Alpaca daily bars (B24).

Reads the last N daily bars per symbol (default 45) and computes
the four D-0026 measures the stages depend on:

  liquidity_measure           = mean(volume * vwap) over last 20 bars
                                (dollar volume)
  atr_measure                 = mean True Range over last 14 bars
  momentum_measure            = (close_today - close_30bars_ago)
                                / close_30bars_ago
  execution_quality_proxy     = mean((high - low) / vwap) over last 20 bars
                                (daily range as a spread proxy;
                                free-tier IEX feed has no true
                                bid/ask, so this is documented
                                proxy)

`warm_up_sufficient` requires at least `min_bars` (default 30) bars.
Symbols with insufficient history return an unenriched candidate
(bar=None, features=None) so Stage B rejects them cleanly.

HTTP transport is injectable. Never mutates candidates in place
(they are frozen dataclasses).
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, List, Mapping, Optional, Tuple

from d0026.models import (
    AdjustmentConvention, DailySecurityFeatures, MarketDataBar,
    RegimeState, UniverseCandidate,
)


_DEFAULT_DATA_BASE_URL = "https://data.alpaca.markets"
_DEFAULT_TIMEOUT_SECONDS = 15.0
_DEFAULT_HISTORY_DAYS = 65  # ~45 trading bars; must exceed
                            # _MOMENTUM_LOOKBACK+1 (31) so that
                            # `_return_over(30)` has enough history
                            # for every enriched candidate.
_DEFAULT_MIN_BARS = 31
_LIQUIDITY_WINDOW = 20
_ATR_WINDOW = 14
_MOMENTUM_LOOKBACK = 30
_EXEC_QUALITY_WINDOW = 20


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
        return HttpResponse(ex.code, ex.read())


class AlpacaFeatureEnricherConfigError(Exception):
    pass


class AlpacaFeatureEnricher:
    def __init__(
        self,
        *,
        key_id: str,
        secret_key: str,
        data_base_url: str = _DEFAULT_DATA_BASE_URL,
        feed: str = "iex",
        history_days: int = _DEFAULT_HISTORY_DAYS,
        min_bars: int = _DEFAULT_MIN_BARS,
        transport: HttpTransport = _urllib_transport,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        retry_policy=None,  # Optional common.http_retry.RetryPolicy
        sector_provider=None,  # Optional d0026.sector_provider.SectorProvider
    ) -> None:
        if not key_id or not secret_key:
            raise AlpacaFeatureEnricherConfigError(
                "key_id and secret_key are required"
            )
        if history_days < min_bars:
            raise AlpacaFeatureEnricherConfigError(
                f"history_days ({history_days}) must be >= "
                f"min_bars ({min_bars})"
            )
        self._key_id = key_id
        self._secret = secret_key
        self._base = data_base_url.rstrip("/")
        self._feed = feed
        self._history_days = history_days
        self._min_bars = min_bars
        if retry_policy is not None:
            from common.http_retry import with_retry
            transport = with_retry(transport, policy=retry_policy)
        self._transport = transport
        self._timeout = timeout_seconds
        # Optional sector lookup. When provided, enriched features
        # carry a "; sector=<name>" fragment on source_reference so the
        # stage G Concentration cap (D-0048 max_sector_fraction) is
        # actually enforced in production. Without this wiring B30's
        # sector module was dormant in production (2026-09-30 Bug #2).
        self._sector_provider = sector_provider

    def __call__(
        self,
        candidate: UniverseCandidate,
        as_of_date: date,
        regime_state: RegimeState,
    ) -> UniverseCandidate:
        symbol = candidate.raw.ticker
        bars = self._fetch_bars(symbol, as_of_date)
        if len(bars) < self._min_bars:
            # Insufficient warm-up. Return unenriched: bar/features
            # stay None so Stage B rejects as INSUFFICIENT_WARM_UP.
            return candidate

        # Last bar is the "current day" bar for MarketDataBar.
        last = bars[-1]
        try:
            bar = MarketDataBar(
                security_id=candidate.identity_resolution.identity.security_id,
                ticker_as_of_date=symbol,
                bar_date=as_of_date,
                open=float(last["o"]),
                high=float(last["h"]),
                low=float(last["l"]),
                close=float(last["c"]),
                volume=float(last["v"]),
                adjustment=AdjustmentConvention.SPLIT_ADJUSTED,
                source_reference=f"alpaca-{self._feed}",
            )
        except (KeyError, ValueError, TypeError):
            return candidate  # malformed bar; skip

        liquidity = _mean_dollar_volume(bars[-_LIQUIDITY_WINDOW:])
        atr = _mean_true_range(bars[-(_ATR_WINDOW + 1):])
        momentum = _return_over(bars, _MOMENTUM_LOOKBACK)
        exec_quality = _mean_range_over_vwap(bars[-_EXEC_QUALITY_WINDOW:])

        sector_fragment = ""
        if self._sector_provider is not None:
            try:
                sector = self._sector_provider.sector_of(symbol)
            except Exception:  # noqa: BLE001 - never block a candidate on sector lookup
                sector = None
            if sector:
                # Keep the exact fragment shape stage G already parses
                # (src/d0026/sector_provider.py: sector_fragment()).
                sector_fragment = f"; sector={sector}"

        features = DailySecurityFeatures(
            security_id=bar.security_id,
            feature_date=as_of_date,
            liquidity_measure=liquidity,
            atr_measure=atr,
            momentum_measure=momentum,
            execution_quality_proxy=exec_quality,
            execution_quality_proxy_is_true_quote=False,
            warm_up_sufficient=True,
            source_reference=f"alpaca-{self._feed}{sector_fragment}",
        )

        return UniverseCandidate(
            raw=candidate.raw,
            identity_resolution=candidate.identity_resolution,
            bar=bar,
            features=features,
        )

    def _fetch_bars(self, symbol: str, as_of_date: date) -> List[dict]:
        start = (as_of_date - timedelta(days=self._history_days)).isoformat()
        url = (
            f"{self._base}/v2/stocks/{symbol}/bars"
            f"?feed={self._feed}&timeframe=1Day"
            f"&start={start}&limit=1000&sort=asc"
        )
        headers = {
            "APCA-API-KEY-ID": self._key_id,
            "APCA-API-SECRET-KEY": self._secret,
            "Accept": "application/json",
        }
        try:
            resp = self._transport(url, headers, self._timeout)
        except Exception:  # noqa: BLE001 -- network/DNS/etc.
            return []
        if resp.status != 200:
            return []
        try:
            data = json.loads(resp.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return []
        return list(data.get("bars", []))


# ---- pure math ------------------------------------------------------

def _mean_dollar_volume(bars: List[dict]) -> Optional[float]:
    if not bars:
        return None
    vs = []
    for b in bars:
        v = b.get("v")
        vw = b.get("vw") or b.get("c")
        if v is None or vw is None:
            continue
        vs.append(float(v) * float(vw))
    return (sum(vs) / len(vs)) if vs else None


def _mean_true_range(bars: List[dict]) -> Optional[float]:
    if len(bars) < 2:
        return None
    trs = []
    prev_close = None
    for b in bars:
        try:
            h = float(b["h"]); l = float(b["l"]); c = float(b["c"])
        except (KeyError, ValueError, TypeError):
            continue
        if prev_close is None:
            tr = h - l
        else:
            tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr)
        prev_close = c
    return (sum(trs) / len(trs)) if trs else None


def _return_over(bars: List[dict], lookback: int) -> Optional[float]:
    if len(bars) < lookback + 1:
        return None
    try:
        past = float(bars[-(lookback + 1)]["c"])
        now = float(bars[-1]["c"])
    except (KeyError, ValueError, TypeError):
        return None
    if past <= 0:
        return None
    return (now - past) / past


def _mean_range_over_vwap(bars: List[dict]) -> Optional[float]:
    if not bars:
        return None
    ratios = []
    for b in bars:
        try:
            h = float(b["h"]); l = float(b["l"])
            vw = float(b.get("vw", 0.0)) or float(b["c"])
        except (KeyError, ValueError, TypeError):
            continue
        if vw <= 0:
            continue
        ratios.append((h - l) / vw)
    return (sum(ratios) / len(ratios)) if ratios else None
