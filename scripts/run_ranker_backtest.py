"""Backtest the TradeEvaluator against 12 months of Polygon data
(D-0050 Phase 13).

Usage:
  cd ~/Treading-bot-Claude && set -a && source .env && set +a && \\
  PYTHONPATH=src python3.11 scripts/run_backtest.py \\
      --symbols AAPL,MSFT,NVDA,TSLA,GOOGL,META,AMZN,SPY,JPM,UNH \\
      --months 12 --picks-per-day 3

Writes results to ./backtest_result.json.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from backtest.historical_simulator import (                 # noqa: E402
    HistoricalSimulator, ReplayFeatures, BacktestResult,
)
from engine.trade_evaluator import EvaluatorConfig, DEFAULT_CONFIG  # noqa: E402


_DEFAULT_UNIVERSE = (
    "AAPL,MSFT,NVDA,TSLA,GOOGL,META,AMZN,SPY,QQQ,JPM,UNH,V,MA,HD,"
    "WMT,PG,JNJ,XOM,CVX,KO,PEP,DIS,NFLX,ADBE,CRM,CSCO,ORCL,INTC,AMD"
)


def _closes_provider_from_polygon(client, start: date, end: date):
    """Returns a function that fetches close prices for one symbol via
    Polygon /v2/aggs/ticker/{sym}/range/1/day. The outer closure caches
    results so each symbol is downloaded exactly once."""
    cache: Dict[str, List[Tuple[date, float]]] = {}
    def _get(symbol: str) -> List[Tuple[date, float]]:
        if symbol in cache:
            return cache[symbol]
        try:
            bars = client.get_aggregates(symbol, 1, "day",
                                          start, end, adjusted=True)
        except Exception:  # noqa: BLE001
            cache[symbol] = []
            return []
        out: List[Tuple[date, float]] = []
        for d, row in bars:
            if isinstance(row, dict):
                c = row.get("c")
                if isinstance(c, (int, float)):
                    out.append((d, float(c)))
        cache[symbol] = out
        return out
    _get.cache = cache
    return _get


def _score_from_features(feats: ReplayFeatures) -> Tuple[float, Dict[str, float]]:
    """Simplified reproduction of the production evaluator's trend +
    rel-strength + volatility rules — the subset that applies when
    only price history is available (no FRED/Finnhub/Perplexity in
    the backtest). This is intentional: it isolates whether the
    PRICE signal has edge, before we attribute credit to richer
    sources we can't easily reconstruct historically."""
    cfg = DEFAULT_CONFIG
    bd: Dict[str, float] = {}

    # Trend (5d + 30d + 90d returns, equal-weighted)
    trend_components = []
    for ret in (feats.return_5d_pct, feats.return_30d_pct,
                feats.return_90d_pct):
        if ret is not None:
            clamped = max(-10.0, min(10.0, ret))
            trend_components.append((clamped + 10.0) / 20.0)
    if trend_components:
        bd["trend"] = (sum(trend_components) / len(trend_components)) * cfg.weight_trend
    else:
        bd["trend"] = 0.0

    # Relative strength vs SPY
    if feats.rel_strength_30d_pct is not None:
        clamped = max(-5.0, min(5.0, feats.rel_strength_30d_pct))
        bd["rel_str"] = ((clamped + 5.0) / 10.0) * cfg.weight_rel_strength
    else:
        bd["rel_str"] = 0.0

    # Volatility risk discount
    risk = 0.0
    if (feats.volatility_30d_pct is not None
            and feats.volatility_30d_pct > cfg.high_volatility_pct_threshold):
        risk = min(5.0, (feats.volatility_30d_pct - cfg.high_volatility_pct_threshold) / 20)
    bd["risk"] = -risk

    total = sum(bd.values())
    return max(0.0, min(100.0, total)), bd


