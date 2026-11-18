"""Measures two things needed for the risk/sizing workstream
(Controller priority 2 of 3, after signal quality): a data-grounded
daily loss limit, and whether the current concurrency cap (2) and an
unbounded daily trade count still make sense on fresh data.

Per the Controller's own rule ("calculate from recorded data, never
from a chosen number", .claude/rules/03-research-and-workflow.md):
this reports DISTRIBUTIONS, not a single proposed threshold. The
Controller picks the number from what the data actually shows.

Methodology: run the same locked pipeline (signal -> entry_filters ->
exit_logic.simulate_exit with the day-pace cap) used by every prior
D-0095 measurement, UNCAPPED on concurrency (every opportunity that
passes the filters is taken), and record each trade's real entry_time,
exit_time (from simulate_exit's own result, not a planned estimate)
and pnl. From that one list of real trades:

1. DAILY LOSS DISTRIBUTION -- group by date, sum pnl per day, report
   the full distribution (worst days, percentiles, mean) so the
   Controller can choose a daily stop level from real worst-case days,
   not a guess.
2. TRADES-PER-DAY -- report the distribution of how many candidates
   pass filters per day, and whether high-count days have worse
   per-trade pnl than low-count days (evidence for or against a daily
   trade-count cap, same "tag and compare" method used throughout
   D-0095/D-0095-A).
3. CONCURRENCY CAP RESWEEP -- re-run the same causal, sequential
   admission simulation D-0095's cap=2 decision was based on, but on
   this run's own data, using each trade's REAL exit_time (from
   simulate_exit), not a planned-exit estimate -- so this is a
   fresh, independent check of whether cap=2 still holds, not a
   re-display of the old numbers.
4. DAILY TRADE-COUNT CAP SWEEP (2026-11-18 addition) -- the tercile
   view in (2) said "high-count days are worse" but its "high" bucket
   spanned 8-23 trades/day, too wide to say WHERE the cap should sit.
   This sweeps a cap K from 1 to the observed max trades/day: for each
   day, take only that day's first K trades in entry-time order
   (reject the (K+1)th candidate onward, same "reject the extra
   signal" shape as the concurrency cap, applied per-day instead of
   per-moment) and report total pnl / profit_factor at each K -- same
   read as the concurrency sweep: look for where it plateaus or
   reverses, don't pick a number independently of that point.

Uses only PAST, already-completed trading days -- historical
measurement, not a live run.

Run on the VM where the real .env is loaded:

  cd ~/Treading-bot-Claude && set -a && source .env && set +a && \\
  PYTHONPATH=src python3.11 scripts/spx0dte_measure_risk_sizing.py --start 2025-06-01 --end 2025-12-31
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean, median

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from spx0dte.alpaca_client import AlpacaDataError, AlpacaOptionsClient  # noqa: E402
from spx0dte.entry_filters import run_all_filters  # noqa: E402
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


def _percentile(sorted_vals: list, pct: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * pct
    f, c = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


CONTRACT_MULTIPLIER = 100  # US listed options convention, not an Alpaca/code choice -- 1 contract = 100 shares-equivalent


def _dollars(raw_per_share_pnl: float, quantity: int = 1) -> float:
    """Converts a raw exit_price-entry_price figure (per share, as
    every pnl in this module is computed) into the real dollar amount
    a position of `quantity` contracts would actually realize. See
    docs/trading/decisions.md D-0095-A, 'CORRECTION... not real
    dollars' -- every bare total in this project's history before
    2026-11-18 needs this same x100 conversion applied by the reader."""
    return raw_per_share_pnl * CONTRACT_MULTIPLIER * quantity


def report_daily_loss_distribution(daily_pnls: dict) -> None:
    print("\n=== 1. DAILY PNL DISTRIBUTION (for a data-grounded daily loss limit) ===")
    print("  (raw = per-share option price points; $ = real dollars at 1 contract, raw x100)")
    if not daily_pnls:
        print("  no trading days with accepted trades -- nothing to report.")
        return
    values = sorted(daily_pnls.values())
    n = len(values)
    losing_days = [v for v in values if v < 0]
    print(f"  trading days with >=1 accepted trade: {n}")
    print(f"  losing days: {len(losing_days)} ({len(losing_days)/n:.1%})")
    print(f"  worst day: {values[0]:+.2f} raw (${_dollars(values[0]):+,.0f})")
    print(f"  5th percentile day: {_percentile(values, 0.05):+.2f} raw (${_dollars(_percentile(values, 0.05)):+,.0f})")
    print(f"  25th percentile day: {_percentile(values, 0.25):+.2f} raw (${_dollars(_percentile(values, 0.25)):+,.0f})")
    print(f"  median day: {median(values):+.2f} raw (${_dollars(median(values)):+,.0f})")
    print(f"  mean day: {mean(values):+.2f} raw (${_dollars(mean(values)):+,.0f})")
    print(f"  best day: {values[-1]:+.2f} raw (${_dollars(values[-1]):+,.0f})")
    if losing_days:
        print(f"  mean losing day: {mean(losing_days):+.2f} raw (${_dollars(mean(losing_days)):+,.0f})")
        worst5_dollars = [round(_dollars(v)) for v in values[:5]]
        print(f"  worst 5 days (raw): {[round(v, 2) for v in values[:5]]}")
        print(f"  worst 5 days ($): {worst5_dollars}")
    print(
        "\n  Choose a daily-loss-stop level from the $ column directly --"
        "\n  e.g. 'a bit worse than the 5th-percentile day' or 'the worst day"
        "\n  seen plus a margin' -- not a round number picked independently of it."
        "\n  If --quantity (live) is ever run above 1 contract, scale this"
        "\n  linearly: these $ figures assume the approved 1-contract baseline."
    )


def report_trades_per_day(daily_counts: dict, daily_pnls: dict) -> None:
    print("\n=== 2. TRADES-PER-DAY DISTRIBUTION (for a daily trade-count cap) ===")
    if not daily_counts:
        print("  no trading days with accepted trades -- nothing to report.")
        return
    counts = sorted(daily_counts.values())
    print(f"  days: {len(counts)}  min={counts[0]} median={median(counts)} max={counts[-1]}")
    print(
        f"  75th percentile: {_percentile(counts, 0.75):.1f}  "
        f"90th percentile: {_percentile(counts, 0.90):.1f}  "
        f"95th percentile: {_percentile(counts, 0.95):.1f}"
    )
    print(
        "\n  (Not a trading-behavior limit -- an 'unusual day' NOTIFICATION"
        "\n  threshold, per the Controller's own suggestion: inform, never"
        "\n  block, since the cap sweep below shows no profit case for"
        "\n  blocking a high-count day. The 90th percentile above is the"
        "\n  recommended starting threshold -- re-measure periodically"
        "\n  rather than freezing it forever.)"
    )

    # Tag-and-compare: split days into terciles by trade count, report
    # average pnl PER TRADE (not per day) in each tercile -- same
    # methodology as the cross-market strength probe.
    ranked = sorted(daily_counts.items(), key=lambda kv: kv[1])
    third = len(ranked) // 3
    if third == 0:
        print("  too few days to split into terciles -- skipped.")
        return
    for label, bucket in (
        ("low-count days", ranked[:third]),
        ("mid-count days", ranked[third:2 * third]),
        ("high-count days", ranked[2 * third:]),
    ):
        total_trades = sum(c for _, c in bucket)
        total_pnl = sum(daily_pnls[date] for date, _ in bucket)
        per_trade = total_pnl / total_trades if total_trades else 0.0
        count_range = (bucket[0][1], bucket[-1][1]) if bucket else (0, 0)
        print(
            f"  {label} (trades/day {count_range[0]}-{count_range[1]}): "
            f"{len(bucket)} days, {total_trades} trades, pnl/trade={per_trade:+.4f}"
        )
    print(
        "\n  If pnl/trade drops clearly on high-count days, that's evidence"
        "\n  FOR a daily trade-count cap (overtrading signal decay). If it's"
        "\n  flat, the concurrency cap is already doing the real work and a"
        "\n  separate daily count cap would add nothing."
    )


def simulate_concurrency_cap(trades: list, cap: int) -> dict:
    """Causal, sequential admission: process trades in entry-time
    order, accept only if fewer than `cap` already-accepted trades
    are still open (by their REAL exit_time) at this trade's
    entry_time. A rejected trade occupies no slot."""
    open_until: list = []
    accepted_pnls: list = []
    for entry_time, exit_time, pnl in sorted(trades, key=lambda t: t[0]):
        open_until = [t for t in open_until if t > entry_time]
        if len(open_until) < cap:
            open_until.append(exit_time)
            accepted_pnls.append(pnl)
    n = len(accepted_pnls)
    wins = [p for p in accepted_pnls if p > 0]
    losses = [p for p in accepted_pnls if p <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = round(gross_profit / gross_loss, 4) if gross_loss > 0 else float("inf")
    return {"n": n, "total_pnl": sum(accepted_pnls), "profit_factor": pf}


def report_concurrency_resweep(trades: list) -> None:
    print("\n=== 3. CONCURRENCY CAP RESWEEP (cap=2 was the D-0095 decision -- does it still hold?) ===")
    if not trades:
        print("  no trades -- nothing to report.")
        return
    for cap in range(1, 9):
        r = simulate_concurrency_cap(trades, cap)
        print(f"  cap={cap}: n={r['n']} total_pnl={r['total_pnl']:+.2f} profit_factor={r['profit_factor']}")
    print(
        "\n  Look for where total_pnl plateaus or reverses, same read as the"
        "\n  original D-0095 sweep -- the cap should sit right at that point,"
        "\n  not higher just because headroom exists."
    )


def simulate_daily_trade_count_cap(trades_by_day: dict, cap: int) -> dict:
    """For each day, keep only the first `cap` trades in entry-time
    order (reject the (cap+1)th candidate onward that day) and sum
    pnl over every kept trade across all days. Pure day-by-day
    truncation -- no cross-day state, unlike the concurrency sweep."""
    kept_pnls: list = []
    for _date, day_trades in trades_by_day.items():
        ordered = sorted(day_trades, key=lambda t: t[0])
        kept_pnls.extend(pnl for _entry_time, pnl in ordered[:cap])
    n = len(kept_pnls)
    wins = [p for p in kept_pnls if p > 0]
    losses = [p for p in kept_pnls if p <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = round(gross_profit / gross_loss, 4) if gross_loss > 0 else float("inf")
    return {"n": n, "total_pnl": sum(kept_pnls), "profit_factor": pf}


def report_daily_trade_count_cap_sweep(trades_by_day: dict) -> None:
    print("\n=== 4. DAILY TRADE-COUNT CAP SWEEP (where exactly does an extra trade/day start hurting?) ===")
    if not trades_by_day:
        print("  no trading days -- nothing to report.")
        return
    max_count = max(len(day_trades) for day_trades in trades_by_day.values())
    for cap in range(1, max_count + 1):
        r = simulate_daily_trade_count_cap(trades_by_day, cap)
        print(
            f"  cap={cap}: n={r['n']} total_pnl={r['total_pnl']:+.2f} raw "
            f"(${_dollars(r['total_pnl']):+,.0f}) profit_factor={r['profit_factor']}"
        )
    print(
        "\n  Look for where total_pnl plateaus or turns down as the cap"
        "\n  rises, same read as the concurrency sweep above -- that point"
        "\n  is where a daily trade-count cap should sit. Re-run this on a"
        "\n  second independent period before setting it for real, same"
        "\n  cross-period-agreement rule as every other filter/limit"
        "\n  decision in this project."
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
    print(f"Measuring risk/sizing over {len(dates)} weekdays, {args.start} to {args.end} (concurrency UNCAPPED)...")

    daily_pnls: dict = defaultdict(float)
    daily_counts: dict = defaultdict(int)
    all_trades: list = []  # (entry_time, exit_time, pnl)
    trades_by_day: dict = defaultdict(list)  # date -> [(entry_time, pnl), ...]
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
            daily_pnls[date] += pnl
            daily_counts[date] += 1
            all_trades.append((entry.entry_time, result.exit_time, pnl))
            trades_by_day[date].append((entry.entry_time, pnl))

        if (i + 1) % 20 == 0:
            print(f"  ...{i + 1}/{len(dates)} dates processed")

    print(f"\nFetch/data skips: {skip_notes}")
    print(f"Total accepted (uncapped) trades: {len(all_trades)}")

    report_daily_loss_distribution(daily_pnls)
    report_trades_per_day(daily_counts, daily_pnls)
    report_concurrency_resweep(all_trades)
    report_daily_trade_count_cap_sweep(trades_by_day)


if __name__ == "__main__":
    main()
