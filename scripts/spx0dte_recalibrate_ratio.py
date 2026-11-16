"""Recalibrates spx0dte.alpaca_client.APPROX_SPY_TO_SPX_RATIO from real
data -- per this project's "calculate from recorded data, never a
guessed number" rule, and because D-0095-A's live verification found
a found strike 665 points from the estimate (the ratio had drifted
further since it was last set).

Alpaca serves no SPX index-level data at all (confirmed earlier, see
D-0095's design notes) -- there is no way to measure "true SPX" from
Alpaca itself. Uses FRED's SP500 daily series instead (free, official,
the same FRED_API_KEY already in .env.example for D-0042), paired
against SPY daily closes from Alpaca for the same dates -- not a new
dependency, not a new ToS question, just a second use of a source
already approved for this project.

Run on the VM where the real .env is loaded:

  cd ~/Treading-bot-Claude && set -a && source .env && set +a && \\
  PYTHONPATH=src python3.11 scripts/spx0dte_recalibrate_ratio.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from statistics import median

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from spx0dte.alpaca_client import AlpacaOptionsClient, APPROX_SPY_TO_SPX_RATIO  # noqa: E402

FRED_BASE_URL = "https://api.stlouisfed.org/fred"
LOOKBACK_DAYS = 30  # calendar days; ~20 trading days


def fetch_sp500_closes(api_key: str, start_date: str, end_date: str) -> dict:
    """Returns {date_str: close_float} from FRED's SP500 daily series."""
    qs = urllib.parse.urlencode({
        "series_id": "SP500", "api_key": api_key, "file_type": "json",
        "observation_start": start_date, "observation_end": end_date,
    })
    request = urllib.request.Request(f"{FRED_BASE_URL}/series/observations?{qs}")
    with urllib.request.urlopen(request, timeout=15) as response:
        body = json.loads(response.read().decode("utf-8"))

    closes = {}
    for obs in body.get("observations", []):
        value = obs.get("value")
        if value in (None, ".", ""):
            continue  # FRED marks a missing observation as "."
        try:
            closes[obs["date"]] = float(value)
        except ValueError:
            continue
    return closes


def main() -> None:
    fred_key = os.environ.get("FRED_API_KEY", "")
    if not fred_key:
        print("FATAL: FRED_API_KEY not set -- did you `source .env`?")
        sys.exit(1)
    key_id = os.environ.get("ALPACA_API_KEY_ID", "")
    secret_key = os.environ.get("ALPACA_API_SECRET_KEY", "")
    if not key_id or not secret_key:
        print("FATAL: ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY not set.")
        sys.exit(1)

    end = datetime.now().date()
    start = end - timedelta(days=LOOKBACK_DAYS)
    start_str, end_str = start.isoformat(), end.isoformat()

    print(f"Fetching SP500 daily closes ({start_str} to {end_str}) from FRED...")
    sp500_closes = fetch_sp500_closes(fred_key, start_str, end_str)
    print(f"  got {len(sp500_closes)} SP500 observations")

    print(f"Fetching SPY daily bars ({start_str} to {end_str}) from Alpaca...")
    client = AlpacaOptionsClient(key_id=key_id, secret_key=secret_key)
    spy_bars = client.get_spy_daily_bars(start_str, end_str)
    print(f"  got {len(spy_bars)} SPY bars")

    ratios = []
    print("\ndate         SPY_close   SP500_close   ratio")
    for bar in spy_bars:
        date_str = bar["t"][:10]
        spy_close = bar["c"]
        sp500_close = sp500_closes.get(date_str)
        if sp500_close is None or spy_close <= 0:
            continue
        ratio = sp500_close / spy_close
        ratios.append(ratio)
        print(f"{date_str}   {spy_close:9.2f}   {sp500_close:11.2f}   {ratio:.4f}")

    if not ratios:
        print("\nFATAL: no matched (SPY, SP500) date pairs -- nothing to compute.")
        sys.exit(1)

    new_ratio = median(ratios)
    print(f"\nCurrent hardcoded APPROX_SPY_TO_SPX_RATIO = {APPROX_SPY_TO_SPX_RATIO}")
    print(f"Measured median ratio over {len(ratios)} matched days = {new_ratio:.4f}")
    print(f"Drift: {100 * (new_ratio - APPROX_SPY_TO_SPX_RATIO) / APPROX_SPY_TO_SPX_RATIO:+.2f}%")
    print(
        "\nIf this looks like a real, sustained drift (not day-to-day noise),"
        "\nupdate APPROX_SPY_TO_SPX_RATIO in src/spx0dte/alpaca_client.py to"
        f"\n{new_ratio:.4f} and record the change with this measurement in"
        "\ndocs/trading/decisions.md, per D-0095's own calibration note."
    )


if __name__ == "__main__":
    main()
