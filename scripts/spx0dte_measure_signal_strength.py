"""Measures whether a candidate's OWN signal strength at entry predicts
its real outcome -- Controller's own request (2026-11-23), "Option B"
of the pro-level improvement menu: size the trade by signal strength
instead of a fixed 1 contract always.

FACT this script exists to establish BEFORE any sizing change is
proposed, per this project's own rule ("calculate from recorded data,
never a guessed number"): nothing about position size changes from
this script alone. It only tags and compares -- exactly the same
methodology as every other filter/feature measurement in this module
(cross-market, VIX band, time-of-day).

Two candidate strength metrics, both derivable from `signal.EntrySignal`
alone (no new data source needed):

1. `breakout_magnitude` -- how far price_at_entry pushed beyond the
   ORB boundary, normalized by the ORB's own width (orb_high -
   orb_low), so it is comparable across days of different volatility:
     call: (price_at_entry - orb_high) / (orb_high - orb_low)
     put:  (orb_low - price_at_entry) / (orb_high - orb_low)
   A bigger push suggests more conviction behind the breakout.

2. `vwap_distance` -- how far price_at_entry sits from the running
   VWAP/TWAP proxy at entry, same normalization:
     |price_at_entry - vwap_at_entry| / (orb_high - orb_low)
   A bigger gap suggests a more decisive move away from the session's
   average price, not a marginal one.

Each is bucketed into TERCILES computed from that SAME run's own
distribution (not a fixed global threshold -- what counts as "strong"
should be relative to the period's own conditions, not an assumed
absolute number), then pnl/profit_factor/expectancy is compared across
the three buckets, plus a permutation significance test on the
top-tercile bucket (same sharpness standard the Controller required for
the time-of-day/day-of-week study, D-0100).

The REAL filter set this module currently runs (VIX range + economic
calendar -- the lunch-chop window was measured and disabled, D-0100
FINAL) is applied before tagging, so the candidate population here
matches what actually reaches a live proposal today.

Run on the VM where the real .env is loaded:

  cd ~/Treading-bot-Claude && set -a && source .env && set +a && \\
  PYTHONPATH=src python3.11 scripts/spx0dte_measure_signal_strength.py \\
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


def _vixy_price_as_of(vixy_minute_bars: list, cutoff: datetime) -> float:
    eligible = [b for b in vixy_minute_bars if _parse_bar_time(b) <= cutoff]
    if not eligible:
        return 0.0
    return sorted(eligible, key=lambda b: b["t"])[-1]["c"]


def estimate_vix_at(
    entry_time: datetime, yesterday_vix_close: float, yesterday_vixy_close: float,
    todays_vixy_minute_bars: list,
) -> float:
    """Lookahead-safe (same shape as spx0dte_measure_vix_filter.py's
    own helper) -- the live VIXY price is truncated to entry_time."""
    vixy_now = _vixy_price_as_of(todays_vixy_minute_bars, entry_time)
    if vixy_now <= 0 or yesterday_vixy_close <= 0:
        return 0.0
    ratio = vixy_now / yesterday_vixy_close
    if ratio <= 0 or ratio > MAX_PLAUSIBLE_RATIO or ratio < 1.0 / MAX_PLAUSIBLE_RATIO:
        return 0.0
    return yesterday_vix_close * ratio


def breakout_magnitude(entry: EntrySignal) -> float:
    orb_width = entry.orb_high - entry.orb_low
    if orb_width <= 0:
        return 0.0
    if entry.direction == "call":
        return (entry.price_at_entry - entry.orb_high) / orb_width
    return (entry.orb_low - entry.price_at_entry) / orb_width


def vwap_distance(entry: EntrySignal) -> float:
    orb_width = entry.orb_high - entry.orb_low
    if orb_width <= 0:
        return 0.0
    return abs(entry.price_at_entry - entry.vwap_at_entry) / orb_width


def tercile_buckets(tagged: list) -> dict:
    """`tagged`: list of (metric_value, pnl). Splits into terciles by
    this RUN's own metric distribution (not a fixed global cutoff)."""
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
    with a mean AT LEAST AS GOOD as the subgroup's -- used here on the
    "high" bucket, testing whether it is genuinely BETTER than chance,
    not worse (opposite direction from the time-of-day study, which
    tested for a bucket being worse)."""
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
    print(f"Measuring signal-strength patterns over {len(dates)} weekdays, {args.start} to {args.end}...")

    magnitude_tagged: list = []  # (breakout_magnitude, pnl)
    vwap_tagged: list = []       # (vwap_distance, pnl)
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
            magnitude_tagged.append((breakout_magnitude(entry), pnl))
            vwap_tagged.append((vwap_distance(entry), pnl))

        if (i + 1) % 20 == 0:
            print(f"  ...{i + 1}/{len(dates)} dates processed")

    print(f"\nFetch/data skips: {skip_notes}")

    for label, tagged in (("breakout_magnitude", magnitude_tagged), ("vwap_distance", vwap_tagged)):
        print(f"\n=== By {label} tercile (computed from this run's own distribution) ===")
        buckets = tercile_buckets(tagged)
        all_pnls = [pnl for _, pnl in tagged]
        for bucket_name in ("low", "mid", "high"):
            summarize(f"{label} {bucket_name} tercile", buckets[bucket_name])
        if args.permutations > 0 and buckets["high"]:
            p = permutation_p_value(all_pnls, buckets["high"], args.permutations, rng)
            print(
                f"    permutation p-value for the HIGH tercile being genuinely BETTER "
                f"(one-sided, {args.permutations} draws): {p:.4f}"
                f"{'  <-- NOT explainable by chance at the 1% level' if p < 0.01 else ''}"
            )

    print(
        "\nDESCRIPTIVE ONLY -- no sizing change follows from this script by itself."
        "\nA real sizing rule needs a low p-value on the high tercile AND agreement"
        "\nacross independent periods (same standard just applied to the"
        "\ntime-of-day/day-of-week study, D-0100) before being proposed."
    )


if __name__ == "__main__":
    main()
