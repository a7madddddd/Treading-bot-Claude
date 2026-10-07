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

DEFAULT_REQUESTS_PER_MINUTE = 150.0
"""D-0085. Leaves headroom in the broker's rate limit for the LIVE
ENGINE, which shares the same account quota.

This job issues one bars request per surviving symbol -- 11,683 on the
2026-10-05 measurement -- back to back. The engine meanwhile asks for
one price per open position every 30 seconds to evaluate the protective
Floor. On 2026-10-07 the two collided: between 06:19 and 06:26 ET, with
this job running since 06:00, the engine was refused with HTTP 429 for
QQQ, AMZN, GOOGL and TSLA in turn, and each refusal is a Floor check
that did not happen. The job's own log carried 429s too, so the limit
was genuinely saturated rather than briefly spiked.

The engine already retries three times with 1s and 2s backoff, which
handles a momentary spike and cannot handle an hour of saturation.
Retrying harder is the wrong lever; not filling the bucket is the right
one.

150/min against a commonly documented 200/min free tier leaves ~50/min
spare, while the engine needs about 10/min with five open positions --
a five-fold margin, chosen so a slower day or an extra position does
not eat it.

Cost, and why it is affordable: the run stretches from roughly 58 to
roughly 78 minutes. Started by its timer at 06:00 ET it finishes near
07:18, and the first D-0021 trigger is 09:30 -- over two hours of
headroom. The run is also already forbidden while the market is open
(the guard in scripts/run_universe_selection.py), so this pacing
protects the pre-open window, not the session.

None is accepted and means "do not pace" -- for tests, and for a
deliberate operator override.
"""


class _Pacer:
    """Spaces calls so they do not exceed `requests_per_minute`.

    Deliberately a minimum INTERVAL between calls rather than a token
    bucket: a bucket permits a burst that empties it, and a burst is
    exactly what starves the engine for the seconds that follow. The
    clock and sleep are injectable so the behaviour is tested without
    the suite ever sleeping.
    """

    def __init__(self, requests_per_minute, *, monotonic=None, sleep=None):
        if requests_per_minute is not None and requests_per_minute <= 0:
            raise AlpacaFeatureEnricherConfigError(
                f"requests_per_minute must be positive or None; "
                f"got {requests_per_minute!r}"
            )
        self._min_interval = (
            None if requests_per_minute is None
            else 60.0 / float(requests_per_minute)
        )
        import time as _time
        self._monotonic = monotonic or _time.monotonic
        self._sleep = sleep or _time.sleep
        self._last: Optional[float] = None

    def wait(self) -> None:
        if self._min_interval is None:
            return
        now = self._monotonic()
        if self._last is not None:
            due = self._last + self._min_interval
            if now < due:
                self._sleep(due - now)
                now = self._monotonic()
        self._last = now


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
        requests_per_minute: Optional[float] = DEFAULT_REQUESTS_PER_MINUTE,
        monotonic=None,     # injectable clock, for tests
        sleep=None,         # injectable sleep, for tests
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
        self._pacer = _Pacer(requests_per_minute,
                             monotonic=monotonic, sleep=sleep)

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
        # D-0085: pace BEFORE the request, so the live engine always has
        # room in the shared account quota for its Floor checks.
        self._pacer.wait()
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
        # Alpaca may return {"bars": null} for symbols with no data in
        # the window. dict.get(k, default) returns the default only when
        # k is MISSING; a present-but-null value falls through to list(None)
        # and crashes the pipeline. Guard with `or []`.
        return list(data.get("bars") or [])


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
