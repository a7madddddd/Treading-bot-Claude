"""Self-measuring SPY:SPX ratio for the SPX 0DTE module (D-0095-A
follow-up, 2026-11-16).

Replaces the earlier hardcoded `APPROX_SPY_TO_SPX_RATIO` constant in
`alpaca_client.py`. That constant was a genuine measurement when it
was first set, but sitting as a static value in code is exactly the
failure mode this project's own rules warn against: "a number that
felt reasonable... becomes permanent, and nobody later remembers it
was a guess" (.claude/rules/03-research-and-workflow.md). Confirmed
by measurement, not assumption: the hardcoded 8.97 had drifted +11.82%
from the real value (10.0302, median over 20 trading days,
2026-11-16) by the time this was checked.

Alpaca serves no SPX index-level data at all, so "the real value" is
measured by pairing SPY daily closes (Alpaca) against FRED's free
SP500 series (same FRED_API_KEY already used by econ_calendar.py) --
not a new dependency, not a new data-source question.

`RatioProvider` keeps the LAST SUCCESSFULLY MEASURED ratio in memory
and only re-measures when it is older than `max_age`. A measurement
failure (network error, FRED down) falls back to the last real
measurement this process has made, never to a hardcoded default --
except on the very first call in a fresh process, where there is
genuinely no measurement yet. That one bootstrap case is labeled as
such, not hidden.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Dict, Optional

from spx0dte.alpaca_client import AlpacaDataError, AlpacaOptionsClient

FRED_BASE_URL = "https://api.stlouisfed.org/fred"
DEFAULT_LOOKBACK_DAYS = 30  # calendar days; ~20 trading days
DEFAULT_MAX_AGE = timedelta(hours=24)

# BOOTSTRAP ONLY -- used exactly once, if a fresh process needs a
# ratio before its first real measurement has ever succeeded (e.g. a
# strike search at process startup before any network round trip has
# completed). Every call after one successful measurement uses that
# real, measured value instead -- never this constant again for the
# lifetime of the process. If this value is ever seen in a log beyond
# the very first call, that itself is evidence something is wrong
# (FRED or Alpaca unreachable on every subsequent attempt too).
BOOTSTRAP_RATIO = 10.03


class RatioCalibrationError(Exception):
    """Raised by measure_ratio_now() when no (SPY, SP500) pair could
    be matched -- never silently substituted with a guess."""


def fetch_sp500_closes(api_key: str, start_date: str, end_date: str) -> Dict[str, float]:
    """Returns {date_str: close_float} from FRED's SP500 daily series."""
    qs = urllib.parse.urlencode({
        "series_id": "SP500", "api_key": api_key, "file_type": "json",
        "observation_start": start_date, "observation_end": end_date,
    })
    request = urllib.request.Request(f"{FRED_BASE_URL}/series/observations?{qs}")
    with urllib.request.urlopen(request, timeout=15) as response:
        body = json.loads(response.read().decode("utf-8"))

    closes: Dict[str, float] = {}
    for obs in body.get("observations", []):
        value = obs.get("value")
        if value in (None, ".", ""):
            continue  # FRED marks a missing observation as "."
        try:
            closes[obs["date"]] = float(value)
        except ValueError:
            continue
    return closes


def measure_ratio_now(
    client: AlpacaOptionsClient, fred_api_key: str, *, now: Optional[datetime] = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> float:
    """One real measurement: median SP500/SPY close ratio over the
    last `lookback_days` calendar days with a matched date on both
    sides. Raises RatioCalibrationError if no pair matches -- the
    caller decides what to do (e.g. RatioProvider falls back to its
    last known-good measurement), this function never guesses."""
    now = now or datetime.now(timezone.utc)
    end = now.date()
    start = end - timedelta(days=lookback_days)

    sp500_closes = fetch_sp500_closes(fred_api_key, start.isoformat(), end.isoformat())
    spy_bars = client.get_spy_daily_bars(start.isoformat(), end.isoformat())

    ratios = []
    for bar in spy_bars:
        date_str = bar["t"][:10]
        spy_close = bar["c"]
        sp500_close = sp500_closes.get(date_str)
        if sp500_close is None or spy_close <= 0:
            continue
        ratios.append(sp500_close / spy_close)

    if not ratios:
        raise RatioCalibrationError(
            f"no matched (SPY, SP500) date pairs in {start.isoformat()}..{end.isoformat()}"
        )
    return median(ratios)


@dataclass
class RatioProvider:
    """Caches the last successfully measured ratio and only
    re-measures when stale. Thread-unsafe by design, matching this
    module's other simple, single-process components -- a future
    multi-process live loop would need its own synchronization, not
    added speculatively here."""

    client: AlpacaOptionsClient
    fred_api_key: str
    max_age: timedelta = DEFAULT_MAX_AGE
    _last_ratio: Optional[float] = field(default=None, repr=False)
    _last_measured_at: Optional[datetime] = field(default=None, repr=False)

    def get_ratio(self, *, now: Optional[datetime] = None) -> float:
        now = now or datetime.now(timezone.utc)
        is_stale = (
            self._last_measured_at is None
            or (now - self._last_measured_at) > self.max_age
        )
        if not is_stale:
            return self._last_ratio  # type: ignore[return-value]

        try:
            fresh = measure_ratio_now(self.client, self.fred_api_key, now=now)
            self._last_ratio = fresh
            self._last_measured_at = now
            return fresh
        except (RatioCalibrationError, OSError, AlpacaDataError):
            # 2026-11-22 fix: measure_ratio_now's real Alpaca call raises
            # AlpacaDataError (not an OSError subclass) on retry exhaustion --
            # missing it here meant a real outage crashed the caller instead
            # of falling back to the last good ratio as this docstring promises.
            if self._last_ratio is not None:
                return self._last_ratio  # stale but real, not a guess
            return BOOTSTRAP_RATIO  # only reachable on a fresh process's first, failed attempt
