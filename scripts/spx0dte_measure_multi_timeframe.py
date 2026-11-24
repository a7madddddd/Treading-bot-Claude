"""Measures whether a breakout that also holds on a SLOWER timeframe
predicts a better real outcome -- Option 2 of the pro-level improvement
menu the Controller chose from (2026-11-23), "multi-timeframe ORB
confirmation".

FACT this script only tags and compares, per this project's own rule
("calculate from recorded data, never a guessed number"): nothing about
entry logic changes from this script alone. Same discipline as every
other filter/feature measurement in this module (signal-strength,
opening-gap, time-of-day).

Current production signal (`signal.find_breakout_opportunities`,
D-0095) detects a breakout on a SINGLE 1-minute print crossing the ORB
boundary, confirmed only by a TWAP/VWAP-side check on that same print.
It never checks whether the move holds on a slower timeframe. The
"multi-timeframe confirmation" idea: a breakout that is still outside
the ORB range on the most recently CLOSED 5-minute bar is more likely a
genuine move than one where the very next few 1-minute prints snap
back inside the range.

`multi_tf_confirmed(entry, five_min_bars)`:
  - finds the last 5-minute bar whose bucket is fully CLOSED strictly
    before `entry.entry_time` (lookahead-safe: never uses a bar that
    includes or follows the entry's own minute),
  - confirmed = that bar's CLOSE is on the breakout side of the ORB
    boundary, same direction as the entry signal.
  - returns None (excluded from tagging, not forced False) when no
    completed 5-minute bar exists yet (can happen for very early
    breakouts, e.g. inside the first 5-minute bucket after the ORB
    window) -- an undefined confirmation state is not the same claim as
    "not confirmed".

5-minute bars are DERIVED from the same `get_spy_minute_bars` 1-minute
series already fetched for ORB detection -- no new data source, no new
Alpaca symbol or feed.

Tags each real candidate (full real filter chain already applied, same
population that reaches a live proposal today) with confirmed/not
confirmed/undefined, compares pnl/profit_factor/expectancy, plus a
permutation significance test on the CONFIRMED bucket being genuinely
better (same one-sided test used for D-0101's high tercile).

Run on the VM where the real .env is loaded:

  cd ~/Treading-bot-Claude && set -a && source .env && set +a && \\
  PYTHONPATH=src python3.11 scripts/spx0dte_measure_multi_timeframe.py \\
      --start 2025-06-01 --end 2025-12-31 --permutations 10000
"""
from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import datetime, timedelta
from statistics import mean

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from spx0dte.alpaca_client import AlpacaDataError, AlpacaOptionsClient  # noqa: E402
from spx0dte.entry_filters import economic_calendar_clear, vix_in_tradeable_range  # noqa: E402
from spx0dte.exit_logic import DailyPaceTracker, session_pace_width_pct, simulate_exit  # noqa: E402
from spx0dte.ratio_calibration import RatioProvider  # noqa: E402
from spx0dte.signal import EntrySignal, find_breakout_opportunities  # noqa: E402
from spx0dte.vix_proxy import MAX_PLAUSIBLE_RATIO, VIXY_PROXY_SYMBOL, fetch_vixcls_closes  # noqa: E402


def _bars_to_signal_series(bars: list) -> list:
    return [{"datetime": b["t"], "spx": b["c"]} for b in bars]


def _parse_bar_time(bar: dict) -> datetime:
    return datetime.fromisoformat(bar["t"].replace("Z", "+00:00"))


def daterange(start: str, end: str):
    d0 = datetime.strptime(start, "%Y-%m-%d")
    d1 = datetime.strptime(end, "%Y-%m-%d")
    cur = d0
    while cur <= d1:
        if cur.weekday() < 5:
            yield cur.strftime("%Y-%m-%d")
        cur += timedelta(days=1)


