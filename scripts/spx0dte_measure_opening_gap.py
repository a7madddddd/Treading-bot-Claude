"""Measures whether SPY's own opening gap (today's open vs yesterday's
close) predicts real 0DTE outcomes -- Controller's own request
(2026-11-23), "pro-level" improvement research, option 1 of the new
menu (opening gap / multi-timeframe ORB / SPY's own realized vol as an
independent feature -- NOT the already-rejected VIX-proxy use of it).

Checked against docs/trading/decisions.md before building (CLAUDE.md
§0.b): SPY realized volatility was tested ONLY as a candidate VIX-proxy
ingredient and rejected for THAT purpose (D-0095-A, "SPY-realized-vol
alone far worse"). Opening gap has never been measured for anything in
this project. No overlap with prior work.

Two things measured, same tag-and-compare methodology as every other
filter/feature study this project has run:

1. Gap MAGNITUDE: |today's open - yesterday's close| / yesterday's
   close, bucketed into TERCILES from this run's own distribution (not
   a fixed global cutoff). Every trade that day shares that day's gap
   value (it's a once-per-day feature, unlike breakout_magnitude which
   is per-trade). Tests: does a bigger/smaller opening gap predict
   better or worse 0DTE outcomes that day?

2. Gap DIRECTION alignment: on a day with a real (non-negligible) gap,
   is a trade in the SAME direction as the gap ("with the gap") better
   or worse than one AGAINST it? A null/flat-gap day's trades are kept
   in a separate bucket, not forced into either side.

The real current filter set (VIX range + economic calendar -- the
lunch-chop window was measured and disabled, D-0100 FINAL) is applied
before tagging, matching what actually reaches a live proposal today.
Includes the same permutation significance test built for D-0100/D-0101
(here testing the magnitude's high tercile and the "with the gap"
bucket, each against a null of "no real relationship").

Run on the VM where the real .env is loaded:

  cd ~/Treading-bot-Claude && set -a && source .env && set +a && \\
  PYTHONPATH=src python3.11 scripts/spx0dte_measure_opening_gap.py \\
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

SPY_SYMBOL = "SPY"
GAP_FLAT_THRESHOLD_PCT = 0.001  # 0.1% -- below this, a day counts as "flat", neither aligned nor against


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


def _vixy_price_as_of(vixy_minute_bars: list, cutoff: datetime) -> float:
    eligible = [b for b in vixy_minute_bars if _parse_bar_time(b) <= cutoff]
    if not eligible:
        return 0.0
    return sorted(eligible, key=lambda b: b["t"])[-1]["c"]


def estimate_vix_at(
    entry_time: datetime, yesterday_vix_close: float, yesterday_vixy_close: float,
    todays_vixy_minute_bars: list,
) -> float:
    vixy_now = _vixy_price_as_of(todays_vixy_minute_bars, entry_time)
    if vixy_now <= 0 or yesterday_vixy_close <= 0:
        return 0.0
    ratio = vixy_now / yesterday_vixy_close
    if ratio <= 0 or ratio > MAX_PLAUSIBLE_RATIO or ratio < 1.0 / MAX_PLAUSIBLE_RATIO:
        return 0.0
    return yesterday_vix_close * ratio


def tercile_buckets(tagged: list) -> dict:
    """`tagged`: list of (metric_value, pnl). Splits into terciles by
    this RUN's own metric distribution."""
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

    print(f"Fetching {VIXY_PROXY_SYMBOL} daily bars (for yesterday's VIX anchor)...")
    vixy_daily_bars = client.get_stock_daily_bars(VIXY_PROXY_SYMBOL, fetch_start, args.end)
    vixy_daily_closes = {b["t"][:10]: b["c"] for b in vixy_daily_bars}

    print(f"Fetching {SPY_SYMBOL} daily bars (for the opening gap itself)...")
    spy_daily_bars = sorted(client.get_stock_daily_bars(SPY_SYMBOL, fetch_start, args.end), key=lambda b: b["t"])
    spy_daily_by_date = {b["t"][:10]: b for b in spy_daily_bars}
    spy_daily_dates_sorted = sorted(spy_daily_by_date.keys())

    dates = list(daterange(args.start, args.end))
    print(f"Measuring opening-gap patterns over {len(dates)} weekdays, {args.start} to {args.end}...")

    magnitude_tagged: list = []   # (abs_gap_pct, pnl)
    aligned_pnls: list = []       # trade direction matches the day's gap direction
    against_pnls: list = []       # trade direction opposes the day's gap direction
    flat_day_pnls: list = []      # gap too small to call a direction
    skip_notes = 0
    no_gap_data = 0

    for i, date in enumerate(dates):
        prior_vix_dates = [d for d in sorted_vix_dates if d < date]
        if not prior_vix_dates:
            skip_notes += 1
            continue
        yesterday_vix = prior_vix_dates[-1]
        yesterday_vix_close = vix_closes.get(yesterday_vix)
        yesterday_vixy_close = vixy_daily_closes.get(yesterday_vix)
        if yesterday_vix_close is None or yesterday_vixy_close is None:
            skip_notes += 1
            continue

        # The opening gap itself: today's SPY open vs the PRIOR TRADING DAY's close,
        # both read from the real daily-bar series (handles weekends/holidays naturally).
        prior_spy_dates = [d for d in spy_daily_dates_sorted if d < date]
        today_spy_bar = spy_daily_by_date.get(date)
        if not prior_spy_dates or today_spy_bar is None:
            no_gap_data += 1
            continue
        prior_spy_bar = spy_daily_by_date[prior_spy_dates[-1]]
        prior_close = prior_spy_bar["c"]
        today_open = today_spy_bar["o"]
        if prior_close <= 0:
            no_gap_data += 1
            continue
        gap_pct = (today_open - prior_close) / prior_close
        gap_direction = "call" if gap_pct > GAP_FLAT_THRESHOLD_PCT else (
            "put" if gap_pct < -GAP_FLAT_THRESHOLD_PCT else None
        )

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
            magnitude_tagged.append((abs(gap_pct), pnl))

            if gap_direction is None:
                flat_day_pnls.append(pnl)
            elif entry.direction == gap_direction:
                aligned_pnls.append(pnl)
            else:
                against_pnls.append(pnl)

        if (i + 1) % 20 == 0:
            print(f"  ...{i + 1}/{len(dates)} dates processed")

    print(f"\nFetch/data skips: {skip_notes}, no opening-gap data: {no_gap_data}")

    print("\n=== (1) By |opening gap| tercile (computed from this run's own distribution) ===")
    buckets = tercile_buckets(magnitude_tagged)
    all_pnls = [pnl for _, pnl in magnitude_tagged]
    for bucket_name in ("low", "mid", "high"):
        summarize(f"|gap| {bucket_name} tercile", buckets[bucket_name])
    if args.permutations > 0 and buckets["high"]:
        p = permutation_p_value_better(all_pnls, buckets["high"], args.permutations, rng)
        print(
            f"    permutation p-value for the HIGH |gap| tercile being genuinely BETTER "
            f"(one-sided, {args.permutations} draws): {p:.4f}"
            f"{'  <-- NOT explainable by chance at the 1% level' if p < 0.01 else ''}"
        )

    print("\n=== (2) Trade direction vs opening-gap direction ===")
    summarize("WITH the gap (trade direction matches gap direction)", aligned_pnls)
    summarize("AGAINST the gap", against_pnls)
    summarize("flat-gap days (no clear direction)", flat_day_pnls)
    if args.permutations > 0 and aligned_pnls and (aligned_pnls + against_pnls + flat_day_pnls):
        all_directional = aligned_pnls + against_pnls + flat_day_pnls
        p = permutation_p_value_better(all_directional, aligned_pnls, args.permutations, rng)
        print(
            f"    permutation p-value for 'WITH the gap' being genuinely BETTER "
            f"(one-sided, {args.permutations} draws): {p:.4f}"
            f"{'  <-- NOT explainable by chance at the 1% level' if p < 0.01 else ''}"
        )

    print(
        "\nDESCRIPTIVE ONLY -- no filter change follows from this script by itself."
        "\nNeeds a low p-value AND agreement across independent periods before"
        "\nbeing proposed as a real filter, same standard as every other"
        "\nmeasurement in this project."
    )


if __name__ == "__main__":
    main()
