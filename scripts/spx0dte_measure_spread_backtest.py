"""Phase 1 of the "risk-defined spread" professionalization design
(Controller's own request, 2026-11-23): BEFORE any live execution code
is written, measures what a vertical DEBIT spread (buy the near
strike, sell a further-out-of-the-money strike of the SAME type and
expiry) would actually have done against real historical option data,
compared to the current naked-long-option approach -- at several
candidate short-leg widths, since guessing one width is exactly the
kind of number this project's own rule forbids ("calculate from
recorded data, never a guessed number").

Mechanism verified against Alpaca's own documentation before this was
written (https://docs.alpaca.markets/docs/options-level-3-trading,
https://docs.alpaca.markets/reference/postorder -- read verbatim, not
summarized): a 2-leg vertical (long near strike + short far strike,
same type/expiry) is a COVERED structure under Alpaca's multi-leg
rule ("an MLeg order is accepted only if all its legs are covered
within the same MLeg order") -- so this is a buildable live order,
not a theoretical one. Nothing about live execution is built in this
script; it only answers "would this have helped historically".

METHODOLOGY:
- Same backtest skeleton as every other D-0095/D-0095-A measurement
  (signal -> real current filters [VIX range + economic calendar --
  the lunch-chop window was measured and disabled, D-0100 FINAL] ->
  simulate_exit), so the candidate population matches what actually
  reaches a live proposal today.
- For EACH candidate, in the SAME pass: simulate the baseline (today's
  naked long option) AND the spread at each `--widths` value (SPX
  points away from the long strike), so all variants see the exact
  same candidates -- a fair, paired comparison, not independent
  samples that could differ by chance alone.
- The short leg is found via `find_strike_near_target` (this script's
  own helper, NOT `alpaca_client.find_nearest_traded_strike` -- that
  function's coarse-then-fine search is built to find the nearest
  LIQUID near-the-money strike and converges back to essentially the
  long leg's own strike regardless of how far away the target is;
  confirmed empirically on the first real run, 868/868 candidates
  found "no usable short leg"). `find_strike_near_target` scans a
  tight, $5-stepped band directly around (long_strike + width) for a
  call / (long_strike - width) for a put. The REAL actual width
  achieved is recorded (the nearest strike that actually traded may
  not sit exactly `width` points away), never assumed.
- The spread's net price at any moment is (long leg price) - (short
  leg price); its own `simulate_exit` run uses a synthetic bar series
  built by looking up each leg's price AS OF each of the LONG leg's own
  bar timestamps (carrying the short leg's last-known print forward --
  it is typically far less liquid) -- this reuses `exit_logic.
  simulate_exit` completely unmodified, rather than writing a second
  copy of its target/stop/stall logic for a 2-leg instrument.
- Pnl units here are price POINTS per spread/option (not multiplied by
  100 or by quantity) -- consistent across baseline and every spread
  width, which is what a fair relative comparison needs. This is NOT
  yet a capital-efficiency comparison (a spread costs less up-front
  than the naked option, so the same risk budget could buy more spread
  units -- that is a separate sizing question for a later phase, not
  conflated with this per-unit risk/outcome comparison).
- Reports max single-trade LOSS per variant, not just profit_factor --
  the entire point of a risk-defined spread is capping the worst case,
  which a profit_factor/expectancy summary alone would not show.

Run on the VM where the real .env is loaded:

  cd ~/Treading-bot-Claude && set -a && source .env && set +a && \\
  PYTHONPATH=src python3.11 scripts/spx0dte_measure_spread_backtest.py \\
      --start 2025-06-01 --end 2025-12-31 --widths 10,20,30
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from statistics import mean

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from spx0dte.alpaca_client import AlpacaDataError, AlpacaOptionsClient, _occ_symbol  # noqa: E402
from spx0dte.entry_filters import economic_calendar_clear, vix_in_tradeable_range  # noqa: E402
from spx0dte.exit_logic import DailyPaceTracker, session_pace_width_pct, simulate_exit  # noqa: E402
from spx0dte.ratio_calibration import RatioProvider  # noqa: E402
from spx0dte.signal import find_breakout_opportunities  # noqa: E402
from spx0dte.vix_proxy import MAX_PLAUSIBLE_RATIO, VIXY_PROXY_SYMBOL, fetch_vixcls_closes  # noqa: E402


def _bars_to_signal_series(bars: list) -> list:
    return [{"datetime": b["t"], "spx": b["c"]} for b in bars]


def _parse_bar_time(bar: dict) -> datetime:
    return datetime.fromisoformat(bar["t"].replace("Z", "+00:00"))


_SHORT_LEG_SEARCH_SPAN = 15   # SPX points either side of the target offset
_SHORT_LEG_SEARCH_STEP = 5    # SPXW strikes near the money step in $5 increments
_SHORT_LEG_WINDOW_MINUTES = 20


def find_strike_near_target(
    client: AlpacaOptionsClient, date: str, target_level: float, call_put: str, around_time_iso: str,
) -> "tuple | None":
    """Finds a strike with a real trade print NEAR `target_level` --
    deliberately NOT `alpaca_client.find_nearest_traded_strike`, whose
    coarse-then-fine search is built to find the nearest LIQUID
    (near-the-money) strike to an approximate underlying level, and
    converges back to essentially the same near-the-money strike
    regardless of how far `target_level` is offset from it (confirmed
    empirically: using it for the short leg returned the long leg's
    own strike, or nothing, for 868/868 candidates in the first real
    run of this script -- a real bug in reusing the wrong tool, not a
    real absence of OTM liquidity). This scans a TIGHT, $5-stepped band
    directly around `target_level` instead, sorted by how close to the
    actual target each candidate strike is."""
    now = datetime.fromisoformat(around_time_iso.replace("Z", "+00:00"))
    window_start = (now - timedelta(minutes=_SHORT_LEG_WINDOW_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")
    window_end = (now + timedelta(minutes=_SHORT_LEG_WINDOW_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")

    center = round(target_level / _SHORT_LEG_SEARCH_STEP) * _SHORT_LEG_SEARCH_STEP
    offsets = sorted(
        range(-_SHORT_LEG_SEARCH_SPAN, _SHORT_LEG_SEARCH_SPAN + 1, _SHORT_LEG_SEARCH_STEP), key=abs,
    )
    for offset in offsets:
        strike = int(center + offset)
        if strike <= 0:
            continue
        symbol = _occ_symbol(date, call_put, strike)
        try:
            bars = client.get_option_bars(symbol, window_start, window_end, limit=5)
        except AlpacaDataError:
            continue
        if bars:
            return strike, symbol
    return None


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


def price_as_of(bars: list, cutoff: datetime) -> float:
    """Latest trade price at or before `cutoff` -- carries the last
    known print forward for a sparser (less liquid) leg. Returns None
    if nothing has traded yet at/before cutoff."""
    eligible = [b for b in bars if _parse_bar_time(b) <= cutoff]
    if not eligible:
        return None
    return sorted(eligible, key=lambda b: b["t"])[-1]["c"]


def build_net_price_series(long_bars: list, short_bars: list) -> list:
    """Synthetic bar series (same {'t', 'c'} shape simulate_exit
    expects) on the LONG leg's own timestamps -- the short leg's price
    is looked up AS OF each of those same moments, never a future
    short-leg print. A point is skipped if the short leg has no known
    price yet at that moment (can't compute a net price)."""
    out = []
    for b in sorted(long_bars, key=lambda b: b["t"]):
        t = _parse_bar_time(b)
        short_price = price_as_of(short_bars, t)
        if short_price is None:
            continue
        out.append({"t": b["t"], "c": b["c"] - short_price})
    return out


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
    worst = min(trades)
    print(
        f"  {label}: n={n} total_pnl={sum(trades):+.2f} win_rate={len(wins)/n:.2%} "
        f"expectancy={mean(trades):+.4f} profit_factor={pf} worst_single_trade={worst:+.2f}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--widths", default="10,20,30", help="comma-separated SPX points for the short leg")
    args = parser.parse_args()
    widths = [float(w) for w in args.widths.split(",")]

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
    print(f"Measuring spread-vs-naked backtest over {len(dates)} weekdays, {args.start} to {args.end}, widths={widths}...")

    baseline_pnls: list = []
    spread_pnls: dict = {w: [] for w in widths}
    achieved_widths: dict = {w: [] for w in widths}  # actual strike distance achieved, per requested width
    skip_notes = 0
    no_short_leg = {w: 0 for w in widths}

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
            long_strike, long_occ = found

            window_end = entry.entry_time + timedelta(minutes=int(dynamic_cap) + 5)
            try:
                long_bars = client.get_option_bars(
                    long_occ,
                    (entry.entry_time - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    window_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    limit=200,
                )
            except AlpacaDataError:
                continue
            if not long_bars:
                continue

            # Baseline: today's naked long option, unchanged methodology.
            baseline_result = simulate_exit(long_bars, entry.entry_time, int(dynamic_cap))
            if baseline_result is None:
                continue
            long_entry_price = None
            for b in sorted(long_bars, key=lambda b: b["t"]):
                bt = datetime.fromisoformat(b["t"].replace("Z", "+00:00"))
                if bt >= entry.entry_time:
                    long_entry_price = b["c"]
                    break
            if long_entry_price is None:
                continue
            baseline_pnls.append(baseline_result.exit_price - long_entry_price)

            # Each spread width, same candidate, same long leg.
            for width in widths:
                target_short_level = long_strike + width if entry.direction == "call" else long_strike - width
                short_found = find_strike_near_target(
                    client, date, target_short_level, call_put, around_time_iso=entry_time_iso,
                )
                if short_found is None or short_found[0] == long_strike:
                    no_short_leg[width] += 1
                    continue
                short_strike, short_occ = short_found
                try:
                    short_bars = client.get_option_bars(
                        short_occ,
                        (entry.entry_time - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        window_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        limit=200,
                    )
                except AlpacaDataError:
                    no_short_leg[width] += 1
                    continue
                if not short_bars:
                    no_short_leg[width] += 1
                    continue

                net_bars = build_net_price_series(long_bars, short_bars)
                net_entry_price = None
                for b in net_bars:
                    bt = datetime.fromisoformat(b["t"].replace("Z", "+00:00"))
                    if bt >= entry.entry_time:
                        net_entry_price = b["c"]
                        break
                if net_entry_price is None:
                    no_short_leg[width] += 1
                    continue
                if net_entry_price <= 0:
                    # Both legs are separately-printed last-TRADE prices, not a
                    # synchronized spread quote -- a non-positive net debit at
                    # entry means the two prints don't line up into a sane
                    # spread at this moment (stale/mismatched ticks), not a
                    # real tradeable entry. simulate_exit itself divides by
                    # this as P0, so this must be excluded before calling it,
                    # not treated as a valid (and divide-by-zero-prone) trade.
                    no_short_leg[width] += 1
                    continue

                net_result = simulate_exit(net_bars, entry.entry_time, int(dynamic_cap))
                if net_result is None:
                    continue

                spread_pnls[width].append(net_result.exit_price - net_entry_price)
                achieved_widths[width].append(abs(short_strike - long_strike))

        if (i + 1) % 20 == 0:
            print(f"  ...{i + 1}/{len(dates)} dates processed")

    print(f"\nFetch/data skips: {skip_notes}")

    print("\n=== Baseline: today's naked long option (unchanged) ===")
    summarize("naked long", baseline_pnls)

    for width in widths:
        print(f"\n=== Spread, requested short-leg width {width} SPX points ===")
        if achieved_widths[width]:
            print(f"  actual achieved width (mean of nearest traded strikes found): {mean(achieved_widths[width]):.1f}")
        print(f"  candidates with no usable short leg found/skipped: {no_short_leg[width]}")
        summarize(f"spread width~{width}", spread_pnls[width])

    print(
        "\nDESCRIPTIVE ONLY -- no live execution code follows from this script by"
        "\nitself. Compare worst_single_trade across variants first (the actual"
        "\npoint of a risk-defined spread); compare profit_factor/expectancy"
        "\nsecond. Needs a second independent period in agreement before any"
        "\nlive design work starts, same standard as every other measurement"
        "\nin this project."
    )


if __name__ == "__main__":
    main()
