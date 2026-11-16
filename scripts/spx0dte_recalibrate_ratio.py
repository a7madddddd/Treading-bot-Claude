"""Reports the SPY:SPX ratio spx0dte.ratio_calibration measures from
real data (FRED SP500 vs Alpaca SPY daily closes), with the per-day
detail table -- a diagnostic view onto the same measurement
`RatioProvider` makes automatically. There is no hardcoded ratio
constant to compare against anymore (removed from alpaca_client.py,
2026-11-16, after it was found to have drifted +11.82% -- see
docs/trading/decisions.md's D-0095-A follow-up entry); this script
exists to let the Controller SEE the measurement, not to decide
whether to apply it -- the live code already applies it automatically
via RatioProvider.

Run on the VM where the real .env is loaded:

  cd ~/Treading-bot-Claude && set -a && source .env && set +a && \\
  PYTHONPATH=src python3.11 scripts/spx0dte_recalibrate_ratio.py
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from spx0dte.alpaca_client import AlpacaOptionsClient  # noqa: E402
from spx0dte.ratio_calibration import (  # noqa: E402
    DEFAULT_LOOKBACK_DAYS, fetch_sp500_closes, measure_ratio_now,
)
from datetime import datetime, timedelta, timezone  # noqa: E402


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

    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    start_str, end_str = start.isoformat(), end.isoformat()

    print(f"Fetching SP500 daily closes ({start_str} to {end_str}) from FRED...")
    sp500_closes = fetch_sp500_closes(fred_key, start_str, end_str)
    print(f"  got {len(sp500_closes)} SP500 observations")

    client = AlpacaOptionsClient(key_id=key_id, secret_key=secret_key)
    print(f"Fetching SPY daily bars ({start_str} to {end_str}) from Alpaca...")
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

    print("\nCalling the same measure_ratio_now() the live code uses:")
    measured = measure_ratio_now(client, fred_key)
    print(f"  measured median ratio = {measured:.4f} (over {len(ratios)} matched days)")
    print("\nThis is what RatioProvider.get_ratio() returns automatically -- no manual update needed.")


if __name__ == "__main__":
    main()
