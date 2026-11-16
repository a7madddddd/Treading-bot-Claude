"""Manual, one-time diagnostic for D-0095-A: confirms Alpaca actually
accepts a real SPXW OCC option symbol through the UNCHANGED
`AlpacaBrokerClient.submit_order()` path -- the "digits aren't cased
characters" finding behind reusing that method as-is was read from the
code, not yet tested against the live API. This script is that test.

NOT part of the automated test suite (tests/spx0dte/ already covers
the logic against fakes) -- this is a deliberate, small REAL order on
the PAPER account, run by hand, once, before relying on this path.

Submits ONE contract at a price close to the option's last observed
price (not a lowball price meant to avoid a fill -- paper trading has
no real money at risk, so there is no safety reason to avoid a fill;
the point is confirming Alpaca's acceptance of the order itself).

Run on the VM where the real .env is loaded (per deploy/README.md's
own convention):

  cd ~/Treading-bot-Claude && set -a && source .env && set +a && \\
  PYTHONPATH=src python3.11 scripts/spx0dte_verify_order.py [call|put]

Safe to re-run: each run submits a NEW order with a fresh
client_order_id (uses the real broker's idempotency only to prevent a
genuine accidental double-submit within one run, not across runs).
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from execution.alpaca_broker_client import AlpacaBrokerClient  # noqa: E402
from spx0dte.alpaca_client import AlpacaOptionsClient, APPROX_SPY_TO_SPX_RATIO  # noqa: E402
from spx0dte.execution import submit_entry_order  # noqa: E402


def main() -> None:
    call_put = "C"
    if len(sys.argv) > 1 and sys.argv[1].lower() == "put":
        call_put = "P"

    key_id = os.environ.get("ALPACA_API_KEY_ID", "")
    secret_key = os.environ.get("ALPACA_API_SECRET_KEY", "")
    base_url = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    if not key_id or not secret_key:
        print("FATAL: ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY not set -- did you `source .env`?")
        sys.exit(1)

    broker = AlpacaBrokerClient(base_url=base_url, key_id=key_id, secret_key=secret_key)
    options_client = AlpacaOptionsClient(key_id=key_id, secret_key=secret_key)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"Fetching today's ({today}) SPY bars to estimate the SPX level...")
    spy_bars = options_client.get_spy_minute_bars(today)
    if not spy_bars:
        print("FATAL: no SPY bars for today (market closed? run during US market hours).")
        sys.exit(1)
    last_spy_price = spy_bars[-1]["c"]
    approx_spx = last_spy_price * APPROX_SPY_TO_SPX_RATIO
    print(f"Last SPY price: {last_spy_price:.2f}  ->  approx SPX: {approx_spx:.0f}")

    # Alpaca's free Basic tier restricts OPTIONS data to 15+ minutes
    # old -- there is no free real-time feed for options the way
    # "feed=iex" gives stocks (see alpaca_client.get_spy_minute_bars's
    # docstring for that stock-side fix). Searching/pricing around a
    # moment 16 minutes ago, not "now", stays inside the free tier.
    delayed_now = datetime.now(timezone.utc) - timedelta(minutes=16)
    delayed_now_iso = delayed_now.strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"Searching for a SPXW {call_put} strike near {approx_spx:.0f}, as of ~16 min ago (free-tier options delay)...")
    found = options_client.find_nearest_traded_strike(today, approx_spx, call_put, around_time_iso=delayed_now_iso)
    if found is None:
        print("FATAL: no traded SPXW strike found near the estimate -- try the other side (call/put), or outside market hours nothing will have traded today.")
        sys.exit(1)
    strike, occ_symbol = found
    print(f"Found: strike={strike}  symbol={occ_symbol}")

    bars = options_client.get_option_bars(
        occ_symbol,
        (delayed_now - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        delayed_now_iso,
        limit=50,
    )
    if not bars:
        print("FATAL: found the strike but could not fetch a recent price for it.")
        sys.exit(1)
    last_price = bars[-1]["c"]
    print(f"Last observed price: {last_price:.2f}")

    client_order_id = f"SPX-VERIFY-{uuid.uuid4().hex[:10]}"
    print(f"\nSubmitting 1 contract, client_order_id={client_order_id} ...")
    result = submit_entry_order(
        broker, client_order_id=client_order_id, occ_symbol=occ_symbol,
        quantity=1, limit_price=last_price,
    )

    if result.success:
        print("\nSUCCESS -- Alpaca accepted the order.")
        print(f"  broker_order_id = {result.order_state.broker_order_id}")
        print(f"  status          = {result.order_state.status}")
    else:
        print("\nFAILED -- Alpaca did not accept the order (or the outcome was ambiguous).")
        print(f"  error = {result.error}")
        sys.exit(1)


if __name__ == "__main__":
    main()
