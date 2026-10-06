#!/usr/bin/env python3
"""Read-only: what will the engine do at the next scheduled check?

Answers one question the Controller asked before the 2026-10-06 open:
with positions already open, will the new D-0079 cap of 12 actually let
proposals through where the old cap of 5 blocked them?

It uses the PRODUCTION snapshot builder and the PRODUCTION Layer-1 gate
-- not a reimplementation -- so the answer is what the engine itself
will decide, not an estimate.

Writes nothing. Submits nothing. Safe to run while the engine is live.

    PYTHONPATH=src python3.11 scripts/preflight_caps.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone


def _load_env(path: str = ".env") -> None:
    """Minimal .env reader so the operator does not have to `source` it
    first. Values are never printed -- only the fact that a key is
    present or missing."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key.startswith("export "):
                key = key[len("export "):].strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def main() -> int:
    _load_env()
    missing = [k for k in ("ALPACA_BASE_URL", "ALPACA_API_KEY_ID",
                           "ALPACA_API_SECRET_KEY")
               if not os.environ.get(k)]
    if missing:
        print(f"FATAL: missing env: {', '.join(missing)}")
        return 78

    from engine.engine import Engine
    from persistence.db import connect
    from risk.enforcer import PortfolioRiskEnforcer
    from risk.models import PortfolioRiskLimits
    from risk.portfolio_snapshot import LivePortfolioSnapshotBuilder

    conn = connect("paper_session.sqlite")
    builder = LivePortfolioSnapshotBuilder(
        broker_base_url=os.environ["ALPACA_BASE_URL"],
        broker_key_id=os.environ["ALPACA_API_KEY_ID"],
        broker_secret_key=os.environ["ALPACA_API_SECRET_KEY"],
        sqlite_conn=conn,
    )
    limits = PortfolioRiskLimits()

    try:
        snap = builder()
    except Exception as exc:  # noqa: BLE001
        print(f"FATAL: could not build the portfolio snapshot: "
              f"{type(exc).__name__}: {exc}")
        return 1

    print("=== WHAT THE ENGINE SEES RIGHT NOW ===")
    print(f"  open trades       : {snap.open_trades}")
    print(f"  new trades today  : {snap.new_trades_today}")
    print(f"  equity            : {snap.equity_current:,.2f}")
    print(f"  positions held    : {len(snap.positions)}")
    print()
    print("=== OLD CODE, before today (cap 5) ===")
    old_blocked = snap.open_trades >= 5
    print(f"  {snap.open_trades} open >= 5 ?  {old_blocked}"
          f"   -> {'BLOCKED: no proposals at all' if old_blocked else 'would propose'}")
    print()
    print(f"=== NEW CODE, D-0079 (cap {limits.max_concurrent_trades}) ===")
    print(f"  {snap.open_trades} open >= {limits.max_concurrent_trades} ?"
          f"  {snap.open_trades >= limits.max_concurrent_trades}")
    print(f"  {snap.new_trades_today} today >= {limits.max_daily_new_trades} ?"
          f"  {snap.new_trades_today >= limits.max_daily_new_trades}")
    print(f"  free slots        : "
          f"{max(0, limits.max_concurrent_trades - snap.open_trades)}")
    print(f"  may open today    : "
          f"{max(0, limits.max_daily_new_trades - snap.new_trades_today)}")
    print()

    # The REAL Layer-1 gate from D-0080, not a reimplementation.
    engine = Engine.__new__(Engine)
    engine._risk_enforcer = PortfolioRiskEnforcer(
        limits=limits, snapshot_builder=builder,
    )
    reason = engine._caps_already_exhausted(now=datetime.now(timezone.utc))
    print("=== THE ACTUAL D-0080 LAYER-1 GATE ===")
    if reason:
        print(f"  BLOCKS: {reason}")
    else:
        print("  PROCEEDS -- the caps will not stop a proposal this cycle")
    print()

    # Today's universe vs what is already held: a symbol with an open
    # trade is excluded up-front by _check_watchlist.
    from engine.snapshot_watchlist import _current_effective_date_et
    day = _current_effective_date_et(datetime.now(timezone.utc)).isoformat()
    row = conn.execute(
        "SELECT symbols_json FROM universe_snapshots "
        "WHERE effective_trading_date = ? "
        "ORDER BY snapshot_at DESC LIMIT 1", (day,),
    ).fetchone()
    print(f"=== TODAY'S UNIVERSE ({day}) ===")
    if not row:
        print("  NO SNAPSHOT FOR TODAY -> no new trade today (D-0026 rule)")
        return 0
    universe = [e.get("ticker_as_of_date") for e in json.loads(row[0])]
    held = {r[0] for r in conn.execute("SELECT DISTINCT symbol FROM trades")}
    overlap = sorted(set(universe) & held)
    print(f"  symbols ({len(universe)}): {', '.join(universe)}")
    print(f"  already have a trade row: {', '.join(overlap) or 'NONE'}")
    print(f"  eligible to propose     : "
          f"{len([s for s in universe if s not in overlap])}")
    print()
    print("=== THE ONE GATE THIS CHANGE DOES NOT TOUCH ===")
    print(f"  a candidate must still score >= {Engine._MIN_SCORE:.0f}, and at "
          f"most {Engine._TOP_N_PER_CYCLE} are proposed per cycle.")
    print("  so the caps no longer block -- the scores decide.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
