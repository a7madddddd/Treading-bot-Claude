"""Economic-event safety filter for the SPX 0DTE module (D-0095).

Three free, official, government sources only -- no commercial or
unofficial calendar API, consistent with this project's preference for
free sources and its existing caution on unclear-ToS scrapers:

  1. FRED `releases/dates` -- reuses the FRED_API_KEY already in
     .env.example (D-0042 free-source calibration providers). Broad
     coverage of US economic release dates.
  2. BLS.gov official ICS feed -- no key needed. CPI + jobs report
     (Employment Situation / NFP) specifically.
  3. Federal Reserve FOMC meeting calendar -- no key, no machine-
     readable feed (HTML only). Only 8 meetings/year, so this module
     ships a short HARDCODED list that must be refreshed periodically
     against
     https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
     (verified 2026-11-06). A deliberate bootstrap of a short,
     slow-changing, officially-published list -- not a guessed number
     subject to the "calculate from recorded data" rule, which is
     about trading thresholds, not calendar facts.

`is_blackout_window()` tells the entry-filter pipeline whether a
candidate trade's hold window would overlap a scheduled high-impact US
macro release, so the pipeline can skip the signal rather than trade
through a news-driven volatility spike it has no edge on. This is a
SAFETY FILTER, not a trading signal -- it only ever says "skip", never
"enter".

Stdlib only (urllib), per this project's existing dependency policy.
"""
from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

FRED_BASE_URL = "https://api.stlouisfed.org/fred"
BLS_CPI_ICS_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
_NETWORK_TIMEOUT_SECONDS = 15.0

_TRANSIENT_NETWORK_EXCEPTIONS = (
    urllib.error.URLError, socket.timeout, ConnectionError, TimeoutError,
)


@dataclass(frozen=True)
class EconEvent:
    name: str
    at_utc: datetime
    source: str


# Verified 2026-11-06 against the Federal Reserve's own calendar page.
# Announcement time is 2:00 PM ET on the second day of each two-day
# meeting -- 18:00 or 19:00 UTC depending on EST/EDT.
# TODO: refresh this list each quarter.
FOMC_2026_DATES_ET = [
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
]


def _is_us_dst(month: int, day: int) -> bool:
    """Rough DST window shared with signal.py's own convention --
    good enough for a filter, not for anything safety-critical."""
    return (3, 15) <= (month, day) <= (11, 1)


def _http_get(url: str) -> Optional[bytes]:
    """Returns the response body, or None on any transient network
    failure or non-2xx status -- this filter degrades gracefully
    rather than ever raising into the entry-filter pipeline."""
    try:
        request = urllib.request.Request(url=url, method="GET",
                                          headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=_NETWORK_TIMEOUT_SECONDS) as response:
            if not (200 <= response.getcode() < 300):
                return None
            return response.read()
    except urllib.error.HTTPError:
        return None
    except _TRANSIENT_NETWORK_EXCEPTIONS:
        return None


def _fomc_events() -> List[EconEvent]:
    events = []
    for date_str in FOMC_2026_DATES_ET:
        y, m, d = (int(x) for x in date_str.split("-"))
        offset = 4 if _is_us_dst(m, d) else 5
        at_utc = datetime(y, m, d, 14 + offset, 0, tzinfo=timezone.utc)
        events.append(EconEvent(
            name="FOMC rate decision", at_utc=at_utc,
            source="federalreserve.gov (hardcoded, verified 2026-11-06)",
        ))
    return events


def _fred_events(api_key: Optional[str] = None, limit: int = 100) -> List[EconEvent]:
    key = api_key or os.environ.get("FRED_API_KEY")
    if not key:
        return []  # degrade gracefully -- this is a filter, not a hard dependency

    qs = urllib.parse.urlencode({
        "api_key": key, "file_type": "json", "limit": limit, "sort_order": "asc",
        "include_release_dates_with_no_data": "true",
    })
    body = _http_get(f"{FRED_BASE_URL}/releases/dates?{qs}")
    if body is None:
        return []

    try:
        parsed = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return []

    events = []
    for row in parsed.get("release_dates", []):
        try:
            # FRED gives a date only, no time-of-day -- treat as an
            # all-day blackout for that calendar date (conservative).
            y, m, d = (int(x) for x in row["date"].split("-"))
            at_utc = datetime(y, m, d, 0, 0, tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        events.append(EconEvent(
            name=row.get("release_name", "FRED release"), at_utc=at_utc,
            source="FRED releases/dates",
        ))
    return events


def _bls_events() -> List[EconEvent]:
    """Minimal ICS parsing of the official BLS feed -- no external ics
    library dependency, consistent with this project's stdlib-only
    policy."""
    body = _http_get(BLS_CPI_ICS_URL)
    if body is None:
        return []

    events = []
    name = None
    dtstart = None
    for line in body.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith("SUMMARY:"):
            name = line[len("SUMMARY:"):]
        elif line.startswith("DTSTART"):
            value = line.split(":", 1)[-1]
            try:
                if len(value) == 8:  # YYYYMMDD, all-day
                    y, m, d = int(value[:4]), int(value[4:6]), int(value[6:8])
                    dtstart = datetime(y, m, d, 0, 0, tzinfo=timezone.utc)
                else:  # YYYYMMDDTHHMMSSZ
                    dtstart = datetime.strptime(value.rstrip("Z"), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
            except ValueError:
                dtstart = None
        elif line.startswith("END:VEVENT") and name and dtstart:
            events.append(EconEvent(name=name, at_utc=dtstart, source="bls.gov ICS"))
            name, dtstart = None, None
    return events


def get_upcoming_events(days_ahead: int = 14, fred_api_key: Optional[str] = None) -> List[EconEvent]:
    """Best-effort merge of all three sources. Each source's failure is
    caught individually -- a down source degrades coverage, it never
    crashes the caller (this is a safety filter, not a critical path)."""
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=days_ahead)
    all_events: List[EconEvent] = []

    for fetch in (_fomc_events, lambda: _fred_events(fred_api_key), _bls_events):
        try:
            all_events.extend(fetch())
        except Exception:  # noqa: BLE001 -- degrade gracefully, never crash
            continue

    return sorted(
        (e for e in all_events if now <= e.at_utc <= horizon),
        key=lambda e: e.at_utc,
    )


def is_blackout_window(
    window_start: datetime, window_end: datetime,
    events: Optional[List[EconEvent]] = None,
) -> Optional[EconEvent]:
    """Returns the first EconEvent that overlaps [window_start,
    window_end], or None if clear. Caller passes the candidate trade's
    planned entry and (entry + hold_cap) as the window."""
    if events is None:
        events = get_upcoming_events()
    for e in events:
        if window_start <= e.at_utc <= window_end:
            return e
    return None
