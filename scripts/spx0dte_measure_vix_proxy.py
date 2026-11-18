"""Measures a comprehensive, free, real-time VIX proxy for the SPX
0DTE module's `entry_filters.vix_in_tradeable_range` filter, which
currently runs with `vix_level=None` (always skipped) because no
free real-time VIX source exists (confirmed by direct testing,
2026-11-18: CBOE's own endpoints refuse/404, Yahoo's public chart
endpoint for ^VIX lags live by ~15 minutes consistently, Nasdaq's API
rejects the symbol -- a structural, industry-wide licensing
restriction on index data, the same reason options data has its own
15-minute delay).

Design (Controller-approved direction, 2026-11-18): don't try to grab
a live absolute VIX number -- anchor to YESTERDAY's real, free,
official FRED VIXCLS close (one day stale, never guessed), then scale
it by TODAY's REAL-TIME relative move in a fast free proxy. The
proxy's absolute level doesn't need to be trusted, only its relative
day-over-day change -- same "stale anchor + live relative move"
shape as `greeks.reprice_from_stale_quote` (Option B) and
`ratio_calibration.RatioProvider`.

    estimated_vix_today = yesterdays_real_vix_close * (proxy_today / proxy_yesterday)

Two independent candidate proxies, measured separately and combined:
1. VIXY (or VXX) daily close ratio -- a real TRADABLE VIX-futures ETP,
   forward-looking like VIX itself (futures price in expectations),
   fetched via the already-generalized `get_stock_daily_bars`
   (feed=iex, real-time/free, same mechanism already proven for
   SPY/QQQ/IWM).
2. SPY realized volatility ratio -- stdev of 1-minute log returns
   within each day (the day-to-day ANNUALIZATION CONSTANT cancels
   exactly in a same-day-type ratio, so it is deliberately omitted,
   not forgotten), computed from bars already fetched for the signal
   path, zero new dependency.

Validates each estimate against the REAL FRED VIXCLS close for that
same day (available only in hindsight for backtesting -- this is
historical measurement, not a live run) and reports the error
distribution for each candidate alone and for their average.

Run on the VM where the real .env is loaded:

  cd ~/Treading-bot-Claude && set -a && source .env && set +a && \\
  PYTHONPATH=src python3.11 scripts/spx0dte_measure_vix_proxy.py --start 2025-06-01 --end 2025-12-31
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from statistics import mean, median, pstdev

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from spx0dte.alpaca_client import AlpacaDataError, AlpacaOptionsClient  # noqa: E402
from spx0dte.ratio_calibration import FRED_BASE_URL  # noqa: E402

VIXY_PROXY_SYMBOL = "VIXY"


def fetch_vixcls_closes(api_key: str, start_date: str, end_date: str) -> dict:
    """Same shape as ratio_calibration.fetch_sp500_closes, for FRED's
    free VIXCLS (CBOE Volatility Index) daily series instead of SP500.
    Kept local to this research script rather than added to
    ratio_calibration.py, which is production code for a different
    series -- duplicdating ~10 lines here is cheaper than coupling an
    unrelated production module to this measurement's needs."""
    qs = urllib.parse.urlencode({
        "series_id": "VIXCLS", "api_key": api_key, "file_type": "json",
        "observation_start": start_date, "observation_end": end_date,
    })
    request = urllib.request.Request(f"{FRED_BASE_URL}/series/observations?{qs}")
    with urllib.request.urlopen(request, timeout=15) as response:
        body = json.loads(response.read().decode("utf-8"))

    closes: dict = {}
    for obs in body.get("observations", []):
        value = obs.get("value")
        if value in (None, ".", ""):
            continue
        try:
            closes[obs["date"]] = float(value)
        except ValueError:
            continue
    return closes


def daterange(start: str, end: str):
    d0 = datetime.strptime(start, "%Y-%m-%d")
    d1 = datetime.strptime(end, "%Y-%m-%d")
    cur = d0
    while cur <= d1:
        if cur.weekday() < 5:
            yield cur.strftime("%Y-%m-%d")
        cur += timedelta(days=1)


def realized_vol(day_bars: list) -> float:
    """Stdev of 1-minute log returns within one day's bars. No
    annualization constant -- it cancels exactly in a same-unit
    day-over-day ratio, so adding one would be pure decoration."""
    closes = [b["c"] for b in sorted(day_bars, key=lambda b: b["t"])]
    if len(closes) < 3:
        return 0.0
    log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(log_returns) < 2:
        return 0.0
    return pstdev(log_returns)


