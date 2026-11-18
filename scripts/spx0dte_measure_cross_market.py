"""Measures entry_filters.cross_market_confirmed's real discriminating
power -- built but never measured against real data (same situation
volume_confirmed was in before it was measured and dropped, D-0095).

Methodology, identical in spirit to the volume-filter measurement
that led to D-0095's "drop it, it doesn't discriminate" finding: run
the SAME locked pipeline (signal -> entry_filters minus cross-market
-> exit_logic.simulate_exit with the day-pace cap) on real historical
data, but DO NOT enforce cross_market_confirmed -- only TAG each
trade with what it would have decided. Report the "would have
rejected" subset's pnl/win-rate against the "would have passed"
subset's. If the rejected subset performs no worse, cross-market
confirmation is not actually adding discriminating power, same
finding as the volume filter; if it performs clearly worse, that is
real evidence FOR enabling it.

Uses only PAST, already-completed trading days (no live/recency
restriction concern) -- this is historical measurement, not a live
run.

### BUG FOUND AND FIXED, 2026-11-17 -- the first run of this script
had a real lookahead problem, found while reviewing the code to
answer a Controller question, not by a test failing.

`get_stock_minute_bars` fetches a WHOLE trading day's bars once per
date and the loop below reused that same array, unmodified, for
EVERY opportunity found that day. `cross_market_confirmed`'s own
bias check uses the LAST bar of whatever array it is given
(`bars[-1]["c"]`) as "the current price" -- so for an opportunity at
10:15am, the first run was comparing it against QQQ/IWM's 4:00pm
closing price, information that did not exist yet at 10:15am. This
is look-ahead bias: the first run's reported profit_factor numbers
(0.895 vs 2.1282, then 0.5493 vs 2.3142) may be contaminated by it,
and should not be trusted as-is.

This does NOT affect the live loop (`live_loop.run_one_tick`):
fetching "today" bars from a live account only ever returns bars up
to the real current moment, since future bars don't exist yet --
the bug is specific to reusing a full HISTORICAL day's bars across
every opportunity in this offline script.

Fixed here by truncating qqq_bars/iwm_bars to only the bars at or
before each opportunity's own `entry_time` (`_bars_up_to`) before
calling `cross_market_confirmed` -- i.e. "Method 3" from the
Controller's own request: synchronize the comparison window to the
moment of the signal, not to the end of the day.

Also adds a second, independent probe ("Method 2"): among trades the
(now correctly time-synchronized) filter would PASS, how strong was
the QQQ/IWM confirmation -- measured as the move away from the
opening-range midpoint, normalized by that day's own opening-range
width (a volatility proxy that needs no extra data source)? Trades
are split into data-derived TERCILES of that strength (not an
arbitrary chosen cutoff), so the comparison is "does a stronger
confirmation predict a better trade" rather than "pick a threshold
and see if it works."

Run on the VM where the real .env is loaded:

  cd ~/Treading-bot-Claude && set -a && source .env && set +a && \\
  PYTHONPATH=src python3.11 scripts/spx0dte_measure_cross_market.py --start 2025-06-01 --end 2025-12-31
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from statistics import mean

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from spx0dte.alpaca_client import AlpacaDataError, AlpacaOptionsClient  # noqa: E402
from spx0dte.entry_filters import cross_market_confirmed, run_all_filters  # noqa: E402
from spx0dte.exit_logic import DailyPaceTracker, session_pace_width_pct, simulate_exit  # noqa: E402
from spx0dte.ratio_calibration import RatioProvider  # noqa: E402
from spx0dte.signal import find_breakout_opportunities  # noqa: E402

def _bars_to_signal_series(bars: list) -> list:
    return [{"datetime": b["t"], "spx": b["c"]} for b in bars]


def _parse_bar_time(bar: dict) -> datetime:
    return datetime.fromisoformat(bar["t"].replace("Z", "+00:00"))


def _bars_up_to(bars: list, cutoff: datetime) -> list:
    """Only bars at or before `cutoff` -- the fix for the lookahead
    bug: a historical day's bars include times after any given
    opportunity, and nothing in this module may use them for that
    opportunity's own filter decision."""
    return [b for b in bars if _parse_bar_time(b) <= cutoff]


def _confirmation_strength(qqq_bars: list, iwm_bars: list, opening_range_minutes: int = 15) -> float:
    """Average, across QQQ and IWM, of how far the latest (already
    time-synchronized) price sits from that symbol's own
    opening-range midpoint, normalized by that day's opening-range
    width -- e.g. 1.0 means the move was as wide as the whole first
    15 minutes' range. Returns 0.0 if either symbol's range is zero
    or data is too thin (caller should not use this on a trade with
    `cm.passed is False`, since sign isn't checked here)."""
    def _strength(bars: list) -> float:
        if len(bars) < 2:
            return 0.0
        orb_bars = bars[:max(1, opening_range_minutes)]
        hi = max(b["c"] for b in orb_bars)
        lo = min(b["c"] for b in orb_bars)
        width = hi - lo
        if width <= 0:
            return 0.0
        mid = (hi + lo) / 2
        return abs(bars[-1]["c"] - mid) / width

    return (_strength(qqq_bars) + _strength(iwm_bars)) / 2


def daterange(start: str, end: str):
    d0 = datetime.strptime(start, "%Y-%m-%d")
    d1 = datetime.strptime(end, "%Y-%m-%d")
    cur = d0
    while cur <= d1:
        if cur.weekday() < 5:
            yield cur.strftime("%Y-%m-%d")
        cur += timedelta(days=1)


