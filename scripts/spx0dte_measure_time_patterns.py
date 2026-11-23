"""Measures whether SPX 0DTE performance actually varies by time of day
or day of week, against real historical data -- Controller's own
request (2026-11-23): "we need to improve our option trades... search
for new filters."

Two things measured in ONE pass over the same backtest data (cheaper
than two separate runs, same candidates underlie both):

1. Validates `entry_filters.time_quality_window`'s EXISTING 12:00-13:30 ET
   "lunch chop" bootstrap window -- it has never been measured against
   this project's own data either (its own docstring: "Bootstrap
   window... good enough for a filter, not for anything safety-critical").
   Same would-reject-vs-would-pass tag-and-compare methodology as every
   other D-0095/D-0095-A filter measurement (cross-market, VIX band).

2. A broader, open-ended breakdown: profit_factor/expectancy/win_rate
   by ET entry hour and by day-of-week, independent of the existing
   filter's specific 12:00-13:30 boundary -- genuinely new, not yet
   tried per docs/trading/decisions.md.

STATISTICAL SHARPNESS (Controller's own instruction, 2026-11-23): two
independent periods agreeing on DIRECTION is suggestive, not proof --
it can happen by chance, especially with a modest sample. A
permutation test makes the decision sharp and numeric instead of "it
agreed twice": for each hour bucket, draw many random same-size subsets
of ALL that period's trade pnls and ask what fraction of purely random
draws are at least as bad as the real hour's mean pnl. A small fraction
(the reported p-value) means the hour's result is NOT explainable by
chance alone. `--permutations` controls how many random draws (default
10,000); 0 disables the test.

METHODOLOGY NOTES:
- Uses `zoneinfo.ZoneInfo("America/New_York")` for the REAL ET
  conversion, not the module's own rough DST approximation
  ((3,15)-(11,1)) -- deliberately more precise here than the filter
  being measured, so an hour-bucket boundary effect near a real DST
  transition isn't an artifact of the approximation itself (same class
  of bug just fixed in D-0099, applied here from the start rather than
  discovered the hard way a second time).
- VIX is deliberately NOT part of this study and is NOT fetched --
  `economic_calendar_clear` is the only OTHER filter applied before
  tagging (matches `run_all_filters`'s set minus VIX and minus
  `time_quality_window` itself, which is the one being measured).
- Lookahead-safe by construction: this script never looks at bars
  after `entry.entry_time` plus the dynamic hold-cap window.

Run on the VM where the real .env is loaded:

  cd ~/Treading-bot-Claude && set -a && source .env && set +a && \\
  PYTHONPATH=src python3.11 scripts/spx0dte_measure_time_patterns.py \\
      --start 2025-06-01 --end 2025-12-31 --permutations 10000
"""
from __future__ import annotations

import argparse
import os
import random
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean
from zoneinfo import ZoneInfo

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from spx0dte.alpaca_client import AlpacaDataError, AlpacaOptionsClient  # noqa: E402
from spx0dte.entry_filters import economic_calendar_clear, time_quality_window  # noqa: E402
from spx0dte.exit_logic import DailyPaceTracker, session_pace_width_pct, simulate_exit  # noqa: E402
from spx0dte.ratio_calibration import RatioProvider  # noqa: E402
from spx0dte.signal import find_breakout_opportunities  # noqa: E402

_ET = ZoneInfo("America/New_York")
_WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _bars_to_signal_series(bars: list) -> list:
    return [{"datetime": b["t"], "spx": b["c"]} for b in bars]


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


