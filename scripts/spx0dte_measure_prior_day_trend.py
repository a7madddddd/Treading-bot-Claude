"""Measures whether YESTERDAY's own full-day direction/return predicts
real 0DTE outcomes TODAY -- Option 3 of the post-mortem brainstorm
(2026-11-25), "yesterday's full-day direction as a momentum/reversion
feature".

Checked against this project's history first (CLAUDE.md §0.b): D-0103
(opening gap) measured today's OPEN vs yesterday's CLOSE -- an
overnight-gap question. This measures yesterday's CLOSE vs the day
BEFORE yesterday's CLOSE -- yesterday's own whole-session return, a
different number entirely (it says nothing about where today opens).
No overlap: a day can have a big gap and a flat prior-day return, or
the reverse.

Same two-part structure as D-0103, since the question shape is the
same ("does a daily-bar feature from before today predict today's
trade outcome"):

1. |yesterday's return| magnitude tercile vs pnl -- does a bigger
   prior-day move (either direction) predict a better/worse 0DTE
   outcome today, permutation-tested on the high tercile.
2. Trade direction vs yesterday's direction: "WITH yesterday's trend"
   (today's trade direction matches yesterday's close-to-close sign)
   vs "AGAINST" vs "flat prior day" (below threshold, kept separate,
   not forced into either side). Tests momentum-continuation (trend
   persists into today) against mean-reversion (today reverses
   yesterday) without assuming either in advance.

The real filter set (VIX range + economic calendar) is applied before
tagging, matching what actually reaches a live proposal today.

Run on the VM where the real .env is loaded:

  cd ~/Treading-bot-Claude && set -a && source .env && set +a && \\
  PYTHONPATH=src python3.11 scripts/spx0dte_measure_prior_day_trend.py \\
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
TREND_FLAT_THRESHOLD_PCT = 0.001  # 0.1% -- below this, yesterday counts as "flat", same threshold as D-0103's gap


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

    print(f"Fetching {SPY_SYMBOL} daily bars (for yesterday's own close-to-close return)...")
    spy_daily_bars = sorted(client.get_stock_daily_bars(SPY_SYMBOL, fetch_start, args.end), key=lambda b: b["t"])
    spy_daily_by_date = {b["t"][:10]: b for b in spy_daily_bars}
    spy_daily_dates_sorted = sorted(spy_daily_by_date.keys())

    dates = list(daterange(args.start, args.end))
    print(f"Measuring prior-day-trend patterns over {len(dates)} weekdays, {args.start} to {args.end}...")

    magnitude_tagged: list = []   # (abs_prior_return_pct, pnl)
    aligned_pnls: list = []       # today's trade direction matches yesterday's close-to-close direction
    against_pnls: list = []       # today's trade direction opposes yesterday's direction
    flat_day_pnls: list = []      # yesterday's return too small to call a direction
    skip_notes = 0
    no_trend_data = 0

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

        # Yesterday's OWN close-to-close return: yesterday's close vs the
        # day BEFORE yesterday's close -- both read from the real daily-bar
        # series. Distinct from the opening gap (today's open vs yesterday's
        # close): this says nothing about where today opens.
        prior_spy_dates = [d for d in spy_daily_dates_sorted if d < date]
        if len(prior_spy_dates) < 2:
            no_trend_data += 1
            continue
        yesterday_date, day_before_date = prior_spy_dates[-1], prior_spy_dates[-2]
        yesterday_close = spy_daily_by_date[yesterday_date]["c"]
        day_before_close = spy_daily_by_date[day_before_date]["c"]
        if day_before_close <= 0:
            no_trend_data += 1
            continue
        prior_return_pct = (yesterday_close - day_before_close) / day_before_close
        trend_direction = "call" if prior_return_pct > TREND_FLAT_THRESHOLD_PCT else (
            "put" if prior_return_pct < -TREND_FLAT_THRESHOLD_PCT else None
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
            magnitude_tagged.append((abs(prior_return_pct), pnl))

            if trend_direction is None:
                flat_day_pnls.append(pnl)
            elif entry.direction == trend_direction:
                aligned_pnls.append(pnl)
            else:
                against_pnls.append(pnl)

        if (i + 1) % 20 == 0:
            print(f"  ...{i + 1}/{len(dates)} dates processed")

    print(f"\nFetch/data skips: {skip_notes}, no prior-day-trend data: {no_trend_data}")

    print("\n=== (1) By |yesterday's return| tercile (computed from this run's own distribution) ===")
    buckets = tercile_buckets(magnitude_tagged)
    all_pnls = [pnl for _, pnl in magnitude_tagged]
    for bucket_name in ("low", "mid", "high"):
        summarize(f"|prior return| {bucket_name} tercile", buckets[bucket_name])
    if args.permutations > 0 and buckets["high"]:
        p = permutation_p_value_better(all_pnls, buckets["high"], args.permutations, rng)
        print(
            f"    permutation p-value for the HIGH |prior return| tercile being genuinely BETTER "
            f"(one-sided, {args.permutations} draws): {p:.4f}"
            f"{'  <-- NOT explainable by chance at the 1% level' if p < 0.01 else ''}"
        )

    print("\n=== (2) Trade direction vs yesterday's own close-to-close direction ===")
    summarize("WITH yesterday's trend (continuation)", aligned_pnls)
    summarize("AGAINST yesterday's trend (reversion)", against_pnls)
    summarize("flat prior day (no clear direction)", flat_day_pnls)
    if args.permutations > 0 and aligned_pnls and (aligned_pnls + against_pnls + flat_day_pnls):
        all_directional = aligned_pnls + against_pnls + flat_day_pnls
        p = permutation_p_value_better(all_directional, aligned_pnls, args.permutations, rng)
        print(
            f"    permutation p-value for 'WITH yesterday's trend' being genuinely BETTER "
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
