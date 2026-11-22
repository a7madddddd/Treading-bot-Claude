#!/usr/bin/env python3
"""Measures, on REAL historical orders, how much P-104's bug actually
cost -- i.e. how far the recorded entry_price/exit_price (the
submitted estimate, before the 2026-11-19 fix) was from the broker's
own confirmed filled_avg_price for the SAME orders.

Read-only: looks up each past order by its client_order_id via
`BrokerClient.get_order_by_client_order_id` (a GET, submits nothing),
against whatever is already in `spx0dte.sqlite`. Safe to run any time,
market open or closed, and safe to run while a live session is also
running (reads only).

Per `.claude/rules/03-research-and-workflow.md` ("calculate from
recorded data, never a guessed number"): this is the BACKTEST PLAN
already promised in docs/trading/decisions.md for P-104 -- quantifying
the real size of the bug on real orders, instead of leaving the
measured-vs-estimated gap as a theoretical concern.

Usage (on the Oracle VM, same .env the session itself sources):
    cd ~/Treading-bot-Claude && set -a && source .env && set +a && \\
    PYTHONPATH=src python3.11 scripts/spx0dte_measure_entry_fill_gap.py \\
        --db-path spx0dte.sqlite
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import statistics
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_REPO_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from execution.alpaca_broker_client import AlpacaBrokerClient  # noqa: E402


def _required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        print(f"FATAL: {name} is not set -- did you `source .env`?", file=sys.stderr)
        sys.exit(78)
    return value


def _pct(recorded: float, real: float) -> float:
    if real == 0:
        return float("nan")
    return (recorded - real) / real * 100


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default="spx0dte.sqlite")
    args = parser.parse_args()

    key_id = _required_env("ALPACA_API_KEY_ID")
    secret_key = _required_env("ALPACA_API_SECRET_KEY")
    base_url = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    broker = AlpacaBrokerClient(base_url=base_url, key_id=key_id, secret_key=secret_key)

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM spx_positions ORDER BY entry_time").fetchall()
    conn.close()

    if not rows:
        print("No rows in spx_positions -- nothing to measure.")
        return 0

    entry_gaps = []
    exit_gaps = []
    no_fill_entry = 0
    no_fill_exit = 0
    lookup_failed = 0

    print(f"{len(rows)} position row(s) found. Looking up each order's real fill via Alpaca...\n")

    for row in rows:
        proposal_id = row["proposal_id"]
        recorded_entry = row["entry_price"]
        try:
            entry_state = broker.get_order_by_client_order_id(proposal_id)
        except Exception as exc:  # noqa: BLE001 -- a lookup failure must not stop the measurement
            print(f"  {proposal_id}: entry lookup failed ({exc}) -- skipped")
            lookup_failed += 1
            entry_state = None

        if entry_state is None:
            print(f"  {proposal_id}: no order found for entry client_order_id -- skipped")
        elif entry_state.filled_avg_price is None:
            no_fill_entry += 1
            print(f"  {proposal_id}: entry order has no fill price yet (status={entry_state.status})")
        else:
            real_entry = entry_state.filled_avg_price
            gap = recorded_entry - real_entry
            entry_gaps.append(gap)
            print(
                f"  {proposal_id}: entry recorded={recorded_entry:.4f} real_fill={real_entry:.4f} "
                f"gap={gap:+.4f} ({_pct(recorded_entry, real_entry):+.1f}%)"
            )

        if row["is_closed"] and row["exit_price"] is not None:
            exit_client_order_id = f"{proposal_id}-exit"
            try:
                exit_state = broker.get_order_by_client_order_id(exit_client_order_id)
            except Exception as exc:  # noqa: BLE001
                print(f"  {proposal_id}: exit lookup failed ({exc}) -- skipped")
                lookup_failed += 1
                exit_state = None

            if exit_state is None:
                print(f"  {proposal_id}: no order found for exit client_order_id -- skipped")
            elif exit_state.filled_avg_price is None:
                no_fill_exit += 1
                print(f"  {proposal_id}: exit order has no fill price yet (status={exit_state.status})")
            else:
                real_exit = exit_state.filled_avg_price
                recorded_exit = row["exit_price"]
                gap = recorded_exit - real_exit
                exit_gaps.append(gap)
                print(
                    f"  {proposal_id}: exit  recorded={recorded_exit:.4f} real_fill={real_exit:.4f} "
                    f"gap={gap:+.4f} ({_pct(recorded_exit, real_exit):+.1f}%)"
                )

    def _report(label: str, gaps: list) -> None:
        print(f"\n{label}: n={len(gaps)}")
        if not gaps:
            print("  (no comparable rows)")
            return
        abs_gaps = [abs(g) for g in gaps]
        print(f"  mean gap:      {statistics.mean(gaps):+.4f}")
        print(f"  median gap:    {statistics.median(gaps):+.4f}")
        print(f"  mean |gap|:    {statistics.mean(abs_gaps):.4f}")
        print(f"  median |gap|:  {statistics.median(abs_gaps):.4f}")

    print("\n" + "=" * 60)
    _report("ENTRY gaps (recorded - real fill)", entry_gaps)
    _report("EXIT gaps (recorded - real fill)", exit_gaps)
    print(
        f"\nSkipped: {no_fill_entry} entr{'y' if no_fill_entry == 1 else 'ies'} with no fill yet, "
        f"{no_fill_exit} exit(s) with no fill yet, {lookup_failed} lookup failure(s)."
    )
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
