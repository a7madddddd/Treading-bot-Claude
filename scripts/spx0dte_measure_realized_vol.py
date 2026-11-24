"""Measures whether SPY's own realized volatility SO FAR today (at the
moment of entry) predicts real 0DTE outcomes -- Option 3 of the
pro-level improvement menu (opening gap / multi-timeframe ORB /
SPY's own realized vol as an INDEPENDENT feature).

Checked against docs/trading/decisions.md before building (CLAUDE.md
§0.b): SPY realized volatility was already measured ONCE in this
project, but only as a candidate VIX-PROXY ingredient
(`scripts/spx0dte_measure_vix_proxy.py`), and was REJECTED for that
purpose (mean abs error 23.7% vs real VIX, strong positive bias --
`src/spx0dte/vix_proxy.py`'s own docstring). That measured "does
SPY's realized-vol RATIO track VIX's ratio" -- a proxy-accuracy
question. This script asks a DIFFERENT question: regardless of
whether it tracks VIX, does today's realized vol, by itself, predict
whether a 0DTE trade taken right now does better or worse? No overlap
with the rejected finding -- same raw `realized_vol` arithmetic
(stdev of 1-minute log returns), different use.

Also distinct from `exit_logic.session_pace_width_pct` (the existing
day-pace metric that sets the exit hold-time cap): that is a RANGE
(high-low) over a FROZEN first-15-minutes window, computed once per
day. This feature is a STANDARD DEVIATION of log returns, computed
CONTINUOUSLY from session start up to each trade's own entry_time
(lookahead-safe: never uses a bar at or after entry_time) -- it grows
and changes through the day, unlike the frozen pace metric. No
overlap with existing production logic.

Hypothesis: a long-options (long-gamma) 0DTE strategy should do BETTER
when the underlying is genuinely moving (higher realized vol so far),
since the position profits from the SIZE of the move, not just
direction. Tested the same way as every other feature in this module:
tercile buckets from this run's OWN distribution, pnl/profit_factor/
expectancy compared, one-sided permutation significance test on the
HIGH tercile (the hypothesized better bucket).

The REAL filter set this module currently runs (VIX range + economic
calendar) is applied before tagging, so the candidate population
matches what actually reaches a live proposal today.

Run on the VM where the real .env is loaded:

  cd ~/Treading-bot-Claude && set -a && source .env && set +a && \\
  PYTHONPATH=src python3.11 scripts/spx0dte_measure_realized_vol.py \\
      --start 2025-06-01 --end 2025-12-31 --permutations 10000
"""
from __future__ import annotations

import argparse
import math
import os
import random
import sys
from datetime import datetime, timedelta
from statistics import mean, pstdev

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


def realized_vol_so_far(day_bars: list, cutoff: datetime) -> float:
    """Stdev of 1-minute log returns from session start up to (not
    including) `cutoff` -- lookahead-safe, same raw arithmetic as
    `spx0dte_measure_vix_proxy.realized_vol`, just truncated to a
    point in time instead of a whole day. No annualization constant --
    it cancels in a same-unit comparison across trades."""
    eligible = [b for b in day_bars if _parse_bar_time(b) < cutoff]
    closes = [b["c"] for b in sorted(eligible, key=lambda b: b["t"])]
    if len(closes) < 3:
        return 0.0
    log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(log_returns) < 2:
        return 0.0
    return pstdev(log_returns)


def tercile_buckets(tagged: list) -> dict:
    """`tagged`: list of (metric_value, pnl). Terciles from THIS run's
    own distribution, not a fixed global cutoff."""
    if len(tagged) < 3:
        return {"low": [], "mid": [], "high": []}
    ordered = sorted(tagged, key=lambda t: t[0])
    n = len(ordered)
    low_cut = n // 3
    high_cut = 2 * n // 3
    return {
        "low": [pnl for _, pnl in ordered[:low_cut]],
        "mid": [pnl for _, pnl in ordered[low_cut:high_cut]],
        "high": [pnl for _, pnl in ordered[high_cut:]],
    }


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
    print(f"Measuring realized-vol-so-far over {len(dates)} weekdays, {args.start} to {args.end}...")

    tagged: list = []  # (realized_vol_so_far, pnl)
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
            vol_so_far = realized_vol_so_far(spy_bars, entry.entry_time)
            tagged.append((vol_so_far, pnl))

        if (i + 1) % 20 == 0:
            print(f"  ...{i + 1}/{len(dates)} dates processed")

    print(f"\nFetch/data skips: {skip_notes}")

    print("\n=== By realized-vol-so-far tercile (computed from this run's own distribution) ===")
    buckets = tercile_buckets(tagged)
    all_pnls = [pnl for _, pnl in tagged]
    for bucket_name in ("low", "mid", "high"):
        summarize(f"realized_vol_so_far {bucket_name} tercile", buckets[bucket_name])
    if args.permutations > 0 and buckets["high"]:
        p = permutation_p_value(all_pnls, buckets["high"], args.permutations, rng)
        print(
            f"\n  permutation p-value for the HIGH tercile being genuinely BETTER "
            f"(one-sided, {args.permutations} draws): {p:.4f}"
            f"{'  <-- NOT explainable by chance at the 1% level' if p < 0.01 else ''}"
        )

    print(
        "\nDESCRIPTIVE ONLY -- no entry/sizing change follows from this script by itself."
        "\nA real rule needs a low p-value AND agreement across independent periods"
        "\n(same standard already applied to D-0100/D-0101/D-0102/D-0103/D-0104)"
        "\nbefore being proposed."
    )


if __name__ == "__main__":
    main()