def aggregate_bars(one_min_bars: list, minutes: int = 5) -> list:
    """Buckets 1-minute OHLC bars into `minutes`-wide bars, anchored to
    the FIRST bar's timestamp (the session open), not to a fixed clock
    boundary -- matches how the ORB window itself is anchored
    (`signal.find_breakout_opportunities`: "first tick", not 09:30:00
    exactly, in case of thin pre-open prints)."""
    if not one_min_bars:
        return []
    sorted_bars = sorted(one_min_bars, key=lambda b: b["t"])
    session_start = _parse_bar_time(sorted_bars[0])
    buckets: dict = {}
    for b in sorted_bars:
        t = _parse_bar_time(b)
        idx = int((t - session_start).total_seconds() // (minutes * 60))
        bucket = buckets.setdefault(idx, [])
        bucket.append(b)

    out = []
    for idx in sorted(buckets):
        chunk = sorted(buckets[idx], key=lambda b: b["t"])
        bucket_end = session_start + timedelta(minutes=minutes * (idx + 1))
        out.append({
            "bucket_end": bucket_end,
            "o": chunk[0]["o"],
            "h": max(b["h"] for b in chunk),
            "l": min(b["l"] for b in chunk),
            "c": chunk[-1]["c"],
        })
    return out


def multi_tf_confirmed(entry: EntrySignal, five_min_bars: list) -> "bool | None":
    """None = undefined (no completed 5-minute bar strictly before
    entry_time exists yet) -- excluded from tagging, never forced
    False. Confirmed = the last fully-closed 5-minute bar's CLOSE is
    still on the breakout side of the (1-minute-detected) ORB
    boundary."""
    completed = [b for b in five_min_bars if b["bucket_end"] <= entry.entry_time]
    if not completed:
        return None
    last_closed = completed[-1]
    if entry.direction == "call":
        return last_closed["c"] > entry.orb_high
    return last_closed["c"] < entry.orb_low


def permutation_p_value(all_pnls: list, subgroup_pnls: list, iterations: int, rng: random.Random) -> float:
    """One-sided: fraction of random same-size draws from `all_pnls`
    with a mean AT LEAST AS GOOD as the subgroup's."""
    n = len(subgroup_pnls)
    if n == 0 or n >= len(all_pnls) or iterations <= 0:
        return 1.0
    observed_mean = mean(subgroup_pnls)
    at_least_as_good = 0
    for _ in range(iterations):
        draw = rng.sample(all_pnls, n)
        if mean(draw) >= observed_mean:
            at_least_as_good += 1
    return at_least_as_good / iterations


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


def estimate_vix_at(
    entry_time: datetime, yesterday_vix_close: float, yesterday_vixy_close: float,
    todays_vixy_minute_bars: list,
) -> float:
    eligible = [b for b in todays_vixy_minute_bars if _parse_bar_time(b) <= entry_time]
    if not eligible:
        return 0.0
    vixy_now = sorted(eligible, key=lambda b: b["t"])[-1]["c"]
    if vixy_now <= 0 or yesterday_vixy_close <= 0:
        return 0.0
    ratio = vixy_now / yesterday_vixy_close
    if ratio <= 0 or ratio > MAX_PLAUSIBLE_RATIO or ratio < 1.0 / MAX_PLAUSIBLE_RATIO:
        return 0.0
    return yesterday_vix_close * ratio


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--permutations", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()
    rng = random.Random(args.seed)

    key_id = os.environ.get("ALPACA_API_KEY_ID", "")
    secret_key = os.environ.get("ALPACA_API_SECRET_KEY", "")
    fred_key = os.environ.get("FRED_API_KEY", "")
    if not key_id or not secret_key or not fred_key:
        print("FATAL: ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY / FRED_API_KEY must all be set.")
        sys.exit(1)

    client = AlpacaOptionsClient(key_id=key_id, secret_key=secret_key)
    ratio_provider = RatioProvider(client=client, fred_api_key=fred_key)
    pace_tracker = DailyPaceTracker()

    fetch_start = (datetime.strptime(args.start, "%Y-%m-%d") - timedelta(days=10)).strftime("%Y-%m-%d")
    print("Fetching FRED VIXCLS...")
    vix_closes = fetch_vixcls_closes(fred_key, fetch_start, args.end)
    sorted_vix_dates = sorted(vix_closes.keys())

    print(f"Fetching {VIXY_PROXY_SYMBOL} daily bars (for yesterday's anchor)...")
    vixy_daily_bars = client.get_stock_daily_bars(VIXY_PROXY_SYMBOL, fetch_start, args.end)
    vixy_daily_closes = {b["t"][:10]: b["c"] for b in vixy_daily_bars}

    dates = list(daterange(args.start, args.end))
    print(f"Measuring multi-timeframe confirmation over {len(dates)} weekdays, {args.start} to {args.end}...")

    confirmed_pnls: list = []
    unconfirmed_pnls: list = []
    undefined_count = 0
    skip_notes = 0

    for i, date in enumerate(dates):
        prior_dates = [d for d in sorted_vix_dates if d < date]
        if not prior_dates:
            skip_notes += 1
            continue
        yesterday = prior_dates[-1]
        yesterday_vix_close = vix_closes.get(yesterday)
        yesterday_vixy_close = vixy_daily_closes.get(yesterday)
        if yesterday_vix_close is None or yesterday_vixy_close is None:
            skip_notes += 1
            continue

        try:
            spy_bars = client.get_spy_minute_bars(date)
            vixy_bars_today = client.get_stock_minute_bars(VIXY_PROXY_SYMBOL, date)
        except AlpacaDataError:
            skip_notes += 1
            continue
        if not spy_bars:
            continue

        todays_pace = session_pace_width_pct(spy_bars, minutes=15)
        dynamic_cap = pace_tracker.record_and_get_cap(todays_pace)

        five_min_bars = aggregate_bars(spy_bars, minutes=5)
        opportunities = find_breakout_opportunities(_bars_to_signal_series(spy_bars))
        for entry in opportunities:
            econ_result = economic_calendar_clear(entry.entry_time, dynamic_cap)
            if not econ_result.passed:
                continue

            estimated_vix = estimate_vix_at(
                entry.entry_time, yesterday_vix_close, yesterday_vixy_close, vixy_bars_today,
            )
            if estimated_vix <= 0:
                continue
            if not vix_in_tradeable_range(estimated_vix).passed:
                continue

            confirmation = multi_tf_confirmed(entry, five_min_bars)
            if confirmation is None:
                undefined_count += 1
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
            if confirmation:
                confirmed_pnls.append(pnl)
            else:
                unconfirmed_pnls.append(pnl)

        if (i + 1) % 20 == 0:
            print(f"  ...{i + 1}/{len(dates)} dates processed")

    print(f"\nFetch/data skips: {skip_notes}")
    print(f"Undefined confirmation (no completed 5-min bar yet): {undefined_count}")

    all_pnls = confirmed_pnls + unconfirmed_pnls
    print("\n=== Multi-timeframe (5-minute) breakout confirmation ===")
    summarize("CONFIRMED (5-min bar still outside ORB range)", confirmed_pnls)
    summarize("NOT confirmed (5-min bar snapped back inside)", unconfirmed_pnls)
    if args.permutations > 0 and confirmed_pnls and all_pnls:
        p = permutation_p_value(all_pnls, confirmed_pnls, args.permutations, rng)
        print(
            f"\n  permutation p-value for CONFIRMED being genuinely BETTER "
            f"(one-sided, {args.permutations} draws): {p:.4f}"
            f"{'  <-- NOT explainable by chance at the 1% level' if p < 0.01 else ''}"
        )

    print(
        "\nDESCRIPTIVE ONLY -- no entry-logic change follows from this script by itself."
        "\nA real confirmation filter needs a low p-value AND agreement across"
        "\nindependent periods (same standard already applied to D-0100/D-0101/"
        "\nD-0102/D-0103) before being proposed."
    )


if __name__ == "__main__":
    main()
