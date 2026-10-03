"""Pure ladder strategy backtest (D-0004 + D-0008 standalone).

Isolates the APPROVED strategy without the ranker. For each stock in
the universe, opens a position every Monday and simulates the exact
ladder + floor + trailing exit logic over the next 90 days. Compares
mean return vs a passive buy-and-hold on the same entries.

Strategy under test:
  * Initial entry: 1 share at Monday close.
  * Ladder 1: if price drops -5% from entry, add 1 share.
  * Ladder 2: if price drops -8% from entry, add 1 share.
  * Floor:    if price drops -10% from avg entry, exit ALL (stop loss).
  * Trailing: when position hits +10% peak, trailing floor activates;
              if price then drops -5% from the running peak, exit.
  * Timeout:  exit at close of day 90 if neither floor nor trailing fired.

Zero paid APIs. Uses the Polygon aggregates we already know work.

Usage:
  cd ~/Treading-bot-Claude && set -a && source .env && set +a && \\
  PYTHONPATH=src python3.11 scripts/run_ladder_backtest.py \\
      --symbols AAPL,MSFT,NVDA,TSLA,GOOGL,META,AMZN,JPM,UNH,V,XOM,SPY \\
      --months 12

Writes ./ladder_backtest_result.json.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from marketdata.polygon_source import PolygonSource   # noqa: E402


LADDER1_PCT = -5.0
LADDER2_PCT = -8.0
FLOOR_PCT   = -10.0
TRAIL_ACTIVATE_PCT = 10.0
TRAIL_RATCHET_PCT  = -5.0
TIMEOUT_DAYS       = 90


@dataclass
class LadderTrade:
    symbol: str
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    shares: int                  # 1, 2 or 3 (ladder adds)
    exit_reason: str
    gross_return_pct: float      # per-share weighted by shares
    buy_and_hold_pct: float      # same window, 1 share held, no exit logic


def _simulate_one(sym: str, entry_idx: int,
                  closes: List[Tuple[date, float]]) -> Optional[LadderTrade]:
    """Walks the ladder/floor/trailing logic from entry_idx forward.
    Returns None if there isn't enough data."""
    if entry_idx + TIMEOUT_DAYS >= len(closes):
        return None

    entry_date, entry_px = closes[entry_idx]
    shares = 1
    cost_basis = entry_px
    ladder1_filled = False
    ladder2_filled = False
    trailing_armed = False
    peak = entry_px

    for offset in range(1, TIMEOUT_DAYS + 1):
        d, px = closes[entry_idx + offset]

        # Peak tracking + trailing activation
        if px > peak:
            peak = px
        if not trailing_armed and px >= entry_px * (1 + TRAIL_ACTIVATE_PCT / 100):
            trailing_armed = True

        # Ladder adds (reduce avg cost)
        if not ladder1_filled and px <= entry_px * (1 + LADDER1_PCT / 100):
            cost_basis = (cost_basis * shares + px) / (shares + 1)
            shares += 1
            ladder1_filled = True
        if not ladder2_filled and px <= entry_px * (1 + LADDER2_PCT / 100):
            cost_basis = (cost_basis * shares + px) / (shares + 1)
            shares += 1
            ladder2_filled = True

        # Trailing exit (checked FIRST — protective floor priority)
        if trailing_armed and px <= peak * (1 + TRAIL_RATCHET_PCT / 100):
            final_bh = (closes[entry_idx + TIMEOUT_DAYS][1] - entry_px) / entry_px * 100
            return LadderTrade(
                symbol=sym, entry_date=entry_date.isoformat(),
                entry_price=round(entry_px, 4),
                exit_date=d.isoformat(), exit_price=round(px, 4),
                shares=shares, exit_reason="trailing",
                gross_return_pct=round((px - cost_basis) / cost_basis * 100, 3),
                buy_and_hold_pct=round(final_bh, 3),
            )
        # Original floor (ONLY active before trailing)
        if not trailing_armed and px <= cost_basis * (1 + FLOOR_PCT / 100):
            final_bh = (closes[entry_idx + TIMEOUT_DAYS][1] - entry_px) / entry_px * 100
            return LadderTrade(
                symbol=sym, entry_date=entry_date.isoformat(),
                entry_price=round(entry_px, 4),
                exit_date=d.isoformat(), exit_price=round(px, 4),
                shares=shares, exit_reason="floor",
                gross_return_pct=round((px - cost_basis) / cost_basis * 100, 3),
                buy_and_hold_pct=round(final_bh, 3),
            )

    # Timeout exit at day 90
    exit_date, exit_px = closes[entry_idx + TIMEOUT_DAYS]
    bh = (exit_px - entry_px) / entry_px * 100
    return LadderTrade(
        symbol=sym, entry_date=entry_date.isoformat(),
        entry_price=round(entry_px, 4),
        exit_date=exit_date.isoformat(), exit_price=round(exit_px, 4),
        shares=shares, exit_reason="timeout",
        gross_return_pct=round((exit_px - cost_basis) / cost_basis * 100, 3),
        buy_and_hold_pct=round(bh, 3),
    )


