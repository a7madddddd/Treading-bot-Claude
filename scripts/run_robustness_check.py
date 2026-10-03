"""Robustness check for the mean-reversion edge.

Re-tests the winning scorer (mean-reversion @ 15d) under two
stress conditions:

  B1. EXPANDED UNIVERSE — adds 10 volatile mid-cap names to catch
      whether the edge was universe-dependent (large-cap-only may
      be a selection bias).
  B2. DIFFERENT WINDOW — same scorer, same universe, but
      shifted 12 months earlier so none of the training-period
      data overlaps.

If the edge persists in BOTH, the mean-reversion thesis is robust.
If it collapses in either, we need more analysis before any
production changes.

Usage:
  cd ~/Treading-bot-Claude && set -a && source .env && set +a && \\
  PYTHONPATH=src python3.11 scripts/run_robustness_check.py

Writes ./robustness_check_result.json.
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


ORIGINAL_UNIVERSE = ("AAPL,MSFT,NVDA,TSLA,GOOGL,META,AMZN,JPM,UNH,V,"
                     "XOM,SPY").split(",")
EXTRA_VOLATILE = ("PLTR,SMCI,COIN,AMD,SOFI,RBLX,UBER,HOOD,PINS,SNAP").split(",")


def _mean_rev(feats: ReplayFeatures) -> Tuple[float, Dict[str, float]]:
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
            print(f"      ⚠ rate-limited; waiting 65s + retrying...", flush=True)
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


def _run_once(label: str, closes: Dict[str, List[Tuple[date, float]]],
              valid: List[str], start: date, end: date,
              forward_days: int = 15, picks_per_day: int = 3) -> dict:
    def _cp(sym):
        return closes.get(sym, [])
    sim = HistoricalSimulator(
        closes_provider=_cp, scorer=_mean_rev,
        picks_per_day=picks_per_day, forward_days=forward_days,
        min_history_days=100,
    )
    r = sim.run(universe=valid, start=start, end=end).to_dict()
    print(f"\n[{label}]  {start} → {end}  universe={len(valid)}  "
          f"fwd={forward_days}d")
    print(f"  mean={r['mean_return_pct']:>+6.2f}%  "
          f"win={r['win_rate_pct']:>5.2f}%  "
          f"sharpe={r['sharpe_like']:>+5.2f}  "
          f"base_mean={r['baseline_mean_return_pct']:>+5.2f}%  "
          f"edge={r['edge_over_baseline_pct']:>+5.2f}%  "
          f"n={r['completed_picks']}")
    return r


def main() -> int:
    p = argparse.ArgumentParser(description="Robustness check")
    p.add_argument("--out", default="./robustness_check_result.json")
    args = p.parse_args()

    pg = PolygonSource.from_env()
    if pg is None:
        print("ERROR: POLYGON_API_KEY unset.", file=sys.stderr)
        return 2

    end = date.today()
    results: Dict[str, dict] = {}

    # B1: EXPANDED universe (original + 10 volatile mid-caps), current year
    print("=" * 72)
    print("  B1 — EXPANDED UNIVERSE (current year)")
    print("=" * 72)
    expanded = sorted(set(ORIGINAL_UNIVERSE) | set(EXTRA_VOLATILE))
    data_start_b1 = end - timedelta(days=12 * 31 + 150)
    sim_start_b1 = end - timedelta(days=12 * 31)
    print(f"Fetching {len(expanded)} symbols...")
    closes_b1 = _closes(pg, expanded, data_start_b1, end)
    valid_b1 = [s for s in expanded if closes_b1.get(s)]
    print(f"\n{len(valid_b1)}/{len(expanded)} symbols have data.")
    results["B1_expanded"] = _run_once(
        "B1 expanded", closes_b1, valid_b1, sim_start_b1, end,
        forward_days=15, picks_per_day=3,
    )

    # B2: PRIOR year (shift 12 months earlier) — same ORIGINAL universe
    print("\n" + "=" * 72)
    print("  B2 — PRIOR YEAR (same universe, shifted 12 months earlier)")
    print("=" * 72)
    prior_end = end - timedelta(days=365)
    data_start_b2 = prior_end - timedelta(days=12 * 31 + 150)
    sim_start_b2 = prior_end - timedelta(days=12 * 31)
    print(f"Fetching {len(ORIGINAL_UNIVERSE)} symbols for {sim_start_b2} → "
          f"{prior_end}...")
    closes_b2 = _closes(pg, ORIGINAL_UNIVERSE, data_start_b2, prior_end)
    valid_b2 = [s for s in ORIGINAL_UNIVERSE if closes_b2.get(s)]
    print(f"\n{len(valid_b2)}/{len(ORIGINAL_UNIVERSE)} symbols have data.")
    results["B2_prior_year"] = _run_once(
        "B2 prior-year", closes_b2, valid_b2, sim_start_b2, prior_end,
        forward_days=15, picks_per_day=3,
    )

    print("\n" + "=" * 72)
    print("  ROBUSTNESS SUMMARY")
    print("=" * 72)
    for key, r in results.items():
        verdict = ("✅ edge" if r["edge_over_baseline_pct"] > 0.5
                   else "⚠ neutral" if r["edge_over_baseline_pct"] > -0.5
                   else "❌ no edge")
        print(f"  {key:<20} edge={r['edge_over_baseline_pct']:>+5.2f}%  "
              f"win={r['win_rate_pct']:>5.2f}%  {verdict}")
    print("=" * 72)

    b1_ok = results["B1_expanded"]["edge_over_baseline_pct"] > 0.5
    b2_ok = results["B2_prior_year"]["edge_over_baseline_pct"] > 0.5
    if b1_ok and b2_ok:
        print("✅ EDGE IS ROBUST across universes AND windows.")
    elif b1_ok or b2_ok:
        print("⚠ PARTIAL robustness — one dimension holds, one does not.")
    else:
        print("❌ EDGE NOT ROBUST — may have been overfit to the 12-month "
              "large-cap window.")

    try:
        with open(args.out, "w") as f:
            json.dump({
                "end_date": end.isoformat(),
                "scorer": "mean_reversion",
                "forward_days": 15,
                "results": results,
            }, f, indent=2)
        print(f"\nFull result → {args.out}")
    except OSError as exc:
        print(f"\n⚠ Could not write {args.out}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