def permutation_p_value(all_pnls: list, subgroup_pnls: list, iterations: int, rng: random.Random) -> float:
    """One-sided: what fraction of random same-size draws from
    `all_pnls` have a mean at or below `subgroup_pnls`'s real mean?
    Small (e.g. < 0.01) means the subgroup's bad result is NOT
    explainable by chance alone -- a sharp, numeric answer instead of
    "it agreed across periods," which can happen by chance. Returns
    1.0 (no evidence) if there's nothing to compare."""
    n = len(subgroup_pnls)
    if n == 0 or n >= len(all_pnls) or iterations <= 0:
        return 1.0
    observed_mean = mean(subgroup_pnls)
    at_least_as_bad = 0
    for _ in range(iterations):
        draw = rng.sample(all_pnls, n)
        if mean(draw) <= observed_mean:
            at_least_as_bad += 1
    return at_least_as_bad / iterations


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument(
        "--permutations", type=int, default=10_000,
        help="random draws for the per-hour significance test (0 disables it)",
    )
    parser.add_argument("--seed", type=int, default=None, help="RNG seed, for a reproducible run")
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

    dates = list(daterange(args.start, args.end))
    print(f"Measuring time-of-day / day-of-week patterns over {len(dates)} weekdays, {args.start} to {args.end}...")

    would_reject_lunch_pnls: list = []
    would_pass_lunch_pnls: list = []
    pnl_by_hour: dict = defaultdict(list)
    pnl_by_weekday: dict = defaultdict(list)
    skip_notes = 0

    for i, date in enumerate(dates):
        try:
            spy_bars = client.get_spy_minute_bars(date)
        except AlpacaDataError:
            skip_notes += 1
            continue
        if not spy_bars:
            continue

        todays_pace = session_pace_width_pct(spy_bars, minutes=15)
        dynamic_cap = pace_tracker.record_and_get_cap(todays_pace)

        opportunities = find_breakout_opportunities(_bars_to_signal_series(spy_bars))
        for entry in opportunities:
            # Every OTHER filter in the real pipeline except VIX (out of
            # scope for this study) and time_quality_window (the one being
            # measured, plus the broader hour/weekday breakdown below).
            econ_result = economic_calendar_clear(entry.entry_time, dynamic_cap)
            if not econ_result.passed:
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

            # (1) Validate the EXISTING lunch-chop bootstrap window.
            lunch_result = time_quality_window(entry.entry_time)
            if lunch_result.passed:
                would_pass_lunch_pnls.append(pnl)
            else:
                would_reject_lunch_pnls.append(pnl)

            # (2) Broader breakdown -- real ET via zoneinfo, not the rough approximation.
            entry_et = entry.entry_time.astimezone(_ET)
            pnl_by_hour[entry_et.hour].append(pnl)
            pnl_by_weekday[entry_et.weekday()].append(pnl)

        if (i + 1) % 20 == 0:
            print(f"  ...{i + 1}/{len(dates)} dates processed")

    print(f"\nFetch/data skips: {skip_notes}")

    print("\n=== (1) EXISTING lunch-chop window (12:00-13:30 ET) validation ===")
    summarize("would_have_REJECTED by time_quality_window", would_reject_lunch_pnls)
    summarize("would_have_PASSED by time_quality_window", would_pass_lunch_pnls)
    print(
        "\nIf the rejected subset performs no worse than the passed subset, this"
        "\nbootstrap window has no real discriminating power on this data (same"
        "\ndisposition as the volume/cross-market filters). Run a second"
        "\nindependent period before deciding either way."
    )

    all_pnls = [p for pnls in pnl_by_hour.values() for p in pnls]

    print("\n=== (2) By ET entry hour (open-ended, no existing filter boundary) ===")
    for hour in sorted(pnl_by_hour):
        summarize(f"hour {hour:02d}:00-{hour:02d}:59 ET", pnl_by_hour[hour])
        if args.permutations > 0:
            p = permutation_p_value(all_pnls, pnl_by_hour[hour], args.permutations, rng)
            print(
                f"    permutation p-value (one-sided, {args.permutations} draws): {p:.4f}"
                f"{'  <-- NOT explainable by chance at the 1% level' if p < 0.01 else ''}"
            )

    print("\n=== (2) By day of week ===")
    for wd in sorted(pnl_by_weekday):
        summarize(_WEEKDAY_NAMES[wd], pnl_by_weekday[wd])

    print(
        "\nThese two breakdowns are DESCRIPTIVE, not a proposed filter -- per"
        "\nthis project's own rule, nothing is activated from a single period's"
        "\nnumbers alone, and (Controller's own instruction, 2026-11-23) not from"
        "\ntwo periods merely agreeing on direction either -- that can happen by"
        "\nchance. The permutation p-value above is the sharp, numeric answer for"
        "\neach hour; a bucket needs BOTH a low p-value AND agreement across"
        "\nindependent periods before being proposed as a real filter."
    )


if __name__ == "__main__":
    main()
