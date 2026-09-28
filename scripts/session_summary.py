#!/usr/bin/env python3
"""B26 post-session summary.

Aggregates the full lifetime of a paper-session SQLite database:
  - Total trades opened / closed / still open
  - All proposals with approval outcome
  - All order executions with fill status
  - Realized and unrealized P&L (best-effort from stored fill prices)

Read-only. Safe to run alongside a live session or after it ends.

Usage:
    PYTHONPATH=src python3 scripts/session_summary.py \\
        --db-path ./paper_session.sqlite [--csv]
"""

from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _print_header(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    parser = argparse.ArgumentParser(description="B26 session summary")
    parser.add_argument(
        "--db-path",
        default=os.environ.get("PAPER_SESSION_DB_PATH",
                               "./paper_session.sqlite"),
    )
    parser.add_argument("--csv", action="store_true",
                        help="Emit CSV rows of every proposal and every "
                             "execution to stdout after the summary.")
    args = parser.parse_args()

    if not os.path.isfile(args.db_path):
        print(f"[FAIL] db not found: {args.db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(f"file:{args.db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    print(f"SESSION SUMMARY -- {args.db_path}")
    print(f"generated: {_now_utc().isoformat()}")

    # ---- Trades ---------------------------------------------------
    trades = conn.execute(
        "SELECT trade_id, symbol, initial_order_status, "
        "initial_filled_shares, total_shares, "
        "weighted_avg_entry_price, ladder1_filled, ladder2_filled, "
        "trailing_activated, trailing_floor_price, "
        "original_floor_price, created_at "
        "FROM trades ORDER BY created_at ASC"
    ).fetchall()

    total = len(trades)
    open_states = ("pending", "partially_filled", "filled")
    open_trades = [t for t in trades
                   if t["initial_order_status"] in open_states
                   and (t["total_shares"] or 0) > 0]
    closed_trades = [t for t in trades if t not in open_trades]

    _print_header("TRADES")
    print(f"total trades in db:       {total}")
    print(f"  open (with shares > 0): {len(open_trades)}")
    print(f"  closed or empty:        {len(closed_trades)}")
    status_counts = Counter(t["initial_order_status"] for t in trades)
    for st, n in sorted(status_counts.items()):
        print(f"    initial_order_status={st!r}: {n}")

    ladder_counts = Counter()
    for t in trades:
        if t["ladder1_filled"]:
            ladder_counts["ladder1_filled"] += 1
        if t["ladder2_filled"]:
            ladder_counts["ladder2_filled"] += 1
        if t["trailing_activated"]:
            ladder_counts["trailing_activated"] += 1
    for k, v in sorted(ladder_counts.items()):
        print(f"    {k}: {v}")

    # ---- Proposals -------------------------------------------------
    proposals = conn.execute(
        "SELECT proposal_id, trade_id, symbol, proposed_action, "
        "approval_state, proposal_created_at, "
        "current_price_at_proposal "
        "FROM proposals ORDER BY proposal_created_at ASC"
    ).fetchall()

    _print_header("PROPOSALS")
    print(f"total proposals: {len(proposals)}")
    approval_counts = Counter(p["approval_state"] for p in proposals)
    for st, n in sorted(approval_counts.items()):
        print(f"  {st}: {n}")
    action_counts = Counter(p["proposed_action"] for p in proposals)
    for act, n in sorted(action_counts.items()):
        print(f"  action {act}: {n}")

    # ---- Executions -----------------------------------------------
    executions = conn.execute(
        "SELECT execution_id, proposal_id, trade_id, client_order_id, "
        "side, requested_qty, filled_qty, filled_avg_price, status, "
        "is_broker_terminal, created_at "
        "FROM order_executions ORDER BY created_at ASC"
    ).fetchall()

    _print_header("ORDER EXECUTIONS")
    print(f"total executions: {len(executions)}")
    exec_status_counts = Counter(e["status"] for e in executions)
    for st, n in sorted(exec_status_counts.items()):
        print(f"  status {st!r}: {n}")
    terminal = [e for e in executions if e["is_broker_terminal"]]
    in_flight = [e for e in executions if not e["is_broker_terminal"]]
    print(f"  terminal: {len(terminal)}")
    print(f"  in-flight: {len(in_flight)}")

    # ---- Realized P&L (best-effort per closed trade) --------------
    _print_header("REALIZED P&L (per-trade, best-effort)")
    realized = 0.0
    per_trade = []
    for t in closed_trades:
        cost = ((t["weighted_avg_entry_price"] or 0.0)
                * (t["total_shares"] or 0))
        # Find matching SELL execution for this trade_id
        sell = None
        for e in executions:
            if e["trade_id"] == t["trade_id"] and e["side"] == "sell":
                if sell is None or e["created_at"] > sell["created_at"]:
                    sell = e
        if sell and sell["filled_avg_price"] is not None:
            proceeds = sell["filled_avg_price"] * (sell["filled_qty"] or 0)
            pnl = proceeds - cost
            realized += pnl
            per_trade.append((t["trade_id"], t["symbol"], pnl))
    print(f"realized P&L (over closed trades where SELL is recorded): "
          f"{realized:+.2f} USD")
    if per_trade:
        wins = sum(1 for _, _, p in per_trade if p > 0)
        losses = sum(1 for _, _, p in per_trade if p < 0)
        print(f"  closed with sell recorded: {len(per_trade)} "
              f"({wins} wins / {losses} losses)")

    # ---- CSV export -----------------------------------------------
    if args.csv:
        _print_header("CSV: proposals")
        w = csv.writer(sys.stdout)
        w.writerow(["proposal_id", "trade_id", "symbol",
                    "proposed_action", "approval_state",
                    "proposal_created_at",
                    "current_price_at_proposal"])
        for p in proposals:
            w.writerow([p[k] for k in [
                "proposal_id", "trade_id", "symbol", "proposed_action",
                "approval_state", "proposal_created_at",
                "current_price_at_proposal",
            ]])
        _print_header("CSV: executions")
        w.writerow([])  # blank separator
        w.writerow(["execution_id", "proposal_id", "trade_id", "side",
                    "requested_qty", "filled_qty", "filled_avg_price",
                    "status", "is_broker_terminal", "created_at"])
        for e in executions:
            w.writerow([e[k] for k in [
                "execution_id", "proposal_id", "trade_id", "side",
                "requested_qty", "filled_qty", "filled_avg_price",
                "status", "is_broker_terminal", "created_at",
            ]])

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
