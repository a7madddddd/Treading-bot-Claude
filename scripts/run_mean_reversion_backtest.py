"""Mean-reversion ranker backtest (opposite philosophy to momentum).

Hypothesis: since the approved D-0004 strategy is MEAN REVERSION
(buy on dips), the ranker should SELECT stocks likely to drop + rebound
— i.e. oversold, below moving-average, high-volatility names — rather
than momentum leaders.

This script runs the existing HistoricalSimulator with a mean-reversion
scorer that REWARDS:
  * negative recent returns (5d/30d/90d) — the "sold off" dimension
  * high volatility — ladder benefits from larger drops
  * negative relative strength vs SPY — laggards
and PENALIZES:
  * strongly positive momentum — likely to keep climbing without a dip

Uses the same 12-month Polygon data as the momentum run and reports
the same metrics so results are directly comparable.

Usage:
  cd ~/Treading-bot-Claude && set -a && source .env && set +a && \\
  PYTHONPATH=src python3.11 scripts/run_mean_reversion_backtest.py \\
      --symbols AAPL,MSFT,NVDA,TSLA,GOOGL,META,AMZN,JPM,UNH,V,XOM,SPY \\
      --months 12 --picks-per-day 3

Writes ./mean_reversion_backtest_result.json.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, timedelta
from typing import Dict, List, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from backtest.historical_simulator import (                 # noqa: E402
    HistoricalSimulator, ReplayFeatures,
)
from marketdata.polygon_source import PolygonSource          # noqa: E402


def _mean_reversion_scorer(feats: ReplayFeatures) -> Tuple[float, Dict[str, float]]:
    """Rewards oversold / high-vol / lagging stocks."""
    bd: Dict[str, float] = {}

    # "Reversion appetite" — large negative recent returns score HIGHER.
    # Weight 30 across 3 look-backs.
    parts = []
    for r in (feats.return_5d_pct, feats.return_30d_pct, feats.return_90d_pct):
        if r is not None:
            # -10% return → 1.0; +10% return → 0.0. Clamp.
            clamped = max(-10.0, min(10.0, r))
            parts.append((10.0 - clamped) / 20.0)
    bd["oversold"] = (sum(parts) / len(parts)) * 30.0 if parts else 0.0

    # Negative rel-strength vs SPY → +10. Weight 10.
    if feats.rel_strength_30d_pct is not None:
        clamped = max(-5.0, min(5.0, feats.rel_strength_30d_pct))
        bd["lagging"] = ((5.0 - clamped) / 10.0) * 10.0
    else:
        bd["lagging"] = 0.0

    # High volatility → more drop opportunities. Weight 15.
    if feats.volatility_30d_pct is not None:
        clamped = max(10.0, min(80.0, feats.volatility_30d_pct))
        bd["volatility"] = ((clamped - 10.0) / 70.0) * 15.0
    else:
        bd["volatility"] = 0.0

    total = sum(bd.values())
    return max(0.0, min(100.0, total)), bd


def _closes(pg: PolygonSource, syms: List[str], start: date, end: date,
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
        out[s] = rows
        if i < len(syms):
            time.sleep(throttle_s)
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Mean-reversion ranker backtest")
    p.add_argument("--symbols",
                   default="AAPL,MSFT,NVDA,TSLA,GOOGL,META,AMZN,JPM,UNH,V,XOM,SPY")
    p.add_argument("--months", type=int, default=12)
    p.add_argument("--picks-per-day", type=int, default=3)
    p.add_argument("--forward-days", type=int, default=30)
    p.add_argument("--out", default="./mean_reversion_backtest_result.json")
    args = p.parse_args()

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if "SPY" not in syms:
        syms.append("SPY")

    pg = PolygonSource.from_env()
    if pg is None:
        print("ERROR: POLYGON_API_KEY unset.", file=sys.stderr)
        return 2

    end = date.today()
    data_start = end - timedelta(days=args.months * 31 + 150)
    sim_start = end - timedelta(days=args.months * 31)

    print(f"Universe:    {len(syms)} symbols")
    print(f"Window:      {sim_start} → {end}")
    print(f"Picks/day:   {args.picks_per_day}")
    print(f"Forward:     {args.forward_days} days")
    print(f"Scorer:      MEAN REVERSION (reward oversold/laggard/high-vol)")
    print("\nFetching Polygon aggregates...")

    closes = _closes(pg, syms, data_start, end)
    valid = [s for s in syms if closes.get(s)]
    print(f"\n{len(valid)}/{len(syms)} symbols have data.\n")

    def _cp(sym):
        return closes.get(sym, [])

    sim = HistoricalSimulator(
        closes_provider=_cp, scorer=_mean_reversion_scorer,
        picks_per_day=args.picks_per_day, forward_days=args.forward_days,
        min_history_days=100,
    )
    result = sim.run(universe=valid, start=sim_start, end=end)
    s = result.to_dict()

    print("=" * 60)
    print("  MEAN-REVERSION RANKER BACKTEST")
    print("=" * 60)
    for k, v in s.items():
        print(f"  {k:<28} {v}")
    print("=" * 60)
    edge = s["edge_over_baseline_pct"]
    if edge > 0.5:
        print(f"✅ MEAN REVERSION HAS EDGE: {edge:+.2f}% over random.")
    elif edge > -0.5:
        print(f"⚠ MEAN REVERSION NEUTRAL: {edge:+.2f}%.")
    else:
        print(f"❌ MEAN REVERSION NEGATIVE EDGE: {edge:+.2f}%.")

    try:
        with open(args.out, "w") as f:
            json.dump({
                "summary": s,
                "picks": [{
                    "date": p.pick_date.isoformat(),
                    "symbol": p.symbol,
                    "score": round(p.score, 2),
                    "entry": round(p.entry_price, 4),
                    "forward_close": (round(p.forward_30d_close, 4)
                                      if p.forward_30d_close is not None else None),
                    "forward_return_pct": (round(p.forward_30d_return_pct, 3)
                                           if p.forward_30d_return_pct is not None else None),
                    "breakdown": {k: round(v, 2) for k, v in p.score_breakdown.items()},
                } for p in result.picks],
            }, f, indent=2)
        print(f"\nFull result → {args.out}")
    except OSError as exc:
        print(f"\n⚠ Could not write {args.out}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
