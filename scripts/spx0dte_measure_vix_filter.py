"""Measures whether `entry_filters.vix_in_tradeable_range`'s bootstrap
12-35 bounds actually discriminate real performance, now that
`vix_proxy.VixProxyProvider` gives a validated (median 2.1% error,
630-sample) VIX estimate to test them with. The bounds themselves
were NEVER measured against this project's own data (D-0095's module
docstring says so plainly: "not claiming to have found an edge inside
the range -- only removing the two ends most research agrees are
unreliable").

Methodology, identical in spirit to every other D-0095/D-0095-A
filter measurement: run the locked pipeline (signal -> filters minus
VIX -> exit_logic.simulate_exit with the day-pace cap) on real
historical data, tag each accepted trade with what
vix_in_tradeable_range WOULD have decided using the real estimated
VIX at that trade's own entry_time, without enforcing it, then
compare the would-reject subset's pnl/profit_factor against the
would-pass subset's.

LOOKAHEAD-BUG LESSON APPLIED FROM THE START (the cross-market filter
measurement did not catch this until its SECOND run): the VIX
estimate for an entry at e.g. 10:15am must be computed using ONLY
VIXY bars up to 10:15am, never the rest of that historical day.
`_vixy_price_as_of` truncates explicitly before every estimate --
this is not an afterthought, it is the reason this script exists
as a careful, from-scratch build rather than a copy-paste of
`vix_proxy.py`'s live-tick logic (which legitimately uses "all bars
fetched so far today" because in live use that already stops at
"now" -- a historical day's bars do not stop anywhere on their own).

Run on the VM where the real .env is loaded:

  cd ~/Treading-bot-Claude && set -a && source .env && set +a && \\
  PYTHONPATH=src python3.11 scripts/spx0dte_measure_vix_filter.py --start 2025-06-01 --end 2025-12-31
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
from spx0dte.entry_filters import run_all_filters, vix_in_tradeable_range  # noqa: E402
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


def _vixy_price_as_of(vixy_minute_bars: list, cutoff: datetime) -> float:
    """The latest VIXY close at or before `cutoff` -- never a bar
    after it. Returns 0.0 (caller must check) if nothing qualifies."""
    eligible = [b for b in vixy_minute_bars if _parse_bar_time(b) <= cutoff]
    if not eligible:
        return 0.0
    return sorted(eligible, key=lambda b: b["t"])[-1]["c"]


def estimate_vix_at(
    entry_time: datetime, yesterday_vix_close: float, yesterday_vixy_close: float,
    todays_vixy_minute_bars: list,
) -> float:
    """Same formula as vix_proxy.VixProxyProvider.get_vix_level, but
    the "live" VIXY price is explicitly truncated to `entry_time`,
    not "the latest bar fetched" -- the lookahead-safe version for
    historical measurement."""
    vixy_now = _vixy_price_as_of(todays_vixy_minute_bars, entry_time)
    if vixy_now <= 0 or yesterday_vixy_close <= 0:
        return 0.0
    ratio = vixy_now / yesterday_vixy_close
    if ratio <= 0 or ratio > MAX_PLAUSIBLE_RATIO or ratio < 1.0 / MAX_PLAUSIBLE_RATIO:
        return 0.0
    return yesterday_vix_close * ratio


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

    fetch_start = (datetime.strptime(args.start, "%Y-%m-%d") - timedelta(days=10)).strftime("%Y-%m-%d")
    print("Fetching FRED VIXCLS...")
    vix_closes = fetch_vixcls_closes(fred_key, fetch_start, args.end)
    sorted_vix_dates = sorted(vix_closes.keys())

    print(f"Fetching {VIXY_PROXY_SYMBOL} daily bars (for yesterday's anchor)...")
    vixy_daily_bars = client.get_stock_daily_bars(VIXY_PROXY_SYMBOL, fetch_start, args.end)
    vixy_daily_closes = {b["t"][:10]: b["c"] for b in vixy_daily_bars}

    dates = list(daterange(args.start, args.end))
    print(f"Measuring vix_in_tradeable_range over {len(dates)} weekdays, {args.start} to {args.end}...")

    would_reject_pnls: list = []
    would_pass_pnls: list = []
    no_vix_estimate = 0
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

            estimated_vix = estimate_vix_at(
                entry.entry_time, yesterday_vix_close, yesterday_vixy_close, vixy_bars_today,
            )
            if estimated_vix <= 0:
                no_vix_estimate += 1
                continue

            vix_result = vix_in_tradeable_range(estimated_vix)
            if vix_result.passed:
                would_pass_pnls.append(pnl)
            else:
                would_reject_pnls.append(pnl)

        if (i + 1) % 20 == 0:
            print(f"  ...{i + 1}/{len(dates)} dates processed")

    print(f"\nFetch/data skips: {skip_notes}")
    print(f"Trades with no usable VIX estimate (excluded from tagging): {no_vix_estimate}")
    print("\n=== RESULT ===")
    summarize("would_have_REJECTED by vix_in_tradeable_range (12-35)", would_reject_pnls)
    summarize("would_have_PASSED by vix_in_tradeable_range (12-35)", would_pass_pnls)
    print(
        "\nIf the rejected subset performs no worse than the passed subset,"
        "\nthe 12-35 bootstrap band has no real discriminating power on this"
        "\ndata -- same disposition as the volume filter. If it performs"
        "\nclearly worse, that is real evidence FOR activating the filter"
        "\nwith these bounds. Run on a second independent period before"
        "\ndeciding either way, same cross-period-agreement rule as every"
        "\nother filter in this project."
    )


if __name__ == "__main__":
    main()
