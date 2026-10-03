"""Pullback-in-Uptrend ranker backtest.

Tests a smarter ranker philosophy: pick stocks that are
  (a) in a SHORT-TERM pullback  (-5% to -2% over last week)
  (b) in a LONG-TERM uptrend    (90-day return positive)
  (c) OUTPERFORMING the market  (positive relative strength)
  (d) MODERATE volatility       (not SMCI-style wild swings)

Avoids the mean-reversion ranker's trap of catching "falling knives"
like SMCI/COIN that score high on oversold+lagging but are
fundamentally broken.

Runs three tests:
  1. Standalone on original 12-symbol universe (fwd 15d + 30d)
  2. Standalone on expanded 22-symbol universe (fwd 15d) — robustness
  3. Combined with Ladder (end-to-end production simulation)

If all three show positive edge, we keep the ranker. If any show
negative or neutral, we drop it.

Usage:
  cd ~/Treading-bot-Claude && set -a && source .env && set +a && \\
  PYTHONPATH=src python3.11 scripts/run_pullback_ranker_backtest.py \\
      --months 12

Writes ./pullback_ranker_result.json.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from backtest.historical_simulator import (                 # noqa: E402
    HistoricalSimulator, ReplayFeatures, _build_replay_features,
)
from marketdata.polygon_source import PolygonSource         # noqa: E402


ORIGINAL = "AAPL,MSFT,NVDA,TSLA,GOOGL,META,AMZN,JPM,UNH,V,XOM,SPY".split(",")
EXPANDED = ORIGINAL + "PLTR,SMCI,COIN,AMD,SOFI,RBLX,UBER,HOOD,PINS,SNAP".split(",")

LADDER1_PCT = -5.0
LADDER2_PCT = -8.0
FLOOR_PCT   = -10.0
TRAIL_ACT   = 10.0
TRAIL_DOWN  = -5.0
TIMEOUT_D   = 90


def _pullback_scorer(feats: ReplayFeatures) -> Tuple[float, Dict[str, float]]:
    """Pullback-in-uptrend scorer. Higher = better pick."""
    bd: Dict[str, float] = {}

    # (a) Short-term pullback bonus — sweet spot -5% to -2% last 5d
    r5 = feats.return_5d_pct
    if r5 is not None:
        if -10 <= r5 <= -1.5:
            # peaks at -5%, 0 at -1.5% or -10%
            dist = abs(r5 - (-5))
            bd["pullback"] = max(0.0, (3.5 - dist) / 3.5) * 25
        elif r5 > -1.5:
            bd["pullback"] = 0.0            # no pullback yet
        else:
            bd["pullback"] = -5             # beyond -10% = freefall penalty
    else:
        bd["pullback"] = 0.0

    # (b) Long-term uptrend REQUIRED — hard penalty if in downtrend
    r90 = feats.return_90d_pct
    if r90 is not None:
        if r90 > 0:
            bd["uptrend"] = min(20.0, r90) / 20.0 * 25
        else:
            # Hard penalty — this is the SMCI/COIN filter
            bd["uptrend"] = -20
    else:
        bd["uptrend"] = 0.0

    # (c) Market leadership — outperforming SPY
    rs = feats.rel_strength_30d_pct
    if rs is not None:
        if rs > 0:
            bd["leadership"] = min(10.0, rs) / 10.0 * 15
        else:
            bd["leadership"] = max(-10.0, rs)
    else:
        bd["leadership"] = 0.0

    # (d) Moderate volatility — reward 15-40%, penalize >60%
    vol = feats.volatility_30d_pct
    if vol is not None:
        if 15 <= vol <= 40:
            bd["vol_sweet"] = 10
        elif vol > 60:
            bd["vol_sweet"] = -10
        else:
            bd["vol_sweet"] = 0
    else:
        bd["vol_sweet"] = 0

    total = sum(bd.values())
    return max(0.0, min(100.0, total)), bd


def _closes(pg: PolygonSource, syms: List[str], start: date, end: date,
            throttle_s: float = 13.0) -> Dict[str, List[Tuple[date, float]]]:
    out: Dict[str, List[Tuple[date, float]]] = {}
    for i, s in enumerate(syms, 1):
        try:
            bars = pg.get_aggregates(s, 1, "day", start, end, adjusted=True)
        except Exception:
            bars = []
        rows = [(d, float(row.get("c"))) for d, row in bars
                if isinstance(row, dict) and isinstance(row.get("c"), (int, float))]
        print(f"  [{i:>3}/{len(syms)}] {s:<6} {len(rows):>4} bars", flush=True)
        if not rows and i < len(syms):
            print(f"      ⚠ rate-limited; waiting 65s + retrying...", flush=True)
            time.sleep(65)
            try:
                bars = pg.get_aggregates(s, 1, "day", start, end, adjusted=True)
            except Exception:
                bars = []
            rows = [(d, float(row.get("c"))) for d, row in bars
                    if isinstance(row, dict)
                    and isinstance(row.get("c"), (int, float))]
            print(f"      retry: {len(rows)} bars", flush=True)
        out[s] = sorted(rows, key=lambda t: t[0])
        if i < len(syms):
            time.sleep(throttle_s)
    return out


def _simulate_ladder(sym, entry_idx, closes):
    if entry_idx + TIMEOUT_D >= len(closes):
        return None
    entry_d, entry_px = closes[entry_idx]
    shares = 1; cost = entry_px
    l1 = l2 = armed = False
    peak = entry_px
    for off in range(1, TIMEOUT_D + 1):
        d, px = closes[entry_idx + off]
        if px > peak: peak = px
        if not armed and px >= entry_px * (1 + TRAIL_ACT/100): armed = True
        if not l1 and px <= entry_px * (1 + LADDER1_PCT/100):
            cost = (cost*shares + px)/(shares+1); shares += 1; l1 = True
        if not l2 and px <= entry_px * (1 + LADDER2_PCT/100):
            cost = (cost*shares + px)/(shares+1); shares += 1; l2 = True
        if armed and px <= peak * (1 + TRAIL_DOWN/100):
            return {"sym": sym, "exit_reason": "trailing",
                    "ret": (px - cost)/cost * 100}
        if not armed and px <= cost * (1 + FLOOR_PCT/100):
            return {"sym": sym, "exit_reason": "floor",
                    "ret": (px - cost)/cost * 100}
    _, exit_px = closes[entry_idx + TIMEOUT_D]
    return {"sym": sym, "exit_reason": "timeout",
            "ret": (exit_px - cost)/cost * 100}


def _summarize(rets, label):
    if not rets:
        return {"label": label, "n": 0}
    mean = sum(rets)/len(rets)
    wins = sum(1 for r in rets if r > 0)/len(rets)*100
    var = sum((r-mean)**2 for r in rets)/len(rets)
    sd = var**0.5
    return {"label": label, "n": len(rets),
            "mean_pct": round(mean, 3),
            "win_rate_pct": round(wins, 2),
            "sharpe_like": round(mean/sd if sd > 0 else 0.0, 3),
            "best": round(max(rets), 2), "worst": round(min(rets), 2)}


def _standalone(label, closes, valid, start, end, fwd):
    def _cp(s): return closes.get(s, [])
    sim = HistoricalSimulator(
        closes_provider=_cp, scorer=_pullback_scorer,
        picks_per_day=3, forward_days=fwd, min_history_days=100,
    )
    r = sim.run(universe=valid, start=start, end=end).to_dict()
    print(f"\n[{label}]  fwd={fwd}d  universe={len(valid)}")
    print(f"  mean={r['mean_return_pct']:>+6.2f}%  "
          f"win={r['win_rate_pct']:>5.2f}%  "
          f"sharpe={r['sharpe_like']:>+5.2f}  "
          f"base={r['baseline_mean_return_pct']:>+5.2f}%  "
          f"edge={r['edge_over_baseline_pct']:>+5.2f}%  "
          f"n={r['completed_picks']}")
    return r


def _combined(closes, histories, sim_start, end):
    """End-to-end: pullback ranker picks → ladder executes."""
    spy = histories.get("SPY")
    trading_days = [d for d, _ in (spy or []) if sim_start <= d <= end]
    mondays = [d for d in trading_days if d.weekday() == 0]

    rng = random.Random(42)
    ranker_rets = []
    random_rets = []

    for day in mondays:
        scored = []
        for sym, hist in histories.items():
            feats = _build_replay_features(sym, hist, day, 100, spy)
            if feats is None: continue
            sc, _ = _pullback_scorer(feats)
            scored.append((sym, sc))
        if not scored: continue
        scored.sort(key=lambda t: -t[1])
        ranker_picks = [s for s, _ in scored[:3]]
        eligible = [s for s, _ in scored]
        random_picks = rng.sample(eligible, min(3, len(eligible)))

        for sym in ranker_picks:
            idx = next((i for i, (d, _) in enumerate(histories[sym]) if d == day), None)
            if idx is None: continue
            t = _simulate_ladder(sym, idx, histories[sym])
            if t: ranker_rets.append(t["ret"])
        for sym in random_picks:
            idx = next((i for i, (d, _) in enumerate(histories[sym]) if d == day), None)
            if idx is None: continue
            t = _simulate_ladder(sym, idx, histories[sym])
            if t: random_rets.append(t["ret"])

    return _summarize(ranker_rets, "Pullback+Ladder"), \
           _summarize(random_rets, "random+Ladder")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--months", type=int, default=12)
    p.add_argument("--out", default="./pullback_ranker_result.json")
    args = p.parse_args()

    pg = PolygonSource.from_env()
    if pg is None:
        print("ERROR: POLYGON_API_KEY unset.", file=sys.stderr)
        return 2

    end = date.today()
    data_start = end - timedelta(days=args.months * 31 + 150)
    sim_start = end - timedelta(days=args.months * 31)

    print("=" * 72)
    print("  PULLBACK-IN-UPTREND RANKER BACKTEST")
    print("=" * 72)
    print(f"Window: {sim_start} → {end}\n")

    # Fetch expanded universe once (reused for all 3 tests)
    print(f"Fetching {len(EXPANDED)} symbols (one-time)...")
    closes = _closes(pg, EXPANDED, data_start, end)
    print()

    results = {}

    # TEST 1: standalone on original universe @ 15d and 30d
    print("=" * 72)
    print("  TEST 1 — STANDALONE on ORIGINAL (12 symbols)")
    print("=" * 72)
    valid_orig = [s for s in ORIGINAL if closes.get(s)]
    results["t1_original_15d"] = _standalone("T1 original 15d", closes,
                                              valid_orig, sim_start, end, 15)
    results["t1_original_30d"] = _standalone("T1 original 30d", closes,
                                              valid_orig, sim_start, end, 30)

    # TEST 2: standalone on EXPANDED universe (robustness)
    print("\n" + "=" * 72)
    print("  TEST 2 — STANDALONE on EXPANDED (22 symbols)")
    print("=" * 72)
    valid_exp = [s for s in EXPANDED if closes.get(s)]
    results["t2_expanded_15d"] = _standalone("T2 expanded 15d", closes,
                                              valid_exp, sim_start, end, 15)
    results["t2_expanded_30d"] = _standalone("T2 expanded 30d", closes,
                                              valid_exp, sim_start, end, 30)

    # TEST 3: combined with ladder on EXPANDED
    print("\n" + "=" * 72)
    print("  TEST 3 — COMBINED (ranker + ladder) on EXPANDED")
    print("=" * 72)
    histories = {s: closes[s] for s in valid_exp if len(closes[s]) >= 100}
    s_rl, s_xl = _combined(closes, histories, sim_start, end)
    print(f"\n  [{s_rl['label']}]        n={s_rl['n']:>4} "
          f"mean={s_rl.get('mean_pct', 0):>+6.2f}% "
          f"win={s_rl.get('win_rate_pct', 0):>5.2f}% "
          f"sharpe={s_rl.get('sharpe_like', 0):>+5.2f}")
    print(f"  [{s_xl['label']}]          n={s_xl['n']:>4} "
          f"mean={s_xl.get('mean_pct', 0):>+6.2f}% "
          f"win={s_xl.get('win_rate_pct', 0):>5.2f}% "
          f"sharpe={s_xl.get('sharpe_like', 0):>+5.2f}")
    combined_edge = s_rl.get("mean_pct", 0) - s_xl.get("mean_pct", 0)
    print(f"\n  Combined edge over random+ladder: {combined_edge:+.2f}%")
    results["t3_combined"] = {"pullback_ladder": s_rl,
                              "random_ladder": s_xl,
                              "edge_vs_random_ladder_pct": round(combined_edge, 3)}

    # FINAL VERDICT
    print("\n" + "=" * 72)
    print("  VERDICT")
    print("=" * 72)
    checks = [
        ("T1 original 15d edge",
         results["t1_original_15d"]["edge_over_baseline_pct"], 0.5),
        ("T1 original 30d edge",
         results["t1_original_30d"]["edge_over_baseline_pct"], 0.5),
        ("T2 expanded 15d edge",
         results["t2_expanded_15d"]["edge_over_baseline_pct"], 0.5),
        ("T2 expanded 30d edge",
         results["t2_expanded_30d"]["edge_over_baseline_pct"], 0.5),
        ("T3 combined edge",
         combined_edge, 0.5),
    ]
    all_pass = True
    for name, val, thresh in checks:
        verdict = ("✅ PASS" if val > thresh
                   else "⚠ WEAK" if val > -thresh
                   else "❌ FAIL")
        if val < thresh: all_pass = False
        print(f"  {name:<28} {val:>+6.2f}%  {verdict}")
    print("-" * 72)
    if all_pass:
        print("✅ ALL CHECKS PASSED — Pullback ranker is ROBUST. Keep it.")
    else:
        print("❌ ONE OR MORE CHECKS FAILED — do NOT adopt this ranker.")

    try:
        with open(args.out, "w") as f:
            json.dump({
                "window_start": sim_start.isoformat(),
                "window_end": end.isoformat(),
                "results": results,
                "verdict_all_pass": all_pass,
            }, f, indent=2, default=str)
        print(f"\nFull result → {args.out}")
    except OSError as exc:
        print(f"\n⚠ Could not write {args.out}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
