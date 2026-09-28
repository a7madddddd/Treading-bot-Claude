#!/usr/bin/env python3
"""B26 live session status snapshot.

Reads the paper-session SQLite database in READ-ONLY mode and prints a
snapshot of the current session: engine lock, active trades, pending
proposals, in-flight executions. Safe to run alongside a live session
-- opens the DB with the `?mode=ro` URI so no write lock is taken.

Usage:
    PYTHONPATH=src python3 scripts/session_status.py \\
        --db-path ./paper_session.sqlite
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone


_here = os.path.abspath(os.path.dirname(__file__))
_src = os.path.abspath(os.path.join(_here, "..", "src"))
if _src not in sys.path:
    sys.path.insert(0, _src)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _humanize_age(iso: str) -> str:
    try:
        ts = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return "?"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = _now_utc() - ts
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def main() -> int:
    parser = argparse.ArgumentParser(description="B26 session status")
    parser.add_argument(
        "--db-path",
        default=os.environ.get("PAPER_SESSION_DB_PATH",
                               "./paper_session.sqlite"),
    )
    parser.add_argument("--stale-lock-seconds", type=float, default=90.0,
                        help="Consider engine lock stale if heartbeat older "
                             "than this many seconds (default 90).")
    args = parser.parse_args()

    if not os.path.isfile(args.db_path):
        print(f"[FAIL] db not found: {args.db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(f"file:{args.db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    print(f"=== SESSION STATUS ({args.db_path}) ===")
    print(f"as of: {_now_utc().isoformat()}")

    # Engine lock
    row = conn.execute("SELECT * FROM engine_lock WHERE id = 1").fetchone()
    if row is None:
        print("\n[engine_lock] no engine currently holds the lock")
    else:
        age = _humanize_age(row["heartbeat_at"])
        try:
            hb = datetime.fromisoformat(row["heartbeat_at"])
            if hb.tzinfo is None:
                hb = hb.replace(tzinfo=timezone.utc)
            stale = (_now_utc() - hb).total_seconds() > args.stale_lock_seconds
        except (TypeError, ValueError):
            stale = True
        badge = "STALE" if stale else "ALIVE"
        print(f"\n[engine_lock] {badge}: pid={row['pid']}, host={row['host']!r}, "
              f"started_at={row['started_at']}, "
              f"heartbeat_at={row['heartbeat_at']} (age {age})")

    # Active trades
    rows = conn.execute(
        "SELECT trade_id, symbol, initial_order_status, "
        "initial_filled_shares, total_shares, "
        "weighted_avg_entry_price, ladder1_filled, ladder2_filled, "
        "trailing_activated, trailing_floor_price, original_floor_price, "
        "created_at "
        "FROM trades "
        "WHERE initial_order_status IN "
        "  ('pending', 'partially_filled', 'filled') "
        "ORDER BY created_at DESC"
    ).fetchall()
    print(f"\n[active_trades] {len(rows)} row(s)")
    for r in rows:
        floor = (r["trailing_floor_price"] if r["trailing_activated"]
                 else r["original_floor_price"])
        print(f"  {r['trade_id']} {r['symbol']:6s} "
              f"status={r['initial_order_status']:10s} "
              f"shares={r['total_shares']} "
              f"wae={r['weighted_avg_entry_price']} "
              f"L1={'Y' if r['ladder1_filled'] else '-'} "
              f"L2={'Y' if r['ladder2_filled'] else '-'} "
              f"trail={'Y' if r['trailing_activated'] else '-'} "
              f"floor={floor}")

    # Pending proposals
    rows = conn.execute(
        "SELECT proposal_id, trade_id, symbol, proposed_action, "
        "approval_state, proposal_created_at, current_price_at_proposal "
        "FROM proposals "
        "WHERE approval_state = 'PENDING' "
        "ORDER BY proposal_created_at DESC"
    ).fetchall()
    print(f"\n[pending_proposals] {len(rows)} row(s)")
    for r in rows:
        age = _humanize_age(r["proposal_created_at"])
        print(f"  {r['proposal_id']} {r['symbol']:6s} "
              f"{r['proposed_action']:12s} "
              f"price={r['current_price_at_proposal']} "
              f"age {age}")

    # In-flight executions
    rows = conn.execute(
        "SELECT execution_id, client_order_id, proposal_id, side, "
        "requested_qty, filled_qty, status, is_broker_terminal, "
        "created_at "
        "FROM order_executions "
        "WHERE is_broker_terminal = 0 "
        "ORDER BY created_at DESC"
    ).fetchall()
    print(f"\n[in_flight_executions] {len(rows)} row(s)")
    for r in rows:
        age = _humanize_age(r["created_at"])
        print(f"  {r['execution_id']} side={r['side']} "
              f"qty={r['filled_qty']}/{r['requested_qty']} "
              f"status={r['status']} age {age}")

    # Recent completed executions (last 10)
    rows = conn.execute(
        "SELECT execution_id, client_order_id, side, requested_qty, "
        "filled_qty, filled_avg_price, status, created_at "
        "FROM order_executions "
        "WHERE is_broker_terminal = 1 "
        "ORDER BY created_at DESC LIMIT 10"
    ).fetchall()
    print(f"\n[recent_terminal_executions] last {len(rows)} of many")
    for r in rows:
        age = _humanize_age(r["created_at"])
        print(f"  {r['execution_id']} side={r['side']} "
              f"filled={r['filled_qty']}/{r['requested_qty']} "
              f"@ {r['filled_avg_price']} status={r['status']} "
              f"({age})")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
