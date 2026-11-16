"""Per-trade exit logic for the SPX 0DTE module (D-0095).

Evaluated per DATA bar (a bar with an actual trade print), not per
wall-clock minute -- Alpaca's options bars are last-TRADE prices, not
a continuous quote, so illiquid stretches have no prints at all and
must not be read as "nothing happened" one way or the other.

Decision tree, in priority order, each bar:
  1. price >= P0 * (1 + target_pct)         -> EXIT, profit target
  2. if armed (ever reached +arm_pct):
       price <= P0 (breakeven)                -> EXIT, breakeven stop
       no new running-high in last
         stall_bars DATA bars                  -> EXIT, momentum stalled
     else (not yet armed):
       price <= P0 * (1 - stop_pct)            -> EXIT, stop loss
       lagging its own expected pace
         (see lagging_pace below)              -> EXIT, cut early
  3. hold_cap_minutes elapsed since entry      -> EXIT, time cap

Day-pace dynamic cap
--------------------
`hold_cap_minutes` is itself not a fixed number. `DailyPaceTracker`
compares today's first-15-minute SPY range (% of price) against a
trailing 20-trading-day average of the same measure (sequential, never
looking at a future day) and scales the base 40-minute cap inversely:
a faster/wider day shortens it (floor 25 min), a slower/narrower day
lengthens it (ceiling 55 min).

Relative-pace early exit ("lagging_pace")
------------------------------------------
Replaces an earlier fixed-clock "scratch" idea (exit if no progress in
15 minutes) that the Controller correctly rejected: a flat wait can't
tell a trade resolving in 3 minutes from one resolving in 15. Instead,
pre-arm only, compares:

    time_ratio     = elapsed_minutes_since_entry / hold_cap_minutes
    progress_ratio = best_gain_so_far / arm_pct

and exits if `progress_ratio < lag_threshold * time_ratio` -- the
trade is behind its own expected trajectory toward arming, regardless
of how much wall-clock time that represents. Guarded against noise at
the very start of a trade: only evaluated once >= min_bars_before_check
real data bars AND >= min_elapsed_minutes have both passed.

Backtest evidence (docs/trading/decisions.md D-0095): this rule turned
2026's single-position result from -44.20 to +5.88 total pnl (n=38),
and reduced 2025's loss from -36.55 to -7.22 (n=37); applied to every
filtered opportunity rather than just the first per day, two
independent years produced n=254/+77.91 and n=235/+69.08.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Literal, Optional

ExitReason = Literal[
    "target", "breakeven_stop", "stall", "stop_loss", "lagging_pace", "time_cap",
]

BASE_HOLD_CAP_MINUTES = 40.0
MIN_HOLD_CAP_MINUTES = 25.0
MAX_HOLD_CAP_MINUTES = 55.0
PACE_LOOKBACK_DAYS = 20

TARGET_PCT = 0.80
STOP_PCT = 0.40
ARM_PCT = 0.25
STALL_BARS = 3
LAG_THRESHOLD = 0.5
MIN_BARS_BEFORE_LAG_CHECK = 2
MIN_ELAPSED_MINUTES_BEFORE_LAG_CHECK = 3.0


def session_pace_width_pct(bars: List[dict], minutes: int = 15) -> Optional[float]:
    """First `minutes` of the session's high-low range as a fraction of
    the opening price -- "how fast today is", independent of whatever
    opening-range duration the entry signal itself uses, so day-pace
    stays comparable across days. `bars` are SPY minute bars with a
    `c` (close) field, in order."""
    if not bars:
        return None
    window = bars[:minutes] if len(bars) >= minutes else bars
    if not window:
        return None
    open_price = window[0]["c"]
    if open_price <= 0:
        return None
    high = max(b["c"] for b in window)
    low = min(b["c"] for b in window)
    return (high - low) / open_price


@dataclass
class DailyPaceTracker:
    """Trailing window of recent days' pace (opening-range width %), to
    classify today's pace relative to RECENT history only -- never the
    whole dataset, which would leak future information into a
    backtest. Call record_and_get_cap() once per day, in date order; it
    only ever looks at days already seen before today."""

    lookback_days: int = PACE_LOOKBACK_DAYS
    base_cap: float = BASE_HOLD_CAP_MINUTES
    min_cap: float = MIN_HOLD_CAP_MINUTES
    max_cap: float = MAX_HOLD_CAP_MINUTES
    _history: List[float] = field(default_factory=list)

    def record_and_get_cap(self, todays_pace: Optional[float]) -> float:
        if todays_pace is None or not self._history:
            cap = self.base_cap
        else:
            avg = sum(self._history) / len(self._history)
            if avg <= 0:
                cap = self.base_cap
            else:
                ratio = todays_pace / avg
                cap = self.base_cap / ratio
                cap = max(self.min_cap, min(self.max_cap, cap))
        if todays_pace is not None:
            self._history.append(todays_pace)
            if len(self._history) > self.lookback_days:
                self._history.pop(0)
        return cap


@dataclass(frozen=True)
class ExitResult:
    exit_price: float
    exit_time: datetime
    reason: ExitReason
    armed: bool
    running_max_pct: float  # best unrealized gain reached, as a fraction of P0


def simulate_exit(
    bars: List[dict],
    entry_time: datetime,
    hold_cap_minutes: float,
    target_pct: float = TARGET_PCT,
    stop_pct: float = STOP_PCT,
    arm_pct: float = ARM_PCT,
    stall_bars: int = STALL_BARS,
    lag_threshold: float = LAG_THRESHOLD,
    min_bars_before_lag_check: int = MIN_BARS_BEFORE_LAG_CHECK,
    min_elapsed_minutes_before_lag_check: float = MIN_ELAPSED_MINUTES_BEFORE_LAG_CHECK,
) -> Optional[ExitResult]:
    """
    `bars`: option bars (each with an ISO `t` timestamp and a `c` close
    price), already fetched for a window covering
    [entry_time, entry_time + hold_cap_minutes]. The first bar at or
    after entry_time is treated as the entry fill (P0). Returns None if
    no bar at or after entry_time exists (no usable data for this
    trade -- the caller must not fabricate a price)."""
    usable = sorted(
        (b for b in bars if _parse_ts(b["t"]) >= entry_time),
        key=lambda b: b["t"],
    )
    if not usable:
        return None

    cap_deadline = entry_time + timedelta(minutes=hold_cap_minutes)
    p0 = usable[0]["c"]
    target_price = p0 * (1 + target_pct)
    pre_arm_stop_price = p0 * (1 - stop_pct)

    running_max = p0
    armed = False
    bars_since_new_high = 0
    bars_seen = 0
    last_bar = usable[0]

    for bar in usable:
        bar_time = _parse_ts(bar["t"])
        if bar_time > cap_deadline:
            break
        price = bar["c"]
        last_bar = bar
        bars_seen += 1

        if price > running_max:
            running_max = price
            bars_since_new_high = 0
        else:
            bars_since_new_high += 1

        if not armed and running_max >= p0 * (1 + arm_pct):
            armed = True

        if price >= target_price:
            return ExitResult(price, bar_time, "target", armed, (running_max - p0) / p0)

        if armed:
            if price <= p0:
                return ExitResult(price, bar_time, "breakeven_stop", armed, (running_max - p0) / p0)
            if bars_since_new_high >= stall_bars:
                return ExitResult(price, bar_time, "stall", armed, (running_max - p0) / p0)
        else:
            if price <= pre_arm_stop_price:
                return ExitResult(price, bar_time, "stop_loss", armed, (running_max - p0) / p0)

            elapsed_minutes = (bar_time - entry_time).total_seconds() / 60
            if (
                bars_seen >= min_bars_before_lag_check
                and elapsed_minutes >= min_elapsed_minutes_before_lag_check
                and hold_cap_minutes > 0
            ):
                time_ratio = min(elapsed_minutes / hold_cap_minutes, 1.0)
                progress_ratio = max((running_max - p0) / p0, 0.0) / arm_pct
                if progress_ratio < lag_threshold * time_ratio:
                    return ExitResult(price, bar_time, "lagging_pace", armed, (running_max - p0) / p0)

    last_time = _parse_ts(last_bar["t"])
    return ExitResult(last_bar["c"], last_time, "time_cap", armed, (running_max - p0) / p0)


def _parse_ts(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))
