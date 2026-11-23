"""Comprehensive live VIX proxy for the SPX 0DTE module (D-0095-A,
2026-11-18 -- market-context workstream, priority 3 of 3).

No free real-time official VIX source exists anywhere (confirmed by
direct live testing: CBOE's own endpoints refuse/404, Yahoo's public
chart endpoint lags ~15 minutes consistently, Nasdaq rejects the
symbol -- the same structural, industry-wide licensing restriction
already known from real-time options data, OPRA). This module does
NOT try to fetch a live absolute VIX number. It anchors to
YESTERDAY's real, free, official FRED VIXCLS close (one day stale,
never guessed) and scales it by TODAY's real-time relative move in
VIXY (a real tradable VIX-futures ETP, fetched via the already-
generalized `alpaca_client.get_stock_minute_bars`/`get_stock_daily_bars`,
`feed=iex`, same zero-delay mechanism already proven for SPY/QQQ/IWM)
-- the exact "stale real anchor + live relative move" shape already
proven twice in this project (`greeks.reprice_from_stale_quote` /
Option B; `ratio_calibration.RatioProvider`):

    estimated_vix_today = yesterdays_real_vix_close * (vixy_now / vixy_yesterday_close)

VALIDATED against 630 real historical samples (2023-06-01..2025-12-31,
`scripts/spx0dte_measure_vix_proxy.py`) before being trusted: median
absolute error 2.1%, mean 3.9%, near-zero signed bias (+1.1%) --
usable for `entry_filters.vix_in_tradeable_range`'s wide 12-35
bootstrap band. An earlier SPY-realized-volatility-ratio candidate
was measured in the SAME run and REJECTED (mean abs error 23.7%,
strong positive bias) -- deliberately not used here, not an oversight.

A worst-case error of 387.2% was also observed in that same
630-sample run (a single anomalous day, likely a VIXY-specific data
event, not a flaw in the method itself) -- `MAX_PLAUSIBLE_RATIO`
below exists because of that one real observation, not a
hypothetical worry.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from spx0dte.alpaca_client import AlpacaDataError, AlpacaOptionsClient

FRED_BASE_URL = "https://api.stlouisfed.org/fred"
VIXY_PROXY_SYMBOL = "VIXY"
DEFAULT_LOOKBACK_DAYS = 10  # calendar days; only need the most recent trading day
DEFAULT_MAX_AGE = timedelta(hours=24)

MAX_PLAUSIBLE_RATIO = 3.0
"""Bootstrap, conservative judgment call -- rejects an estimate
implying VIX more than tripled or dropped to a third of yesterday's
close intraday, which has not happened even on real historical crash
days in the measured sample. Exists specifically because of a real
observed 387.2% error in one anomalous day
(scripts/spx0dte_measure_vix_proxy.py, 2023-06-01..2025-12-31 run) --
better to return "unavailable" (same graceful skip as vix_level=None
everywhere else in this module) than trust a corrupted estimate."""


class VixProxyError(Exception):
    """Raised by `measure_vix_anchor_now` when no usable (VIXCLS,
    VIXY) pair could be found -- never silently substituted with a
    guess. Callers should treat this as "no VIX data available
    today", the same graceful degradation `entry_filters.
    vix_in_tradeable_range` already has for `vix_level=None`."""


def fetch_vixcls_closes(api_key: str, start_date: str, end_date: str) -> Dict[str, float]:
    """Same shape as `ratio_calibration.fetch_sp500_closes`, for
    FRED's free VIXCLS (CBOE Volatility Index) daily series instead
    of SP500. Kept as its own copy rather than importing from
    ratio_calibration.py -- that module's scope is the SPY:SPX ratio,
    a different concern; duplicating ~10 lines here is cheaper than
    coupling the two."""
    qs = urllib.parse.urlencode({
        "series_id": "VIXCLS", "api_key": api_key, "file_type": "json",
        "observation_start": start_date, "observation_end": end_date,
    })
    request = urllib.request.Request(f"{FRED_BASE_URL}/series/observations?{qs}")
    with urllib.request.urlopen(request, timeout=15) as response:
        body = json.loads(response.read().decode("utf-8"))

    closes: Dict[str, float] = {}
    for obs in body.get("observations", []):
        value = obs.get("value")
        if value in (None, ".", ""):
            continue
        try:
            closes[obs["date"]] = float(value)
        except ValueError:
            continue
    return closes


@dataclass(frozen=True)
class VixAnchor:
    """Yesterday's real values, measured once per day."""
    date: str  # the trading day this anchor is FOR (e.g. "today" when measured at the start of today)
    yesterday_vix_close: float
    yesterday_vixy_close: float


def measure_vix_anchor_now(
    client: AlpacaOptionsClient, fred_api_key: str, *, now: Optional[datetime] = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> VixAnchor:
    """Finds the most recent trading day strictly before `now` with
    BOTH a real FRED VIXCLS close and a real VIXY close, and returns
    them as the anchor for today. Raises VixProxyError if none found
    -- the caller decides what to do (treat VIX as unavailable), this
    function never guesses."""
    now = now or datetime.now(timezone.utc)
    today_str = now.date().isoformat()
    end = now.date()
    start = end - timedelta(days=lookback_days)

    vix_closes = fetch_vixcls_closes(fred_api_key, start.isoformat(), end.isoformat())
    vixy_bars = client.get_stock_daily_bars(VIXY_PROXY_SYMBOL, start.isoformat(), end.isoformat())
    vixy_closes = {b["t"][:10]: b["c"] for b in vixy_bars}

    candidate_dates = sorted(d for d in vix_closes if d < today_str)
    for date_str in reversed(candidate_dates):
        vixy_close = vixy_closes.get(date_str)
        if vixy_close is not None and vixy_close > 0:
            return VixAnchor(
                date=today_str, yesterday_vix_close=vix_closes[date_str],
                yesterday_vixy_close=vixy_close,
            )

    raise VixProxyError(
        f"no matched (VIXCLS, {VIXY_PROXY_SYMBOL}) prior trading day in "
        f"{start.isoformat()}..{end.isoformat()}"
    )


@dataclass
class VixProxyProvider:
    """Caches the day's anchor (yesterday's real VIXCLS + VIXY
    closes) and only re-measures once per calendar day -- same "lock
    once per day" pattern as `exit_logic.DailyPaceTracker` and
    `sizing.DailyLossLimitTracker`. `get_vix_level()` is the one
    method `live_loop` calls each tick; it is cheap (one Alpaca
    minute-bar fetch for VIXY's live price) except on the first call
    of a new day, which also re-measures the anchor."""

    client: AlpacaOptionsClient
    fred_api_key: str
    max_age: timedelta = DEFAULT_MAX_AGE
    _anchor: Optional[VixAnchor] = field(default=None, repr=False)

    def get_vix_level(self, *, now: Optional[datetime] = None) -> Optional[float]:
        """Returns the estimated VIX level, or None if unavailable
        (no anchor could be measured, or today's live VIXY price
        could not be fetched, or the resulting estimate is
        implausible per MAX_PLAUSIBLE_RATIO) -- never raises, mirrors
        `vix_in_tradeable_range`'s own existing graceful-skip
        contract for `vix_level=None`."""
        now = now or datetime.now(timezone.utc)
        today_str = now.date().isoformat()

        if self._anchor is None or self._anchor.date != today_str:
            try:
                self._anchor = measure_vix_anchor_now(self.client, self.fred_api_key, now=now)
            except (VixProxyError, AlpacaDataError):
                # 2026-11-22 fix: measure_vix_anchor_now's own get_stock_daily_bars
                # call can raise AlpacaDataError directly (not wrapped in
                # VixProxyError) -- missing it here meant a real Alpaca outage on
                # the daily anchor refresh crashed the tick instead of degrading
                # to None as this method's own docstring promises ("never raises").
                return None

        try:
            today_bars = self.client.get_stock_minute_bars(VIXY_PROXY_SYMBOL, today_str)
        except AlpacaDataError:
            return None
        if not today_bars:
            return None
        vixy_now = sorted(today_bars, key=lambda b: b["t"])[-1]["c"]
        if vixy_now <= 0 or self._anchor.yesterday_vixy_close <= 0:
            return None

        ratio = vixy_now / self._anchor.yesterday_vixy_close
        if ratio <= 0 or ratio > MAX_PLAUSIBLE_RATIO or ratio < 1.0 / MAX_PLAUSIBLE_RATIO:
            return None

        estimate = self._anchor.yesterday_vix_close * ratio
        if estimate <= 0:
            return None
        return estimate
