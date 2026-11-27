"""Measures whether a trade's own Black-Scholes GREEKS at entry
(delta, theta decay rate) predict its real 0DTE outcome -- the
Controller's own request (2026-11-25), step 1 before any Greeks-aware
EXIT redesign: tag and compare first, never build a new exit engine on
a guess.

Checked against this project's history first (CLAUDE.md §0.b):
`src/spx0dte/greeks.py` (D-0095-A, "Option B") already has a full,
validated Black-Scholes + implied-volatility solver, built so far ONLY
for re-pricing a stale quote to a live moment -- never used to tag a
trade's outcome. `scripts/spx0dte_validate_greeks.py` validated the
re-pricing accuracy itself (median 3.8% error), a different question
(pricing accuracy, not outcome prediction). No prior script in this
project ties Greeks to trade pnl. No overlap.

Two Greeks-derived features, both backed out of the REAL observed
entry option price (never a guessed IV):

1. Entry |delta| -- how far in/out of the money the option is at
   entry. A 0DTE breakout entry is usually bought very close to the
   money (|delta| near 0.5); this tests whether trades that happen to
   be more/less ATM at entry do better.
2. Entry theta BURN RATE -- theta_per_day / entry option price (a
   normalized fraction of the premium lost per day to time decay
   alone, NOT absolute theta, so it is comparable across different
   premium levels). 0DTE options decay fastest of all -- this tests
   whether a trade entered when decay is proportionally faster (closer
   to the money, or later in the day) does worse.

Both bucketed into TERCILES from this run's own distribution, pnl/
profit_factor compared, permutation tests on BOTH tails (no
directional assumption baked in, same descriptive spirit as D-0106).
Risk-free rate and the DST-aware same-day expiry calculation are
reused UNCHANGED from `live_pricing.py` (RISK_FREE_RATE,
MARKET_CLOSE_UTC_HOUR_EST/EDT, econ_calendar._is_us_dst) -- the real
production values, not a new guess. A candidate whose entry price
cannot produce a valid implied volatility (price outside what any
sigma in [0.001, 5.0] can reproduce) is skipped, never forced to a
guessed sigma.

Run on the VM where the real .env is loaded:

  cd ~/Treading-bot-Claude && set -a && source .env && set +a && \\
  PYTHONPATH=src python3.11 scripts/spx0dte_measure_greeks_at_entry.py \\
      --start 2025-06-01 --end 2025-12-31 --permutations 10000
"""
from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from statistics import mean

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from spx0dte.alpaca_client import AlpacaDataError, AlpacaOptionsClient  # noqa: E402
from spx0dte.econ_calendar import _is_us_dst  # noqa: E402
from spx0dte.entry_filters import economic_calendar_clear, vix_in_tradeable_range  # noqa: E402
from spx0dte.exit_logic import DailyPaceTracker, session_pace_width_pct, simulate_exit  # noqa: E402
from spx0dte.greeks import ImpliedVolatilityError, OptionType, black_scholes_greeks, implied_volatility  # noqa: E402
from spx0dte.live_pricing import MARKET_CLOSE_UTC_HOUR_EDT, MARKET_CLOSE_UTC_HOUR_EST, RISK_FREE_RATE  # noqa: E402
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


def same_day_expiry(date: str) -> datetime:
    """DST-aware 4:00pm ET close, reusing the exact production
    constants from `live_pricing.py` -- not a new guess. Returns a
    UTC-AWARE datetime (bug found live on the VM 2026-11-26: the
    first version returned a naive datetime, which crashed every
    comparison against `entry.entry_time`, a UTC-aware value from
    `signal.find_breakout_opportunities` -- "can't subtract
    offset-naive and offset-aware datetimes")."""
    naive = datetime.strptime(date, "%Y-%m-%d")
    close_hour = MARKET_CLOSE_UTC_HOUR_EDT if _is_us_dst(naive.month, naive.day) else MARKET_CLOSE_UTC_HOUR_EST
    return naive.replace(hour=close_hour, minute=0, second=0, tzinfo=timezone.utc)


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