def _closes(pg: PolygonSource, syms: List[str],
            start: date, end: date,
            throttle_s: float = 13.0) -> Dict[str, List[Tuple[date, float]]]:
    out: Dict[str, List[Tuple[date, float]]] = {}
    for i, s in enumerate(syms, 1):
        try:
            bars = pg.get_aggregates(s, 1, "day", start, end, adjusted=True)
        except Exception:  # noqa: BLE001
            bars = []
        rows = [(d, float(row.get("c"))) for d, row in bars
                if isinstance(row, dict) and isinstance(row.get("c"), (int, float))]
        print(f"  [{i:>3}/{len(syms)}] {s:<6} {len(rows):>4} bars", flush=True)
        if not rows and i < len(syms):
            print(f"      ⚠ rate-limited; waiting 65s and retrying...",
                  flush=True)
            time.sleep(65)
            try:
                bars = pg.get_aggregates(s, 1, "day", start, end, adjusted=True)
            except Exception:  # noqa: BLE001
                bars = []
            rows = [(d, float(row.get("c"))) for d, row in bars
                    if isinstance(row, dict)
                    and isinstance(row.get("c"), (int, float))]
            print(f"      retry: {len(rows)} bars", flush=True)
        out[s] = sorted(rows, key=lambda t: t[0])
        if i < len(syms):
            time.sleep(throttle_s)
    return out


def _summarize(trades: List[LadderTrade], label: str,
               field: str = "gross_return_pct") -> dict:
    if not trades:
        return {"label": label, "n": 0}
    rets = [getattr(t, field) for t in trades]
    mean = sum(rets) / len(rets)
    wins = sum(1 for r in rets if r > 0) / len(rets) * 100
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    stdev = var ** 0.5
    return {
        "label": label, "n": len(trades),
        "mean_pct": round(mean, 3),
        "median_pct": round(sorted(rets)[len(rets) // 2], 3),
        "win_rate_pct": round(wins, 2),
        "best_pct": round(max(rets), 3),
        "worst_pct": round(min(rets), 3),
        "sharpe_like": round(mean / stdev if stdev > 0 else 0.0, 3),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Pure ladder strategy backtest")
    p.add_argument("--symbols",
                   default="AAPL,MSFT,NVDA,TSLA,GOOGL,META,AMZN,JPM,UNH,V,XOM,SPY")
    p.add_argument("--months", type=int, default=12)
    p.add_argument("--out", default="./ladder_backtest_result.json")
    args = p.parse_args()

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    end = date.today()
    data_start = end - timedelta(days=args.months * 31 + 100)

    pg = PolygonSource.from_env()
    if pg is None:
        print("ERROR: POLYGON_API_KEY unset.", file=sys.stderr)
        return 2

    print(f"Universe:   {len(syms)} symbols")
    print(f"Window:     {data_start} → {end}  ({args.months} months + 100d buffer)")
    print(f"Rules:      L1={LADDER1_PCT}% L2={LADDER2_PCT}% "
          f"Floor={FLOOR_PCT}% TrailAct=+{TRAIL_ACTIVATE_PCT}% "
          f"TrailDown={TRAIL_RATCHET_PCT}% Timeout={TIMEOUT_DAYS}d")
    print("\nFetching Polygon aggregates...")

    closes = _closes(pg, syms, data_start, end)
    print()

    sim_start = end - timedelta(days=args.months * 31)
    all_trades: List[LadderTrade] = []
    for sym, bars in closes.items():
        if len(bars) < 100:
            continue
        # Entry triggers: every Monday within sim_start → (end - 90d)
        for i, (d, _) in enumerate(bars):
            if d < sim_start:
                continue
            if d.weekday() != 0:   # 0 = Monday
                continue
            t = _simulate_one(sym, i, bars)
            if t is not None:
                all_trades.append(t)

    strat = _summarize(all_trades, "ladder_strategy", "gross_return_pct")
    bh = _summarize(all_trades, "buy_and_hold_90d", "buy_and_hold_pct")

    print("=" * 60)
    print("  PURE LADDER STRATEGY BACKTEST")
    print("=" * 60)
    print(f"  Trades simulated:         {len(all_trades)}")
    print("-" * 60)
    print(f"  LADDER     n={strat['n']:>4}  "
          f"mean={strat.get('mean_pct', 0):>+6.2f}%  "
          f"win={strat.get('win_rate_pct', 0):>5.2f}%  "
          f"sharpe={strat.get('sharpe_like', 0):>+5.2f}")
    print(f"  B&H 90d    n={bh['n']:>4}  "
          f"mean={bh.get('mean_pct', 0):>+6.2f}%  "
          f"win={bh.get('win_rate_pct', 0):>5.2f}%  "
          f"sharpe={bh.get('sharpe_like', 0):>+5.2f}")
    edge = strat.get("mean_pct", 0) - bh.get("mean_pct", 0)
    print("-" * 60)
    print(f"  EDGE vs B&H:              {edge:+.2f}%")
    # Exit reason breakdown
    reasons: Dict[str, int] = {}
    for t in all_trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    print(f"  Exit reasons:             {reasons}")
    print("=" * 60)
    if edge > 0.5:
        print("✅ LADDER HAS EDGE vs passive buy-and-hold.")
    elif edge > -0.5:
        print("⚠ LADDER NEUTRAL vs passive buy-and-hold.")
    else:
        print("❌ LADDER UNDERPERFORMS passive buy-and-hold.")

    try:
        with open(args.out, "w") as f:
            json.dump({
                "window_start": sim_start.isoformat(),
                "window_end": end.isoformat(),
                "params": {
                    "ladder1_pct": LADDER1_PCT, "ladder2_pct": LADDER2_PCT,
                    "floor_pct": FLOOR_PCT,
                    "trail_activate_pct": TRAIL_ACTIVATE_PCT,
                    "trail_ratchet_pct": TRAIL_RATCHET_PCT,
                    "timeout_days": TIMEOUT_DAYS,
                },
                "stats": {"ladder": strat, "buy_hold": bh,
                          "edge_pct": round(edge, 3),
                          "exit_reasons": reasons},
                "trades": [asdict(t) for t in all_trades],
            }, f, indent=2)
        print(f"\nFull result → {args.out}")
    except OSError as exc:
        print(f"\n⚠ Could not write {args.out}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