def summarize(label: str, trades: list) -> None:
    n = len(trades)
    if n == 0:
        print(f"  {label}: n=0 (nothing to report)")
        return
    wins = [p for p in trades if p > 0]
    losses = [p for p in trades if p <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = round(gross_profit / gross_loss, 4) if gross_loss > 0 else float("inf")
    print(
        f"  {label}: n={n} total_pnl={sum(trades):+.2f} "
        f"win_rate={len(wins)/n:.2%} expectancy={mean(trades):+.4f} profit_factor={pf}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()

    key_id = os.environ.get("ALPACA_API_KEY_ID", "")
    secret_key = os.environ.get("ALPACA_API_SECRET_KEY", "")
    fred_key = os.environ.get("FRED_API_KEY", "")
    if not key_id or not secret_key or not fred_key:
        print("FATAL: ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY / FRED_API_KEY must all be set.")
        sys.exit(1)

    client = AlpacaOptionsClient(key_id=key_id, secret_key=secret_key)
    ratio_provider = RatioProvider(client=client, fred_api_key=fred_key)
    pace_tracker = DailyPaceTracker()

    dates = list(daterange(args.start, args.end))
    print(f"Measuring cross_market_confirmed over {len(dates)} weekdays, {args.start} to {args.end}...")

    would_reject_pnls: list = []
    would_pass_pnls: list = []
    # (pnl, confirmation_strength) for the would-pass subset, for the Method-2 tercile probe
    would_pass_with_strength: list = []
    skip_notes = 0

    for i, date in enumerate(dates):
        try:
            spy_bars = client.get_spy_minute_bars(date)
            qqq_bars = client.get_stock_minute_bars("QQQ", date)
            iwm_bars = client.get_stock_minute_bars("IWM", date)
        except AlpacaDataError:
            skip_notes += 1
            continue
        if not spy_bars:
            continue

        todays_pace = session_pace_width_pct(spy_bars, minutes=15)
        dynamic_cap = pace_tracker.record_and_get_cap(todays_pace)

        opportunities = find_breakout_opportunities(_bars_to_signal_series(spy_bars))
        for entry in opportunities:
            filter_results = run_all_filters(
                vix_level=None, entry_time=entry.entry_time, hold_cap_minutes=dynamic_cap,
            )
            if any(not r.passed for r in filter_results):
                continue

            ratio = ratio_provider.get_ratio(now=entry.entry_time)
            approx_spx = entry.price_at_entry * ratio
            call_put = "C" if entry.direction == "call" else "P"
            entry_time_iso = entry.entry_time.strftime("%Y-%m-%dT%H:%M:%SZ")

            found = client.find_nearest_traded_strike(date, approx_spx, call_put, around_time_iso=entry_time_iso)
            if found is None:
                continue
            strike, occ_symbol = found

            window_end = entry.entry_time + timedelta(minutes=int(dynamic_cap) + 5)
            try:
                bars = client.get_option_bars(
                    occ_symbol,
                    (entry.entry_time - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    window_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    limit=200,
                )
            except AlpacaDataError:
                continue
            if not bars:
                continue

            result = simulate_exit(bars, entry.entry_time, int(dynamic_cap))
            if result is None:
                continue

            entry_price = None
            for b in sorted(bars, key=lambda b: b["t"]):
                bt = datetime.fromisoformat(b["t"].replace("Z", "+00:00"))
                if bt >= entry.entry_time:
                    entry_price = b["c"]
                    break
            if entry_price is None:
                continue

            pnl = result.exit_price - entry_price

            # Fix for the lookahead bug: only bars up to this opportunity's
            # own entry_time, never the rest of the historical day.
            qqq_sync = _bars_up_to(qqq_bars, entry.entry_time)
            iwm_sync = _bars_up_to(iwm_bars, entry.entry_time)
            cm = cross_market_confirmed(entry.direction, qqq_sync, iwm_sync)
            if cm.passed:
                would_pass_pnls.append(pnl)
                would_pass_with_strength.append((pnl, _confirmation_strength(qqq_sync, iwm_sync)))
            else:
                would_reject_pnls.append(pnl)

        if (i + 1) % 20 == 0:
            print(f"  ...{i + 1}/{len(dates)} dates processed")

    print(f"\nFetch/data skips: {skip_notes}")
    print("\n=== RESULT (time-synchronized, lookahead bug fixed) ===")
    summarize("would_have_REJECTED by cross-market", would_reject_pnls)
    summarize("would_have_PASSED by cross-market", would_pass_pnls)
    print(
        "\nIf the rejected subset performs no worse than the passed subset,"
        "\ncross_market_confirmed has no real discriminating power (same"
        "\nconclusion as the volume filter) -- record either way in"
        "\ndocs/trading/decisions.md before deciding whether to wire it in."
    )

    print("\n=== METHOD 2 PROBE: does confirmation STRENGTH predict better, among the would-PASS subset? ===")
    if len(would_pass_with_strength) < 9:
        print(f"  n={len(would_pass_with_strength)} too thin to split into terciles -- skipped.")
    else:
        ranked = sorted(would_pass_with_strength, key=lambda pair: pair[1])
        third = len(ranked) // 3
        weak = [pnl for pnl, _ in ranked[:third]]
        mid = [pnl for pnl, _ in ranked[third:2 * third]]
        strong = [pnl for pnl, _ in ranked[2 * third:]]
        summarize("weakest tercile of confirmation strength", weak)
        summarize("middle tercile of confirmation strength", mid)
        summarize("strongest tercile of confirmation strength", strong)
        print(
            "\nIf profit_factor climbs weak -> mid -> strong, confirmation"
            "\nSTRENGTH (not just sign) adds real information -- evidence for"
            "\na magnitude-based version of this filter. If it's flat or"
            "\nnoisy, the sign-only version already captures the signal and"
            "\na magnitude threshold would only be added complexity."
        )


if __name__ == "__main__":
    main()
