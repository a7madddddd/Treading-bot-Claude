"""Entry confirmation filters for the SPX 0DTE module (D-0095).

Each filter either passes (signal proceeds) or rejects (skip this
opportunity) -- none ever upgrades a rejected signal into an approved
one; they only subtract candidates.

`volume_confirmed` is kept here but deliberately NOT included in
`run_all_filters`. Measurement (docs/trading/decisions.md D-0095,
"volume-filter measurement") found its effect inconsistent across two
independent years (helped in one year's pooled view, hurt in the
other) and it shrank the sample to n=7-10 where single-trade
artifacts dominated. It stays in the module in case future data
revives it as a real filter -- it is not deleted history.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional

from spx0dte.econ_calendar import is_blackout_window, get_upcoming_events


@dataclass(frozen=True)
class FilterResult:
    passed: bool
    reason: str  # always filled, even on pass -- so a report shows WHY, not just pass/fail


def volume_confirmed(
    spy_bars: List[dict], signal_index: int, lookback: int = 20, min_ratio: float = 1.3,
) -> FilterResult:
    """NOT wired into run_all_filters -- see module docstring. Kept for
    reference and for any future re-measurement."""
    if signal_index < lookback:
        return FilterResult(False, f"not enough history for a {lookback}-bar volume average yet")

    recent = spy_bars[signal_index - lookback:signal_index]
    avg_vol = sum(b.get("v", 0) for b in recent) / len(recent)
    this_vol = spy_bars[signal_index].get("v", 0)
    if avg_vol <= 0:
        return FilterResult(False, "no volume data available")

    ratio = this_vol / avg_vol
    passed = ratio >= min_ratio
    return FilterResult(
        passed,
        f"breakout-bar volume {this_vol:.0f} vs {lookback}-bar avg {avg_vol:.0f} "
        f"(ratio {ratio:.2f}, need >= {min_ratio}) -- {'OK' if passed else 'too thin, rejected'}",
    )


def vix_in_tradeable_range(
    vix_level: Optional[float], low: float = 12.0, high: float = 35.0,
) -> FilterResult:
    """Deliberately wide, conservative bootstrap range: excludes a
    dead/no-movement market (VIX < 12) and outright crisis chaos
    (VIX > 35), where a 25-55 minute mechanical rule has no measured
    edge either way. Not claiming to have found an edge inside the
    range -- only removing the two ends most research agrees are
    unreliable."""
    if vix_level is None:
        # 2026-11-22 fix: this filter exists specifically to keep the bot out
        # of VIX>35 crisis conditions, and missing VIX data is most likely
        # during exactly that kind of real market/data stress -- failing
        # OPEN (pass) here meant the filter was most likely to be blind
        # exactly when it mattered most. Fail SAFE (reject) instead,
        # consistent with this project's bootstrap-conservative bias.
        return FilterResult(False, "no VIX data for this date -- treated as outside tradeable range (fail-safe)")
    passed = low <= vix_level <= high
    return FilterResult(
        passed,
        f"VIX={vix_level:.1f} (range {low}-{high}) -- {'OK' if passed else 'outside range, rejected'}",
    )


def economic_calendar_clear(entry_time: datetime, hold_cap_minutes: float) -> FilterResult:
    """Rejects a signal whose hold window overlaps a scheduled
    FOMC/CPI/jobs release (econ_calendar.is_blackout_window)."""
    window_end = entry_time + timedelta(minutes=hold_cap_minutes)
    try:
        events = get_upcoming_events(days_ahead=400)
    except Exception as e:  # noqa: BLE001 -- safety filter, never crash the pipeline
        return FilterResult(True, f"calendar fetch failed ({e}) -- filter skipped, not rejected")

    hit = is_blackout_window(entry_time, window_end, events=events)
    if hit:
        return FilterResult(False, f"overlaps {hit.name} at {hit.at_utc.isoformat()} ({hit.source})")
    return FilterResult(True, "no scheduled high-impact release in this window")


def time_quality_window(
    entry_time: datetime, blocked_start_et: tuple = (12, 0), blocked_end_et: tuple = (13, 30),
) -> FilterResult:
    """Bootstrap window (12:00-13:30 ET): the "lunch chop" window cited
    repeatedly in professional-practice research as low-liquidity,
    low-conviction, false-signal-prone. entry_time is UTC; converts
    with the same rough DST rule used across this module -- good
    enough for a filter, not for anything safety-critical."""
    is_dst = (3, 15) <= (entry_time.month, entry_time.day) <= (11, 1)
    offset = 4 if is_dst else 5
    et_hour = (entry_time.hour - offset) % 24
    et_minute = entry_time.minute
    et_tuple = (et_hour, et_minute)

    blocked = blocked_start_et <= et_tuple <= blocked_end_et
    return FilterResult(
        not blocked,
        f"entry at {et_hour:02d}:{et_minute:02d} ET -- "
        f"{'inside lunch-chop window, rejected' if blocked else 'OK, outside lunch window'}",
    )


def cross_market_confirmed(
    direction: str, qqq_bars: List[dict], iwm_bars: List[dict], opening_range_minutes: int = 15,
) -> FilterResult:
    """Requires QQQ and IWM to show the SAME directional bias as the
    SPX signal over their own opening range. Not currently wired into
    run_all_filters (the D-0095 backtest never fetched QQQ/IWM bars) --
    available for a future measurement pass."""

    def _bias(bars: List[dict]) -> Optional[str]:
        if len(bars) < 2:
            return None
        orb_bars = bars[:max(1, opening_range_minutes)]
        orb_mid = (max(b["c"] for b in orb_bars) + min(b["c"] for b in orb_bars)) / 2
        latest = bars[-1]["c"]
        if latest > orb_mid:
            return "call"
        if latest < orb_mid:
            return "put"
        return None

    qqq_bias = _bias(qqq_bars)
    iwm_bias = _bias(iwm_bars)
    if qqq_bias is None or iwm_bias is None:
        return FilterResult(True, "insufficient QQQ/IWM data -- filter skipped, not rejected")

    confirmed = (qqq_bias == direction) and (iwm_bias == direction)
    return FilterResult(
        confirmed,
        f"SPX={direction} QQQ={qqq_bias} IWM={iwm_bias} -- "
        f"{'both confirm' if confirmed else 'no broad-market confirmation, rejected'}",
    )


def liquidity_ok(bid: float, ask: float, max_spread_pct: float = 0.10) -> FilterResult:
    """LIVE-TRADING ONLY -- Alpaca's historical options bars give trade
    prices, not bid/ask, so this filter cannot run against backtest
    data. Applies when checking a real live quote right before
    submitting an order."""
    if bid <= 0 or ask <= 0 or ask < bid:
        return FilterResult(False, f"invalid quote (bid={bid}, ask={ask})")
    mid = (bid + ask) / 2
    spread_pct = (ask - bid) / mid
    passed = spread_pct <= max_spread_pct
    return FilterResult(
        passed,
        f"spread {spread_pct:.1%} of mid (bid={bid}, ask={ask}, max {max_spread_pct:.0%}) -- "
        f"{'OK' if passed else 'too wide, rejected'}",
    )


def run_all_filters(
    vix_level: Optional[float], entry_time: datetime, hold_cap_minutes: float,
) -> List[FilterResult]:
    """The filters the D-0095 backtest actually ran: VIX range,
    economic calendar, and the lunch-chop time window. volume_confirmed
    and cross_market_confirmed are deliberately excluded -- see module
    and function docstrings."""
    return [
        vix_in_tradeable_range(vix_level),
        economic_calendar_clear(entry_time, hold_cap_minutes),
        time_quality_window(entry_time),
    ]