def main() -> int:
    p = argparse.ArgumentParser(description="D-0050 Phase 13 backtest")
    p.add_argument("--symbols", default=_DEFAULT_UNIVERSE)
    p.add_argument("--months", type=int, default=12)
    p.add_argument("--picks-per-day", type=int, default=3)
    p.add_argument("--out", default="./backtest_result.json")
    args = p.parse_args()

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if "SPY" not in syms:
        syms.append("SPY")

    # Polygon REST client (free tier supports /v2/aggs).
    from marketdata.polygon_source import PolygonSource
    pg = PolygonSource.from_env()
    if pg is None:
        print("ERROR: POLYGON_API_KEY unset.", file=sys.stderr)
        return 2

    end = date.today()
    # Fetch 100 extra days before `start` so day-0 has 100-day history.
    data_start = end - timedelta(days=args.months * 31 + 150)
    sim_start = end - timedelta(days=args.months * 31)

    print(f"Universe:   {len(syms)} symbols")
    print(f"Window:     {sim_start} → {end}  ({args.months} months)")
    print(f"Picks/day:  {args.picks_per_day}")
    print(f"Fetching aggregates (one call per symbol)...")

    closes_fn = _closes_provider_from_polygon(pg, data_start, end)
    # Prime cache: force one fetch per symbol so we can report progress.
    # Polygon free tier = 5 requests/minute. Sleep 13s between calls so
    # the whole fetch stays under the limit with margin.
    import time
    for i, s in enumerate(syms, 1):
        bars = closes_fn(s)
        print(f"  [{i:>3}/{len(syms)}] {s:<6} {len(bars):>4} bars", flush=True)
        if len(bars) == 0 and i < len(syms):
            # Likely rate-limited — wait a full minute then retry once.
            print(f"      ⚠ 0 bars — likely rate-limited, waiting 65s and retrying...", flush=True)
            time.sleep(65)
            # Clear cache entry so retry actually re-fetches
            closes_fn.cache.pop(s, None)
            bars = closes_fn(s)
            print(f"      retry: {len(bars):>4} bars", flush=True)
        if i < len(syms):
            time.sleep(13)

    sim = HistoricalSimulator(
        closes_provider=closes_fn,
        scorer=_score_from_features,
        picks_per_day=args.picks_per_day,
        forward_days=30,
        min_history_days=100,
    )
    result = sim.run(universe=syms, start=sim_start, end=end)

    summary = result.to_dict()
    print("\n" + "=" * 60)
    print("  BACKTEST RESULTS")
    print("=" * 60)
    for k, v in summary.items():
        print(f"  {k:<28} {v}")
    print("=" * 60)
    edge = summary["edge_over_baseline_pct"]
    if result.completed_picks == 0:
        print("\n⚠ No completed picks (not enough forward history).")
    elif edge > 0.5:
        print(f"\n✅ EDGE DETECTED: {edge:+.2f}% vs random baseline.")
    elif edge > -0.5:
        print(f"\n⚠ NO CLEAR EDGE: {edge:+.2f}% vs baseline. Review weights.")
    else:
        print(f"\n❌ NEGATIVE EDGE: {edge:+.2f}%. DO NOT GO LIVE.")

    # Persist full picks + stats
    out_path = args.out
    try:
        with open(out_path, "w") as f:
            payload = {
                "summary": summary,
                "picks": [{
                    "date": p.pick_date.isoformat(),
                    "symbol": p.symbol,
                    "score": round(p.score, 2),
                    "entry": round(p.entry_price, 4),
                    "forward_30d_close": (round(p.forward_30d_close, 4)
                                           if p.forward_30d_close is not None else None),
                    "forward_30d_return_pct": (round(p.forward_30d_return_pct, 3)
                                                 if p.forward_30d_return_pct is not None else None),
                    "breakdown": {k: round(v, 2) for k, v in p.score_breakdown.items()},
                } for p in result.picks],
            }
            json.dump(payload, f, indent=2)
        print(f"\nFull result → {out_path}")
    except OSError as exc:
        print(f"\n⚠ Could not write {out_path}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