def report_tercile(label: str, tagged: list, args, rng: random.Random) -> None:
    print(f"\n=== By {label} tercile (computed from this run's own distribution) ===")
    buckets = tercile_buckets(tagged)
    all_pnls = [pnl for _, pnl in tagged]
    for bucket_name in ("low", "mid", "high"):
        summarize(f"{label} {bucket_name} tercile", buckets[bucket_name])
    if args.permutations > 0 and all_pnls:
        for bucket_name in ("low", "high"):
            if buckets[bucket_name]:
                p = permutation_p_value_better(all_pnls, buckets[bucket_name], args.permutations, rng)
                print(
                    f"    permutation p-value for the {bucket_name.upper()} tercile being genuinely BETTER "
                    f"(one-sided, {args.permutations} draws): {p:.4f}"
                    f"{'  <-- NOT explainable by chance at the 1% level' if p < 0.01 else ''}"
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
    print(f"Measuring Greeks-at-entry over {len(dates)} weekdays, {args.start} to {args.end}...")

    delta_tagged: list = []        # (abs_delta, pnl)
    theta_burn_tagged: list = []   # (abs_theta_burn_rate, pnl)
    skip_notes = 0
    iv_solver_skips = 0
    iv_skip_details: list = []  # (moneyness, reason) -- diagnostic, see below

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
        expiry = same_day_expiry(date)

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
            entry_price_time = None
            for b in sorted(bars, key=lambda b: b["t"]):
                bt = datetime.fromisoformat(b["t"].replace("Z", "+00:00"))
                if bt >= entry.entry_time:
                    entry_price = b["c"]
                    entry_price_time = bt
                    break
            if entry_price is None:
                continue

            # 2026-11-27 (Controller-requested fix, D-0109 UPDATE 4
            # diagnostic): the option's own first post-signal print can
            # arrive several minutes AFTER entry.entry_time, especially
            # for a thinly-traded contract right at a breakout moment --
            # by then SPY may have moved (often reverted, per D-0104's own
            # finding that most breakouts snap back on a 5-minute close).
            # Using approx_spx from entry.entry_time as "S" while
            # entry_price was actually quoted at a LATER, different
            # underlying level produced a real, confirmed, 100%-ITM-
            # clustered skip pattern (see decisions.md D-0109 UPDATE 4).
            # Fix: recompute S at entry_price's OWN timestamp, not the
            # signal's, so the price and the underlying level being
            # compared to it are from the same moment.
            spy_price_at_entry_bar = None
            for sb in sorted(spy_bars, key=lambda b: b["t"]):
                sbt = datetime.fromisoformat(sb["t"].replace("Z", "+00:00"))
                if sbt <= entry_price_time:
                    spy_price_at_entry_bar = sb["c"]
                else:
                    break
            if spy_price_at_entry_bar is None:
                continue
            greeks_ratio = ratio_provider.get_ratio(now=entry_price_time)
            greeks_s = spy_price_at_entry_bar * greeks_ratio

            t_years = max((expiry - entry.entry_time).total_seconds(), 1) / 86400 / 365
            option_type = OptionType.CALL if entry.direction == "call" else OptionType.PUT
            try:
                sigma = implied_volatility(entry_price, greeks_s, strike, t_years, RISK_FREE_RATE, option_type)
            except ImpliedVolatilityError:
                iv_solver_skips += 1
                # 2026-11-27 (Controller-approved verification): record WHY
                # each skip happened and WHERE in moneyness it sits, so a
                # skip-rate difference between terciles can be checked
                # directly instead of assumed harmless. moneyness > 0 means
                # ITM at entry, < 0 means OTM -- if skips cluster at one
                # extreme, they could distort which tercile a nearby
                # surviving trade lands in.
                moneyness = (greeks_s - strike) if option_type is OptionType.CALL else (strike - greeks_s)
                intrinsic = max(moneyness, 0.0)
                reason = "price_below_intrinsic" if entry_price < intrinsic else "price_above_max_producible"
                iv_skip_details.append((moneyness, reason))
                continue
            entry_greeks = black_scholes_greeks(greeks_s, strike, t_years, RISK_FREE_RATE, sigma, option_type)

            pnl = result.exit_price - entry_price
            delta_tagged.append((abs(entry_greeks.delta), pnl))
            if entry_price > 0:
                theta_burn_tagged.append((abs(entry_greeks.theta) / entry_price, pnl))

        if (i + 1) % 20 == 0:
            print(f"  ...{i + 1}/{len(dates)} dates processed")

    print(f"\nFetch/data skips: {skip_notes}, implied-vol solver skips: {iv_solver_skips}")
    if iv_skip_details:
        below_intrinsic = [m for m, r in iv_skip_details if r == "price_below_intrinsic"]
        above_max = [m for m, r in iv_skip_details if r == "price_above_max_producible"]
        itm_skips = [m for m, _ in iv_skip_details if m > 0]
        otm_skips = [m for m, _ in iv_skip_details if m <= 0]
        print(
            f"  skip diagnostic (Controller-requested verification, 2026-11-27): "
            f"below_intrinsic={len(below_intrinsic)}, above_max_producible={len(above_max)}, "
            f"ITM_at_skip={len(itm_skips)}, OTM_at_skip={len(otm_skips)}"
        )
        if itm_skips:
            print(f"    ITM skip moneyness: min={min(itm_skips):.2f} max={max(itm_skips):.2f} mean={mean(itm_skips):.2f}")
        if otm_skips:
            print(f"    OTM skip moneyness: min={min(otm_skips):.2f} max={max(otm_skips):.2f} mean={mean(otm_skips):.2f}")

    report_tercile("entry |delta|", delta_tagged, args, rng)
    report_tercile("entry theta-burn-rate (|theta|/price)", theta_burn_tagged, args, rng)

    print(
        "\nDESCRIPTIVE ONLY -- no exit-logic change follows from this script by itself."
        "\nA Greeks-aware exit redesign needs a low p-value AND agreement across"
        "\nindependent periods (same standard already applied to D-0100 through"
        "\nD-0108) before being proposed, same as every other measurement this"
        "\nproject has run."
    )


if __name__ == "__main__":
    main()
