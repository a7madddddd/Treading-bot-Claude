"""Combined backtest: Mean-Reversion ranker SELECTS → Ladder EXECUTES.

This is the real production pipeline simulated end-to-end:
  1. Every Monday during the backtest window, run the mean-reversion
     ranker over the universe and keep the Top-3 picks.
  2. For each pick, open a position at Monday's close and run the
     full D-0004 ladder + D-0008 trailing exit logic over the next
     90 days (identical to run_ladder_backtest.py's loop).
  3. Report net return per pick and the compounded portfolio return,
     then compare against:
     * random pick + ladder (controls for the ranker's contribution)
     * mean-reversion pick + buy-and-hold (controls for ladder's
       contribution)
     * random pick + buy-and-hold (base case)

Any positive compounding of ranker edge + ladder edge should show
up here. If the combined edge is LESS than the sum of its parts,
the two components overlap. If MORE, they're complementary.

Usage:
  cd ~/Treading-bot-Claude && set -a && source .env && set +a && \\
  PYTHONPATH=src python3.11 scripts/run_combined_backtest.py \\
      --months 12 --picks-per-week 3

Writes ./combined_backtest_result.json.
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
from typing import Callable, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from backtest.historical_simulator import (                 # noqa: E402
    ReplayFeatures, _build_replay_features,
)
from marketdata.polygon_source import PolygonSource          # noqa: E402


LADDER1_PCT = -5.0
LADDER2_PCT = -8.0
FLOOR_PCT   = -10.0
TRAIL_ACTIVATE_PCT = 10.0
TRAIL_RATCHET_PCT  = -5.0
TIMEOUT_DAYS       = 90
MIN_HISTORY_DAYS   = 100
RANDOM_SEED        = 42


@dataclass
class CombinedTrade:
    picker: str              # "ranker" or "random"
    exit_kind: str           # "ladder" or "bh"
    pick_date: str
    symbol: str
    entry_price: float
    exit_date: str
    exit_price: float
    exit_reason: str
    net_return_pct: float    # the real P/L for this trade


def _mean_rev(feats: ReplayFeatures) -> float:
    oversold_parts = []
    for r in (feats.return_5d_pct, feats.return_30d_pct, feats.return_90d_pct):
        if r is not None:
            clamped = max(-10.0, min(10.0, r))
            oversold_parts.append((10.0 - clamped) / 20.0)
    oversold = (sum(oversold_parts) / len(oversold_parts)) * 30.0 \
        if oversold_parts else 0.0
    lagging = 0.0
    if feats.rel_strength_30d_pct is not None:
        clamped = max(-5.0, min(5.0, feats.rel_strength_30d_pct))
        lagging = ((5.0 - clamped) / 10.0) * 10.0
    vol = 0.0
    if feats.volatility_30d_pct is not None:
        clamped = max(10.0, min(80.0, feats.volatility_30d_pct))
        vol = ((clamped - 10.0) / 70.0) * 15.0
    return oversold + lagging + vol


def _simulate_ladder(sym: str, entry_idx: int,
                     closes: List[Tuple[date, float]]) -> Optional[CombinedTrade]:
    if entry_idx + TIMEOUT_DAYS >= len(closes):
        return None
    entry_date, entry_px = closes[entry_idx]
    shares = 1
    cost = entry_px
    l1 = l2 = armed = False
    peak = entry_px
    for off in range(1, TIMEOUT_DAYS + 1):
        d, px = closes[entry_idx + off]
        if px > peak:
            peak = px
        if not armed and px >= entry_px * (1 + TRAIL_ACTIVATE_PCT / 100):
            armed = True
        if not l1 and px <= entry_px * (1 + LADDER1_PCT / 100):
            cost = (cost * shares + px) / (shares + 1); shares += 1; l1 = True
        if not l2 and px <= entry_px * (1 + LADDER2_PCT / 100):
            cost = (cost * shares + px) / (shares + 1); shares += 1; l2 = True
        if armed and px <= peak * (1 + TRAIL_RATCHET_PCT / 100):
            return CombinedTrade(
                picker="", exit_kind="ladder",
                pick_date=entry_date.isoformat(), symbol=sym,
                entry_price=round(entry_px, 4),
                exit_date=d.isoformat(), exit_price=round(px, 4),
                exit_reason="trailing",
                net_return_pct=round((px - cost) / cost * 100, 3),
            )
        if not armed and px <= cost * (1 + FLOOR_PCT / 100):
            return CombinedTrade(
                picker="", exit_kind="ladder",
                pick_date=entry_date.isoformat(), symbol=sym,
                entry_price=round(entry_px, 4),
                exit_date=d.isoformat(), exit_price=round(px, 4),
                exit_reason="floor",
                net_return_pct=round((px - cost) / cost * 100, 3),
            )
    exit_date, exit_px = closes[entry_idx + TIMEOUT_DAYS]
    return CombinedTrade(
        picker="", exit_kind="ladder",
        pick_date=entry_date.isoformat(), symbol=sym,
        entry_price=round(entry_px, 4),
        exit_date=exit_date.isoformat(), exit_price=round(exit_px, 4),
        exit_reason="timeout",
        net_return_pct=round((exit_px - cost) / cost * 100, 3),
    )


def _simulate_bh(sym: str, entry_idx: int, hold_days: int,
                 closes: List[Tuple[date, float]]) -> Optional[CombinedTrade]:
    if entry_idx + hold_days >= len(closes):
        return None
    entry_date, entry_px = closes[entry_idx]
    exit_date, exit_px = closes[entry_idx + hold_days]
    return CombinedTrade(
        picker="", exit_kind="bh",
        pick_date=entry_date.isoformat(), symbol=sym,
        entry_price=round(entry_px, 4),
        exit_date=exit_date.isoformat(), exit_price=round(exit_px, 4),
        exit_reason="timeout",
        net_return_pct=round((exit_px - entry_px) / entry_px * 100, 3),
    )


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
            print(f"      ⚠ rate-limited; waiting 65s + retrying...",
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


def _summarize(trades: List[CombinedTrade], label: str) -> dict:
    if not trades:
        return {"label": label, "n": 0}
    rets = [t.net_return_pct for t in trades]
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
    p = argparse.ArgumentParser(description="Combined ranker+ladder backtest")
    p.add_argument("--symbols",
                   default="AAPL,MSFT,NVDA,TSLA,GOOGL,META,AMZN,JPM,UNH,V,"
                           "XOM,SPY,PLTR,SMCI,COIN,AMD,SOFI,RBLX,UBER,HOOD")
    p.add_argument("--months", type=int, default=12)
    p.add_argument("--picks-per-week", type=int, default=3)
    p.add_argument("--out", default="./combined_backtest_result.json")
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

    print(f"Universe:        {len(syms)} symbols")
    print(f"Window:          {sim_start} → {end}")
    print(f"Picks/week:      {args.picks_per_week}")
    print(f"Ranker:          mean reversion")
    print(f"Executor:        ladder+trailing (D-0004/D-0008)")
    print("\nFetching Polygon aggregates...")
    closes = _closes(pg, syms, data_start, end)
    histories = {s: v for s, v in closes.items() if len(v) >= MIN_HISTORY_DAYS}
    print(f"\n{len(histories)}/{len(syms)} symbols have ≥{MIN_HISTORY_DAYS} bars.\n")

    spy_hist = histories.get("SPY")

    # Find all Mondays in the simulation window where SPY has data.
    trading_days = [d for d, _ in (spy_hist or []) if sim_start <= d <= end]
    mondays = [d for d in trading_days if d.weekday() == 0]
    print(f"{len(mondays)} Monday entries in window.\n")

    rng = random.Random(RANDOM_SEED)

    ranker_ladder: List[CombinedTrade] = []
    ranker_bh: List[CombinedTrade] = []
    random_ladder: List[CombinedTrade] = []
    random_bh: List[CombinedTrade] = []

    for day in mondays:
        # Rank
        scored: List[Tuple[str, float]] = []
        for sym, hist in histories.items():
            feats = _build_replay_features(sym, hist, day, MIN_HISTORY_DAYS,
                                           spy_hist)
            if feats is None:
                continue
            scored.append((sym, _mean_rev(feats)))
        if not scored:
            continue
        scored.sort(key=lambda t: -t[1])
        ranker_picks = [s for s, _ in scored[: args.picks_per_week]]
        # Random picks from the same eligible set (ensures fair comparison)
        eligible = [s for s, _ in scored]
        random_picks = rng.sample(eligible,
                                   min(args.picks_per_week, len(eligible)))

        for sym in ranker_picks:
            # Find day's index in that symbol's history
            idx = next((i for i, (d, _) in enumerate(histories[sym])
                        if d == day), None)
            if idx is None:
                continue
            t = _simulate_ladder(sym, idx, histories[sym])
            if t: t.picker = "ranker"; ranker_ladder.append(t)
            tb = _simulate_bh(sym, idx, TIMEOUT_DAYS, histories[sym])
            if tb: tb.picker = "ranker"; ranker_bh.append(tb)
        for sym in random_picks:
            idx = next((i for i, (d, _) in enumerate(histories[sym])
                        if d == day), None)
            if idx is None:
                continue
            t = _simulate_ladder(sym, idx, histories[sym])
            if t: t.picker = "random"; random_ladder.append(t)
            tb = _simulate_bh(sym, idx, TIMEOUT_DAYS, histories[sym])
            if tb: tb.picker = "random"; random_bh.append(tb)

    s_rl = _summarize(ranker_ladder, "RANKER + LADDER")
    s_rb = _summarize(ranker_bh, "RANKER + BuyHold90d")
    s_xl = _summarize(random_ladder, "random + LADDER")
    s_xb = _summarize(random_bh, "random + BuyHold90d")

    print("=" * 76)
    print("  COMBINED PIPELINE RESULTS (4-cell comparison)")
    print("=" * 76)
    for s in (s_rl, s_rb, s_xl, s_xb):
        print(f"  {s['label']:<24}  n={s.get('n', 0):>4}  "
              f"mean={s.get('mean_pct', 0):>+6.2f}%  "
              f"win={s.get('win_rate_pct', 0):>5.2f}%  "
              f"sharpe={s.get('sharpe_like', 0):>+5.2f}")
    print("-" * 76)
    ranker_contribution_bh = (s_rb.get("mean_pct", 0) - s_xb.get("mean_pct", 0))
    ladder_contribution_random = (s_xl.get("mean_pct", 0) - s_xb.get("mean_pct", 0))
    combined_edge = (s_rl.get("mean_pct", 0) - s_xb.get("mean_pct", 0))
    sum_of_parts = ranker_contribution_bh + ladder_contribution_random
    print(f"  Ranker contribution (B&H baseline):   "
          f"{ranker_contribution_bh:+.2f}%")
    print(f"  Ladder contribution (random baseline):"
          f" {ladder_contribution_random:+.2f}%")
    print(f"  Sum of parts:                         {sum_of_parts:+.2f}%")
    print(f"  Combined edge (ranker + ladder):      {combined_edge:+.2f}%")
    diff = combined_edge - sum_of_parts
    if diff > 0.3:
        print(f"  → Synergy (+{diff:.2f}%): components complement each other.")
    elif diff > -0.3:
        print(f"  → Additive ({diff:+.2f}%): components are independent.")
    else:
        print(f"  → Overlap ({diff:+.2f}%): components share edge, net loss.")
    print("=" * 76)

    try:
        with open(args.out, "w") as f:
            json.dump({
                "window_start": sim_start.isoformat(),
                "window_end": end.isoformat(),
                "picks_per_week": args.picks_per_week,
                "summary": {
                    "ranker_ladder": s_rl, "ranker_bh": s_rb,
                    "random_ladder": s_xl, "random_bh": s_xb,
                    "ranker_contribution_bh": round(ranker_contribution_bh, 3),
                    "ladder_contribution_random": round(ladder_contribution_random, 3),
                    "combined_edge": round(combined_edge, 3),
                    "sum_of_parts": round(sum_of_parts, 3),
                },
                "trades": {
                    "ranker_ladder": [asdict(t) for t in ranker_ladder],
                    "ranker_bh": [asdict(t) for t in ranker_bh],
                    "random_ladder": [asdict(t) for t in random_ladder],
                    "random_bh": [asdict(t) for t in random_bh],
                },
            }, f, indent=2)
        print(f"\nFull result → {args.out}")
    except OSError as exc:
        print(f"\n⚠ Could not write {args.out}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
