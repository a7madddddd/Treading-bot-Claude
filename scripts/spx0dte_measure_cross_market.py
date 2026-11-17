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

            cm = cross_market_confirmed(entry.direction, qqq_bars, iwm_bars)
            if cm.passed:
                would_pass_pnls.append(pnl)
            else:
                would_reject_pnls.append(pnl)

        if (i + 1) % 20 == 0:
            print(f"  ...{i + 1}/{len(dates)} dates processed")

    print(f"\nFetch/data skips: {skip_notes}")
    print("\n=== RESULT ===")
    summarize("would_have_REJECTED by cross-market", would_reject_pnls)
    summarize("would_have_PASSED by cross-market", would_pass_pnls)
    print(
        "\nIf the rejected subset performs no worse than the passed subset,"
        "\ncross_market_confirmed has no real discriminating power (same"
        "\nconclusion as the volume filter) -- record either way in"
        "\ndocs/trading/decisions.md before deciding whether to wire it in."
    )


if __name__ == "__main__":
    main()
