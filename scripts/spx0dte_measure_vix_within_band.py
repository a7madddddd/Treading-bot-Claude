"""Measures whether VIX LEVEL, as a continuous feature WITHIN the
already-passing 12-35 tradeable band, predicts real 0DTE outcomes --
Option 1 of the new post-mortem brainstorm (2026-11-25).

Checked against this project's own history first (CLAUDE.md §0.b):
`entry_filters.vix_in_tradeable_range` only ever tested PASS/FAIL (is
VIX inside 12-35 or not) -- a binary gate, never measured as a
continuous value among the trades that already pass it. No prior
script in this project buckets VIX level itself into terciles. No
overlap with D-0100 (time-of-day), D-0101 (signal strength), D-0103
(opening gap), D-0104 (multi-timeframe), or D-0105 (realized vol).

Hypothesis: the 12-35 band itself survived every measurement pass this
project has run, so refining it from the inside (rather than adding an
unrelated new feature) starts from the one thing already shown to
matter. Low-VIX trades (closer to a dead/no-movement market) might
underperform high-VIX trades (more genuine movement, more room for a
0DTE long option to profit) even within the "acceptable" band -- or
the opposite could hold if VIX near the top of the band means choppier,
less directional moves.

Tags each real candidate (full real filter chain already applied --
VIX range + economic calendar) with its own estimated VIX level,
tercile buckets from this run's OWN distribution, pnl/profit_factor/
expectancy compared across buckets, plus a permutation significance
test. No directional assumption is baked into which tercile is
"expected" to win -- both HIGH and LOW tercile p-values are reported,
same descriptive spirit as D-0100's day-of-week study.

Run on the VM where the real .env is loaded:

  cd ~/Treading-bot-Claude && set -a && source .env && set +a && \\
  PYTHONPATH=src python3.11 scripts/spx0dte_measure_vix_within_band.py \\
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
from spx0dte.signal import find_breakout_opportunities  # noqa: E402
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


def permutation_p_value_better(all_pnls: list, subgroup_pnls: list, iterations: int, rng: random.Random) -> float:
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
    print(f"Measuring VIX-within-band over {len(dates)} weekdays, {args.start} to {args.end}...")

    tagged: list = []  # (vix_level, pnl) -- only for candidates that PASSED the 12-35 filter
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
            vix_result = vix_in_tradeable_range(estimated_vix)
            if not vix_result.passed:
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
            tagged.append((estimated_vix, pnl))

        if (i + 1) % 20 == 0:
            print(f"  ...{i + 1}/{len(dates)} dates processed")

    print(f"\nFetch/data skips: {skip_notes}")

    print("\n=== By VIX-within-band tercile (computed from this run's own distribution) ===")
    buckets = tercile_buckets(tagged)
    all_pnls = [pnl for _, pnl in tagged]
    vix_values = [v for v, _ in tagged]
    if vix_values:
        print(f"  VIX range observed in this sample: {min(vix_values):.1f} - {max(vix_values):.1f}")
    for bucket_name in ("low", "mid", "high"):
        summarize(f"VIX {bucket_name} tercile", buckets[bucket_name])
    if args.permutations > 0 and all_pnls:
        if buckets["high"]:
            p_high = permutation_p_value_better(all_pnls, buckets["high"], args.permutations, rng)
            print(
                f"\n  permutation p-value for the HIGH VIX tercile being genuinely BETTER "
                f"(one-sided, {args.permutations} draws): {p_high:.4f}"
                f"{'  <-- NOT explainable by chance at the 1% level' if p_high < 0.01 else ''}"
            )
        if buckets["low"]:
            p_low = permutation_p_value_better(all_pnls, buckets["low"], args.permutations, rng)
            print(
                f"  permutation p-value for the LOW VIX tercile being genuinely BETTER "
                f"(one-sided, {args.permutations} draws): {p_low:.4f}"
                f"{'  <-- NOT explainable by chance at the 1% level' if p_low < 0.01 else ''}"
            )

    print(
        "\nDESCRIPTIVE ONLY -- no filter change follows from this script by itself."
        "\nA real rule needs a low p-value AND agreement across independent periods"
        "\n(same standard already applied to D-0100 through D-0105) before being"
        "\nproposed."
    )


if __name__ == "__main__":
    main()
