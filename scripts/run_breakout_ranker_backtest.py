"""Breakout-detection ranker backtest.

Picks stocks that are STRONGLY TRENDING UPWARD with CONFIRMED
MOMENTUM across multiple timeframes — the opposite philosophy of
the pullback ranker, and distinct from pure momentum because it
requires consistency across lookbacks (5d AND 30d AND 90d all
positive, outperforming SPY, moderate-to-strong volatility to
allow follow-through).

The scorer also uses the custom replay loop (not the generic
HistoricalSimulator) so it can inspect raw price history and
compute "percent from recent high" directly — the real breakout
signal — rather than approximate it from return bins.

Scoring:
  (a) Near recent 20-day high      — within 2% of 20d max
  (b) All timeframe returns positive — 5d AND 30d AND 90d all > 0
  (c) Strong relative strength      — outperforming SPY by > 2%
  (d) Momentum acceleration         — 5d return > 30d/6 (fresh push)

Runs the same 5-check suite as the pullback script:
  T1a/b: standalone original 12 symbols @ 15d, 30d
  T2a/b: standalone expanded 22 symbols @ 15d, 30d
  T3:    combined with Ladder (end-to-end)

Verdict: ALL 5 must beat +0.5% to adopt.

Usage:
  cd ~/Treading-bot-Claude && set -a && source .env && set +a && \\
  PYTHONPATH=src python3.11 scripts/run_breakout_ranker_backtest.py \\
      --months 12

Writes ./breakout_ranker_result.json.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from marketdata.polygon_source import PolygonSource         # noqa: E402


ORIGINAL = "AAPL,MSFT,NVDA,TSLA,GOOGL,META,AMZN,JPM,UNH,V,XOM,SPY".split(",")
EXPANDED = ORIGINAL + "PLTR,SMCI,COIN,AMD,SOFI,RBLX,UBER,HOOD,PINS,SNAP".split(",")

LADDER1_PCT = -5.0
LADDER2_PCT = -8.0
FLOOR_PCT   = -10.0
TRAIL_ACT   = 10.0
TRAIL_DOWN  = -5.0
TIMEOUT_D   = 90
MIN_HIST    = 100


def _breakout_score(hist: List[Tuple[date, float]], day: date,
                    spy_hist: Optional[List[Tuple[date, float]]]
                    ) -> Tuple[Optional[float], Dict[str, float]]:
    """Compute breakout score using RAW price history (not ReplayFeatures)
    so we can inspect 20-day highs directly. Returns (score, breakdown)
    or (None, {}) if insufficient history."""
    closes_before = [c for d, c in hist if d <= day]
    if len(closes_before) < MIN_HIST:
        return None, {}
    px = closes_before[-1]
    bd: Dict[str, float] = {}

    # (a) Near 20-day high (the actual breakout signal)
    high_20 = max(closes_before[-20:])
    pct_from_high = (px / high_20 - 1.0) * 100   # 0% = AT high, -5% = 5% below
    if pct_from_high > -2:
        # Right at/near the high → full points
        bd["near_high"] = 30.0
    elif pct_from_high > -5:
        # Within 5% of high → partial
        bd["near_high"] = 30.0 * (pct_from_high + 5) / 3
    else:
        # Far from high → penalty
        bd["near_high"] = -10

    # (b) Multi-timeframe uptrend (ALL must be positive)
    def _ret(lookback):
        if len(closes_before) < lookback + 1:
            return None
        old = closes_before[-lookback - 1]
        if old <= 0: return None
        return (closes_before[-1] - old) / old * 100
    r5, r30, r90 = _ret(5), _ret(21), _ret(63)
    bd["uptrend"] = 0.0
    if r5 is not None and r30 is not None and r90 is not None:
        if r5 > 0 and r30 > 0 and r90 > 0:
            bd["uptrend"] = 20.0
        elif sum(1 for r in (r5, r30, r90) if r > 0) >= 2:
            bd["uptrend"] = 10.0
        else:
            bd["uptrend"] = -15.0

    # (c) Relative strength vs SPY (30d)
    rs = None
    if r30 is not None and spy_hist:
        spy_closes = [c for d, c in spy_hist if d <= day]
        if len(spy_closes) >= 22:
            spy_r30 = (spy_closes[-1] - spy_closes[-22]) / spy_closes[-22] * 100
            rs = r30 - spy_r30
    bd["rel_str"] = 0.0
    if rs is not None:
        if rs > 2:
            bd["rel_str"] = 15.0
        elif rs > 0:
            bd["rel_str"] = 7.0
        else:
            bd["rel_str"] = -5.0

    # (d) Momentum acceleration (5d > 30d/6 means recent push)
    bd["acceleration"] = 0.0
    if r5 is not None and r30 is not None:
        if r5 > r30 / 6:
            bd["acceleration"] = 10.0
        else:
            bd["acceleration"] = -3.0

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
            return {"ret": (px - cost) / cost * 100}
        if not armed and px <= cost * (1 + FLOOR_PCT/100):
            return {"ret": (px - cost) / cost * 100}
    _, exit_px = closes[entry_idx + TIMEOUT_D]
    return {"ret": (exit_px - cost) / cost * 100}


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


def _standalone(label, histories, valid, sim_start, end, fwd):
    """Custom replay loop (not HistoricalSimulator) so scorer can
    access raw history for breakout detection."""
    spy = histories.get("SPY")
    trading_days = [d for d, _ in (spy or []) if sim_start <= d <= end]

    picks_ret = []
    baseline_ret = []
    rng = random.Random(42)

    for day in trading_days:
        scored = []
        eligible = []
        for sym in valid:
            hist = histories.get(sym, [])
            s, _ = _breakout_score(hist, day, spy)
            if s is None: continue
            scored.append((sym, s))
            eligible.append(sym)
        if len(scored) < 3: continue
        scored.sort(key=lambda t: -t[1])
        for sym, _ in scored[:3]:
            hist = histories[sym]
            idx = next((i for i, (d, _) in enumerate(hist) if d == day), None)
            if idx is None or idx + fwd >= len(hist): continue
            entry = hist[idx][1]
            exit_px = hist[idx + fwd][1]
            picks_ret.append((exit_px - entry) / entry * 100)
        # Baseline: 3 random picks
        for sym in rng.sample(eligible, min(3, len(eligible))):
            hist = histories[sym]
            idx = next((i for i, (d, _) in enumerate(hist) if d == day), None)
            if idx is None or idx + fwd >= len(hist): continue
            entry = hist[idx][1]
            exit_px = hist[idx + fwd][1]
            baseline_ret.append((exit_px - entry) / entry * 100)

    picks = _summarize(picks_ret, f"breakout {fwd}d")
    base = _summarize(baseline_ret, f"random {fwd}d")
    edge = picks.get("mean_pct", 0) - base.get("mean_pct", 0)
    print(f"\n[{label}]  fwd={fwd}d  universe={len(valid)}")
    print(f"  mean={picks.get('mean_pct', 0):>+6.2f}%  "
          f"win={picks.get('win_rate_pct', 0):>5.2f}%  "
          f"sharpe={picks.get('sharpe_like', 0):>+5.2f}  "
          f"base={base.get('mean_pct', 0):>+5.2f}%  "
          f"edge={edge:>+5.2f}%  "
          f"n={picks.get('n', 0)}")
    return {"picks": picks, "baseline": base,
            "edge_over_baseline_pct": round(edge, 3),
            "completed_picks": picks.get("n", 0),
            "mean_return_pct": picks.get("mean_pct", 0),
            "win_rate_pct": picks.get("win_rate_pct", 0),
            "sharpe_like": picks.get("sharpe_like", 0),
            "baseline_mean_return_pct": base.get("mean_pct", 0)}


def _combined(histories, valid, sim_start, end):
    """Monday entries, breakout picks → ladder exits."""
    spy = histories.get("SPY")
    trading_days = [d for d, _ in (spy or []) if sim_start <= d <= end]
    mondays = [d for d in trading_days if d.weekday() == 0]

    rng = random.Random(42)
    ranker_rets = []
    random_rets = []

    for day in mondays:
        scored = []
        eligible = []
        for sym in valid:
            hist = histories.get(sym, [])
            s, _ = _breakout_score(hist, day, spy)
            if s is None: continue
            scored.append((sym, s))
            eligible.append(sym)
        if len(scored) < 3: continue
        scored.sort(key=lambda t: -t[1])
        ranker_picks = [s for s, _ in scored[:3]]
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

    return _summarize(ranker_rets, "Breakout+Ladder"), \
           _summarize(random_rets, "random+Ladder")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--months", type=int, default=12)
    p.add_argument("--out", default="./breakout_ranker_result.json")
    args = p.parse_args()

    pg = PolygonSource.from_env()
    if pg is None:
        print("ERROR: POLYGON_API_KEY unset.", file=sys.stderr)
        return 2

    end = date.today()
    data_start = end - timedelta(days=args.months * 31 + 150)
    sim_start = end - timedelta(days=args.months * 31)

    print("=" * 72)
    print("  BREAKOUT-DETECTION RANKER BACKTEST")
    print("=" * 72)
    print(f"Window: {sim_start} → {end}\n")
    print(f"Fetching {len(EXPANDED)} symbols (one-time)...")
    closes = _closes(pg, EXPANDED, data_start, end)
    histories = {s: v for s, v in closes.items() if len(v) >= MIN_HIST}
    print()

    results = {}

    print("=" * 72); print("  TEST 1 — STANDALONE on ORIGINAL (12 symbols)")
    print("=" * 72)
    valid_orig = [s for s in ORIGINAL if s in histories]
    results["t1_original_15d"] = _standalone("T1 original 15d", histories,
                                              valid_orig, sim_start, end, 15)
    results["t1_original_30d"] = _standalone("T1 original 30d", histories,
                                              valid_orig, sim_start, end, 30)

    print("\n" + "=" * 72)
    print("  TEST 2 — STANDALONE on EXPANDED (22 symbols)")
    print("=" * 72)
    valid_exp = [s for s in EXPANDED if s in histories]
    results["t2_expanded_15d"] = _standalone("T2 expanded 15d", histories,
                                              valid_exp, sim_start, end, 15)
    results["t2_expanded_30d"] = _standalone("T2 expanded 30d", histories,
                                              valid_exp, sim_start, end, 30)

    print("\n" + "=" * 72)
    print("  TEST 3 — COMBINED (ranker + ladder) on EXPANDED")
    print("=" * 72)
    s_rl, s_xl = _combined(histories, valid_exp, sim_start, end)
    print(f"\n  [{s_rl['label']}]        n={s_rl['n']:>4} "
          f"mean={s_rl.get('mean_pct', 0):>+6.2f}% "
          f"win={s_rl.get('win_rate_pct', 0):>5.2f}% "
          f"sharpe={s_rl.get('sharpe_like', 0):>+5.2f}")
    print(f"  [{s_xl['label']}]         n={s_xl['n']:>4} "
          f"mean={s_xl.get('mean_pct', 0):>+6.2f}% "
          f"win={s_xl.get('win_rate_pct', 0):>5.2f}% "
          f"sharpe={s_xl.get('sharpe_like', 0):>+5.2f}")
    combined_edge = s_rl.get("mean_pct", 0) - s_xl.get("mean_pct", 0)
    print(f"\n  Combined edge over random+ladder: {combined_edge:+.2f}%")
    results["t3_combined"] = {"breakout_ladder": s_rl,
                              "random_ladder": s_xl,
                              "edge_vs_random_ladder_pct": round(combined_edge, 3)}

    print("\n" + "=" * 72); print("  VERDICT"); print("=" * 72)
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
        print("✅ ALL CHECKS PASSED — Breakout ranker is ROBUST. Keep it.")
    else:
        print("❌ ONE OR MORE CHECKS FAILED — do NOT adopt this ranker.")

    try:
        with open(args.out, "w") as f:
            json.dump({
                "window_start": sim_start.isoformat(),
                "window_end": end.isoformat(),
                "results": results, "verdict_all_pass": all_pass,
            }, f, indent=2, default=str)
        print(f"\nFull result → {args.out}")
    except OSError as exc:
        print(f"\n⚠ Could not write {args.out}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
