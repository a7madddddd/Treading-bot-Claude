"""Entry-signal logic for the SPX 0DTE module (D-0095).

Opening Range Breakout (ORB) confirmed by a VWAP-position filter --
the most-cited 0DTE entry pattern in the research gathered for D-0095,
independently verified by this project's own backtest (not merely
taken on vendor-blog authority).

Returns EVERY distinct breakout opportunity in the session, not just
the first. D-0095's predecessor design stopped at the first breakout
and never scanned again that day; measurement
(docs/trading/decisions.md D-0095, "opportunity-count") found a mean
of ~7.5 distinct opportunities per trading day sitting unused. A
"distinct" opportunity requires price to return INSIDE the ORB range
before a further breakout counts as a new one -- this avoids counting
noisy back-and-forth prints right at the border as dozens of
independent setups.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Literal, Optional

Direction = Literal["call", "put"]


@dataclass(frozen=True)
class Tick:
    dt: datetime
    price: float


@dataclass(frozen=True)
class EntrySignal:
    entry_time: datetime
    direction: Direction
    orb_high: float
    orb_low: float
    vwap_at_entry: float
    price_at_entry: float


def _parse_series(raw_points: List[dict], series_key: str) -> List[Tick]:
    """Defensive parsing: callers may hand this bars with either a unix
    timestamp or an ISO string under `datetime`, and a numeric or
    decimal-string price under `series_key`."""
    ticks: List[Tick] = []
    for point in raw_points:
        raw_dt = point.get("datetime")
        if raw_dt is None:
            continue
        if isinstance(raw_dt, (int, float)):
            dt = datetime.fromtimestamp(raw_dt, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(raw_dt).replace("Z", "+00:00"))

        raw_price = point.get(series_key)
        if raw_price is None:
            continue
        try:
            price = float(raw_price)
        except (TypeError, ValueError):
            continue
        ticks.append(Tick(dt=dt, price=price))
    ticks.sort(key=lambda t: t.dt)
    return ticks


def find_breakout_opportunities(
    raw_points: List[dict],
    series_key: str = "spx",
    opening_range_minutes: int = 15,
    require_vwap_confirmation: bool = True,
) -> List[EntrySignal]:
    """
    ORB: the session's high/low over the first `opening_range_minutes`
    after the first tick in `raw_points` (the caller is responsible for
    handing this a single session's data starting at the official open).

    Entry trigger: a tick that closes outside the ORB high/low, having
    returned inside the range since the previous counted breakout (or
    being the first breakout of the day).
      - breakout above orb_high -> direction = "call"
      - breakout below orb_low  -> direction = "put"

    VWAP confirmation (if require_vwap_confirmation): the breakout
    tick's price must also be on the same side of the running VWAP
    proxy (above it for a call, below it for a put).

    VWAP proxy caveat: when `raw_points` carries index/underlying price
    only (no traded volume), a true volume-weighted VWAP is not
    computable. This falls back to a time-weighted average price
    (TWAP) over ticks-so-far -- a documented simplification, not a
    silent one.

    Returns an empty list if no qualifying breakout occurs in the
    session -- expected and should be reported as "no signal today",
    never silently treated as an error.
    """
    ticks = _parse_series(raw_points, series_key)
    if not ticks:
        return []

    session_start = ticks[0].dt
    orb_cutoff = session_start + timedelta(minutes=opening_range_minutes)

    orb_ticks = [t for t in ticks if t.dt <= orb_cutoff]
    post_orb_ticks = [t for t in ticks if t.dt > orb_cutoff]
    if not orb_ticks or not post_orb_ticks:
        return []

    orb_high = max(t.price for t in orb_ticks)
    orb_low = min(t.price for t in orb_ticks)

    cum_price_sum = sum(t.price for t in orb_ticks)
    cum_count = len(orb_ticks)

    opportunities: List[EntrySignal] = []
    outside_since_last: Optional[Direction] = None

    for tick in post_orb_ticks:
        cum_price_sum += tick.price
        cum_count += 1
        twap_so_far = cum_price_sum / cum_count

        if orb_low <= tick.price <= orb_high:
            outside_since_last = None
            continue

        direction: Direction = "call" if tick.price > orb_high else "put"

        if outside_since_last == direction:
            continue  # still inside the same excursion as the last counted opportunity

        if require_vwap_confirmation:
            if direction == "call" and tick.price <= twap_so_far:
                outside_since_last = direction
                continue
            if direction == "put" and tick.price >= twap_so_far:
                outside_since_last = direction
                continue

        opportunities.append(EntrySignal(
            entry_time=tick.dt,
            direction=direction,
            orb_high=orb_high,
            orb_low=orb_low,
            vwap_at_entry=twap_so_far,
            price_at_entry=tick.price,
        ))
        outside_since_last = direction

    return opportunities