def summarize_errors(label: str, pct_errors: list) -> None:
    n = len(pct_errors)
    if n == 0:
        print(f"  {label}: n=0 (nothing to report)")
        return
    abs_errors = sorted(abs(e) for e in pct_errors)
    print(
        f"  {label}: n={n} mean_abs_err={mean(abs_errors):.1%} "
        f"median_abs_err={median(abs_errors):.1%} "
        f"worst={abs_errors[-1]:.1%} "
        f"signed_mean={mean(pct_errors):+.1%} (bias direction, if any)"
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

    # Fetch VIXCLS a bit before --start too, so the first measured day has a "yesterday".
    fetch_start = (datetime.strptime(args.start, "%Y-%m-%d") - timedelta(days=10)).strftime("%Y-%m-%d")
    print("Fetching FRED VIXCLS...")
    vix_closes = fetch_vixcls_closes(fred_key, fetch_start, args.end)

    print(f"Fetching {VIXY_PROXY_SYMBOL} daily bars...")
    try:
        vixy_bars = client.get_stock_daily_bars(VIXY_PROXY_SYMBOL, fetch_start, args.end)
    except AlpacaDataError as e:
        print(f"FATAL: could not fetch {VIXY_PROXY_SYMBOL} daily bars: {e}")
        sys.exit(1)
    vixy_closes = {b["t"][:10]: b["c"] for b in vixy_bars}

    dates = list(daterange(args.start, args.end))
    print(f"Measuring VIX proxy accuracy over {len(dates)} weekdays, {args.start} to {args.end}...")

    vixy_pct_errors: list = []
    vol_pct_errors: list = []
    combined_pct_errors: list = []
    skipped_no_prior_day = 0
    skipped_missing_data = 0

    sorted_vix_dates = sorted(vix_closes.keys())

    for i, date in enumerate(dates):
        prior_dates = [d for d in sorted_vix_dates if d < date]
        if not prior_dates:
            skipped_no_prior_day += 1
            continue
        yesterday = prior_dates[-1]

        real_today = vix_closes.get(date)
        real_yesterday = vix_closes.get(yesterday)
        vixy_today = vixy_closes.get(date)
        vixy_yesterday = vixy_closes.get(yesterday)
        if real_today is None or real_yesterday is None or real_yesterday <= 0:
            skipped_missing_data += 1
            continue

        try:
            spy_bars_today = client.get_spy_minute_bars(date)
            spy_bars_yesterday = client.get_spy_minute_bars(yesterday)
        except AlpacaDataError:
            skipped_missing_data += 1
            continue

        estimates = []

        if vixy_today is not None and vixy_yesterday is not None and vixy_yesterday > 0:
            vixy_ratio = vixy_today / vixy_yesterday
            estimate_vixy = real_yesterday * vixy_ratio
            vixy_pct_errors.append((estimate_vixy - real_today) / real_today)
            estimates.append(estimate_vixy)

        vol_today = realized_vol(spy_bars_today)
        vol_yesterday = realized_vol(spy_bars_yesterday)
        if vol_today > 0 and vol_yesterday > 0:
            vol_ratio = vol_today / vol_yesterday
            estimate_vol = real_yesterday * vol_ratio
            vol_pct_errors.append((estimate_vol - real_today) / real_today)
            estimates.append(estimate_vol)

        if len(estimates) == 2:
            combined = sum(estimates) / 2
            combined_pct_errors.append((combined - real_today) / real_today)

        if (i + 1) % 20 == 0:
            print(f"  ...{i + 1}/{len(dates)} dates processed")

    print(f"\nSkipped (no prior VIX day yet): {skipped_no_prior_day}")
    print(f"Skipped (missing data that day): {skipped_missing_data}")
    print("\n=== RESULT: estimated VIX vs REAL FRED VIXCLS close, same day ===")
    summarize_errors(f"{VIXY_PROXY_SYMBOL}-ratio proxy alone", vixy_pct_errors)
    summarize_errors("SPY-realized-vol-ratio proxy alone", vol_pct_errors)
    summarize_errors("average of both proxies", combined_pct_errors)
    print(
        "\nA mean_abs_err of a few percent is a usable regime indicator for"
        "\nentry_filters.vix_in_tradeable_range's wide 12-35 bootstrap band."
        "\nA large signed_mean bias (not just scatter) would mean one proxy"
        "\nsystematically over/under-estimates -- worth correcting before"
        "\nwiring in, same as live_pricing.MEASURED_BIAS_PCT was for Option B."
    )


if __name__ == "__main__":
    main()
