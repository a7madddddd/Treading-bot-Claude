"""Empirical accuracy measurement for spx0dte.greeks's "Option B"
reprice-from-stale-quote approach -- NOT part of the automated test
suite (that only checks the math is internally consistent against
itself). This measures against REAL historical option prices whether
backing an implied volatility out of an old quote and re-pricing with
a live underlying actually tracks the real market, or not.

Per this project's "calculate from recorded data, never a guessed
number" rule: this module's docstring explicitly does NOT claim an
accuracy number until this script has been run and the result
recorded. Do not wire greeks.py into any live order-pricing path
before this has run and the Controller has seen the result.

Methodology: for many (date, strike, direction) samples, using PAST
(already-completed, non-recency-restricted) trading days so real
option data is available without the 15-minute delay getting in the
way of the MEASUREMENT itself:
  1. Pick t1 and t2 ~16-20 minutes apart within the same session.
  2. stale_price  = the option's real observed price at t1.
  3. stale_S/live_S = SPY price at t1/t2 x the ratio measured for
     that historical date (spx0dte.ratio_calibration, same function
     the live code uses).
  4. Back out an implied volatility from (stale_price, stale_S, T at t1),
     re-price using (live_S, T at t2) with that SAME volatility.
  5. Compare the repriced estimate against the REAL observed price at
     t2 -- the actual ground truth, not a model.

Reports mean/median absolute error and mean/median error as a
percentage of the real price.

Run on the VM where the real .env is loaded:

  cd ~/Treading-bot-Claude && set -a && source .env && set +a && \\
  PYTHONPATH=src python3.11 scripts/spx0dte_validate_greeks.py --start 2026-10-01 --end 2026-11-13
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from statistics import mean, median

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from spx0dte.alpaca_client import AlpacaOptionsClient, AlpacaDataError  # noqa: E402
from spx0dte.greeks import OptionType, reprice_from_stale_quote  # noqa: E402
from spx0dte.ratio_calibration import RatioProvider  # noqa: E402

RISK_FREE_RATE = 0.045  # same approximation already used throughout this module's examples
SAMPLE_GAP_MINUTES = 18
MARKET_CLOSE_UTC_HOUR = 21  # 4:00pm ET in EST (UTC-5); DST days are off by ~1h, acceptable for this estimate


def _parse_ts(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def daterange(start: str, end: str):
    d0 = datetime.strptime(start, "%Y-%m-%d")
    d1 = datetime.strptime(end, "%Y-%m-%d")
    cur = d0
    while cur <= d1:
        if cur.weekday() < 5:
            yield cur.strftime("%Y-%m-%d")
        cur += timedelta(days=1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--max-samples-per-day", type=int, default=3)
    args = parser.parse_args()

    key_id = os.environ.get("ALPACA_API_KEY_ID", "")
    secret_key = os.environ.get("ALPACA_API_SECRET_KEY", "")
    fred_key = os.environ.get("FRED_API_KEY", "")
    if not key_id or not secret_key or not fred_key:
        print("FATAL: ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY / FRED_API_KEY must all be set.")
        sys.exit(1)

    client = AlpacaOptionsClient(key_id=key_id, secret_key=secret_key)
    ratio_provider = RatioProvider(client=client, fred_api_key=fred_key)

    errors_abs = []
    errors_pct = []
    notes = []

    for date in daterange(args.start, args.end):
        try:
            spy_bars = client.get_spy_minute_bars(date)
        except AlpacaDataError as e:
            notes.append(f"{date}: SPY fetch failed: {e}")
            continue
        if len(spy_bars) < 60:
            continue  # not enough of the session to sample from

        ratio = ratio_provider.get_ratio(now=datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc))
        expiry = datetime.strptime(date, "%Y-%m-%d").replace(
            hour=MARKET_CLOSE_UTC_HOUR, minute=0, second=0, tzinfo=timezone.utc,
        )

        samples_today = 0
        # Sample a handful of (t1, t2) pairs spread through the session.
        candidate_indices = range(30, len(spy_bars) - SAMPLE_GAP_MINUTES - 5, 45)
        for i in candidate_indices:
            if samples_today >= args.max_samples_per_day:
                break
            bar1 = spy_bars[i]
            t1 = _parse_ts(bar1["t"])
            t2 = t1 + timedelta(minutes=SAMPLE_GAP_MINUTES)

            bar2 = min(spy_bars[i:i + SAMPLE_GAP_MINUTES + 3], key=lambda b: abs((_parse_ts(b["t"]) - t2).total_seconds()))
            spy_at_t1, spy_at_t2 = bar1["c"], bar2["c"]

            approx_spx_t1 = spy_at_t1 * ratio
            strike = round(approx_spx_t1 / 25) * 25  # near ATM, a common SPX strike increment
            for call_put, option_type in (("C", OptionType.CALL), ("P", OptionType.PUT)):
                symbol = f"SPXW{date[2:4]}{date[5:7]}{date[8:10]}{call_put}{strike * 1000:08d}"
                try:
                    bars = client.get_option_bars(
                        symbol, t1.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        (t2 + timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ"), limit=50,
                    )
                except AlpacaDataError:
                    continue
                if not bars:
                    continue

                bars_sorted = sorted(bars, key=lambda b: b["t"])
                bar_at_t1 = bars_sorted[0]
                bar_at_t2 = min(bars_sorted, key=lambda b: abs((_parse_ts(b["t"]) - t2).total_seconds()))
                if bar_at_t1["t"] == bar_at_t2["t"]:
                    continue  # need two distinct observed prices

                stale_price = bar_at_t1["c"]
                real_price_t2 = bar_at_t2["c"]
                stale_T = max((expiry - _parse_ts(bar_at_t1["t"])).total_seconds(), 1) / 86400 / 365
                live_T = max((expiry - _parse_ts(bar_at_t2["t"])).total_seconds(), 1) / 86400 / 365

                result = reprice_from_stale_quote(
                    stale_market_price=stale_price, stale_S=approx_spx_t1, stale_T=stale_T,
                    live_S=spy_at_t2 * ratio, live_T=live_T, K=strike, r=RISK_FREE_RATE,
                    option_type=option_type,
                )
                if result is None:
                    continue

                err = result.price - real_price_t2
                errors_abs.append(err)
                errors_pct.append(100 * err / real_price_t2 if real_price_t2 > 0 else None)
                samples_today += 1

    valid_pct_errors = [e for e in errors_pct if e is not None]
    print(f"Total samples: {len(errors_abs)}")
    print(f"Notes/skips: {len(notes)}")
    if not errors_abs:
        print("No samples collected -- nothing to report.")
        return

    abs_errors = [abs(e) for e in errors_abs]
    print("\n=== RESULT (estimate - real observed price) ===")
    print(f"mean error:        {mean(errors_abs):+.4f}")
    print(f"median error:      {median(errors_abs):+.4f}")
    print(f"mean abs error:    {mean(abs_errors):.4f}")
    print(f"median abs error:  {median(abs_errors):.4f}")
    if valid_pct_errors:
        abs_pct = [abs(e) for e in valid_pct_errors]
        print(f"mean abs error %:   {mean(abs_pct):.1f}%")
        print(f"median abs error %: {median(abs_pct):.1f}%")
    print(
        "\nThis is the real, measured accuracy of the reprice-from-stale-quote"
        "\napproach -- record it in docs/trading/decisions.md before deciding"
        "\nwhether to wire greeks.py into any live order-pricing path."
    )


if __name__ == "__main__":
    main()
