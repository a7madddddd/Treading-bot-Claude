"""Hold-period sweep: tests 5, 10, 15, 30, 60-day forward returns on
BOTH momentum and mean-reversion scorers to find the right time
horizon.

The main ranker backtest assumes a 30-day hold. But the real strategy
exits via trailing floor (variable), so 30d may be arbitrary. This
sweep answers: at what horizon does the ranker's edge actually peak?

For each (scorer, horizon) pair:
  * Runs the HistoricalSimulator on 12 months of Polygon data.
  * Reports mean return, win rate, Sharpe, edge vs random baseline.

Usage:
  cd ~/Treading-bot-Claude && set -a && source .env && set +a && \\
  PYTHONPATH=src python3.11 scripts/run_hold_period_sweep.py \\
      --symbols AAPL,MSFT,NVDA,TSLA,GOOGL,META,AMZN,JPM,UNH,V,XOM,SPY \\
      --months 12

Writes ./hold_period_sweep_result.json.
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
from marketdata.polygon_source import PolygonSource         # noqa: E402


def _momentum_scorer(feats: ReplayFeatures) -> Tuple[float, Dict[str, float]]:
    """Standard trend + rel_str + risk-discount (same as ranker)."""
    bd: Dict[str, float] = {}
    parts = []
    for r in (feats.return_5d_pct, feats.return_30d_pct, feats.return_90d_pct):
        if r is not None:
            clamped = max(-10.0, min(10.0, r))
            parts.append((clamped + 10.0) / 20.0)
    bd["trend"] = (sum(parts) / len(parts)) * 13.0 if parts else 0.0
    if feats.rel_strength_30d_pct is not None:
        clamped = max(-5.0, min(5.0, feats.rel_strength_30d_pct))
        bd["rel_str"] = ((clamped + 5.0) / 10.0) * 9.0
    else:
        bd["rel_str"] = 0.0
    risk = 0.0
    if feats.volatility_30d_pct is not None and feats.volatility_30d_pct > 40.0:
        risk = min(5.0, (feats.volatility_30d_pct - 40.0) / 20)
    bd["risk"] = -risk
    return max(0.0, min(100.0, sum(bd.values()))), bd


def _mean_rev_scorer(feats: ReplayFeatures) -> Tuple[float, Dict[str, float]]:
    """Rewards oversold / laggard / high-vol."""
    bd: Dict[str, float] = {}
    parts = []
    for r in (feats.return_5d_pct, feats.return_30d_pct, feats.return_90d_pct):
        if r is not None:
            clamped = max(-10.0, min(10.0, r))
            parts.append((10.0 - clamped) / 20.0)
    bd["oversold"] = (sum(parts) / len(parts)) * 30.0 if parts else 0.0
    if feats.rel_strength_30d_pct is not None:
        clamped = max(-5.0, min(5.0, feats.rel_strength_30d_pct))
        bd["lagging"] = ((5.0 - clamped) / 10.0) * 10.0
    else:
        bd["lagging"] = 0.0
    if feats.volatility_30d_pct is not None:
        clamped = max(10.0, min(80.0, feats.volatility_30d_pct))
        bd["volatility"] = ((clamped - 10.0) / 70.0) * 15.0
    else:
        bd["volatility"] = 0.0
    return max(0.0, min(100.0, sum(bd.values()))), bd


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
    p = argparse.ArgumentParser(description="Hold-period sweep (5/10/15/30/60)")
    p.add_argument("--symbols",
                   default="AAPL,MSFT,NVDA,TSLA,GOOGL,META,AMZN,JPM,UNH,V,XOM,SPY")
    p.add_argument("--months", type=int, default=12)
    p.add_argument("--picks-per-day", type=int, default=3)
    p.add_argument("--out", default="./hold_period_sweep_result.json")
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
    print(f"Hold periods: 5, 10, 15, 30, 60 days")
    print(f"Scorers:     momentum, mean_reversion")
    print("\nFetching Polygon aggregates (one-time)...")

    closes = _closes(pg, syms, data_start, end)
    valid = [s for s in syms if closes.get(s)]
    print(f"\n{len(valid)}/{len(syms)} symbols have data.\n")

    def _cp(sym):
        return closes.get(sym, [])

    scorers = {
        "momentum": _momentum_scorer,
        "mean_reversion": _mean_rev_scorer,
    }
    horizons = [5, 10, 15, 30, 60]

    all_results = {}
    print("Running sweep...")
    print("-" * 76)
    print(f"{'scorer':<16} {'horizon':>8} | {'mean':>8} {'win%':>7} "
          f"{'sharpe':>7} {'base_mean':>10} {'edge':>8} {'n':>5}")
    print("-" * 76)
    for name, scorer in scorers.items():
        all_results[name] = {}
        for h in horizons:
            sim = HistoricalSimulator(
                closes_provider=_cp, scorer=scorer,
                picks_per_day=args.picks_per_day, forward_days=h,
                min_history_days=100,
            )
            r = sim.run(universe=valid, start=sim_start, end=end).to_dict()
            all_results[name][h] = r
            print(f"{name:<16} {h:>8}d | "
                  f"{r['mean_return_pct']:>+7.2f}% {r['win_rate_pct']:>6.2f}% "
                  f"{r['sharpe_like']:>+7.3f} "
                  f"{r['baseline_mean_return_pct']:>+9.2f}% "
                  f"{r['edge_over_baseline_pct']:>+7.2f}% "
                  f"{r['completed_picks']:>5}")
    print("-" * 76)

    # Find best (scorer, horizon) by edge
    best = ("", 0, -999.0)
    for name, hs in all_results.items():
        for h, r in hs.items():
            if r["edge_over_baseline_pct"] > best[2]:
                best = (name, h, r["edge_over_baseline_pct"])
    print(f"\n⭐ BEST: {best[0]} @ {best[1]}d  edge={best[2]:+.2f}%")
    if best[2] > 0.5:
        print(f"✅ Found positive edge configuration.")
    elif best[2] > -0.5:
        print(f"⚠ All configurations essentially random.")
    else:
        print(f"❌ All configurations negative. Price features have no edge "
              f"regardless of scorer or horizon.")

    try:
        with open(args.out, "w") as f:
            json.dump({
                "window_start": sim_start.isoformat(),
                "window_end": end.isoformat(),
                "universe": valid,
                "results": all_results,
                "best": {"scorer": best[0], "horizon": best[1],
                         "edge_pct": best[2]},
            }, f, indent=2)
        print(f"\nFull result → {args.out}")
    except OSError as exc:
        print(f"\n⚠ Could not write {args.out}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
