"""Political-signal-only backtest (Combined-Solution Phase 1).

Isolates the Capitol Trades signal by backtesting the 15-politician
whitelist against the past 12 months of QuiverQuant data. For each
whitelisted politician's Purchase trade, computes the stock's forward
30-day return and compares the mean to a random-date baseline on the
same universe.

Zero paid APIs. Zero look-ahead. Uses:
  * QuiverQuant free tier (/beta/historical/congresstrading/{symbol})
    — one call per universe symbol, 150/month budget.
  * Polygon REST aggregates — one call per symbol with 13s throttle.

Usage:
  cd ~/Treading-bot-Claude && set -a && source .env && set +a && \\
  PYTHONPATH=src python3.11 scripts/run_political_backtest.py \\
      --symbols AAPL,MSFT,NVDA,TSLA,GOOGL,META,AMZN,JPM,UNH,V,XOM,SPY \\
      --months 12

Writes ./political_backtest_result.json.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from marketdata.polygon_source import PolygonSource          # noqa: E402
from marketdata.quiverquant_source import QuiverQuantSource  # noqa: E402
from research.politicians import lookup                      # noqa: E402


_FORWARD_DAYS = 30
_RANDOM_SEED = 42


@dataclass
class PoliticalPick:
    trade_date: str
    symbol: str
    politician: str
    alpha_weight: float
    entry_price: float
    forward_price: Optional[float]
    forward_return_pct: Optional[float]


def _parse_trade_date(s: str) -> Optional[date]:
    if not isinstance(s, str) or not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y"):
        try:
            return datetime.strptime(s[:19], fmt).date()
        except ValueError:
            continue
    return None


def _is_purchase(txn: str) -> bool:
    """QuiverQuant uses 'Purchase', 'Sale (Partial)', 'Sale (Full)'."""
    if not isinstance(txn, str):
        return False
    t = txn.lower()
    return "purchase" in t and "sale" not in t


def _closes_for_universe(pg: PolygonSource, syms: List[str],
                         start: date, end: date) -> Dict[str, Dict[date, float]]:
    """Returns {symbol: {date: close}} with 13s throttle + 65s retry."""
    out: Dict[str, Dict[date, float]] = {}
    for i, s in enumerate(syms, 1):
        try:
            bars = pg.get_aggregates(s, 1, "day", start, end, adjusted=True)
        except Exception:  # noqa: BLE001
            bars = []
        mp = {d: float(row.get("c"))
              for d, row in bars
              if isinstance(row, dict) and isinstance(row.get("c"), (int, float))}
        print(f"  [{i:>3}/{len(syms)}] {s:<6} {len(mp):>4} days", flush=True)
        if not mp and i < len(syms):
            print(f"      ⚠ rate-limited, waiting 65s and retrying...", flush=True)
            time.sleep(65)
            try:
                bars = pg.get_aggregates(s, 1, "day", start, end, adjusted=True)
            except Exception:  # noqa: BLE001
                bars = []
            mp = {d: float(row.get("c"))
                  for d, row in bars
                  if isinstance(row, dict) and isinstance(row.get("c"), (int, float))}
            print(f"      retry: {len(mp)} days", flush=True)
        out[s] = mp
        if i < len(syms):
            time.sleep(13)
    return out


def _forward_price(closes: Dict[date, float], trade_date: date,
                   forward_days: int) -> Tuple[Optional[float], Optional[float]]:
    """Find the first trading-day close >= trade_date (entry) and the
    first trading-day close >= trade_date + forward_days (exit)."""
    if not closes:
        return None, None
    # Entry: first available close on or after trade_date.
    sorted_dates = sorted(closes.keys())
    entry_d = next((d for d in sorted_dates if d >= trade_date), None)
    if entry_d is None:
        return None, None
    exit_target = entry_d + timedelta(days=forward_days)
    exit_d = next((d for d in sorted_dates if d >= exit_target), None)
    entry_px = closes[entry_d]
    exit_px = closes[exit_d] if exit_d is not None else None
    return entry_px, exit_px


def _summarize(picks: List[PoliticalPick], label: str) -> Dict:
    completed = [p for p in picks if p.forward_return_pct is not None]
    if not completed:
        return {"label": label, "n": 0}
    rets = [p.forward_return_pct for p in completed]
    mean = sum(rets) / len(rets)
    wins = sum(1 for r in rets if r > 0) / len(rets) * 100
    median = sorted(rets)[len(rets) // 2]
    # Simple Sharpe-like: mean / stdev
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    stdev = var ** 0.5
    sharpe = mean / stdev if stdev > 0 else 0.0
    return {
        "label": label,
        "n": len(completed),
        "mean_return_pct": round(mean, 3),
        "median_return_pct": round(median, 3),
        "win_rate_pct": round(wins, 2),
        "best_pct": round(max(rets), 3),
        "worst_pct": round(min(rets), 3),
        "sharpe_like": round(sharpe, 3),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Political-only backtest (Phase 1)")
    p.add_argument("--symbols",
                   default="AAPL,MSFT,NVDA,TSLA,GOOGL,META,AMZN,JPM,UNH,V,XOM,SPY")
    p.add_argument("--months", type=int, default=12)
    p.add_argument("--out", default="./political_backtest_result.json")
    args = p.parse_args()

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    end = date.today()
    start = end - timedelta(days=args.months * 31)
    # Need +60 days after start for 30-day forward window + safety.
    data_end = end + timedelta(days=1)
    data_start = start - timedelta(days=30)

    print(f"Universe:   {len(syms)} symbols")
    print(f"Window:     {start} → {end}  ({args.months} months)")
    print(f"Forward:    {_FORWARD_DAYS} days")

    pg = PolygonSource.from_env()
    qq = QuiverQuantSource.from_env()
    if pg is None:
        print("ERROR: POLYGON_API_KEY unset.", file=sys.stderr)
        return 2
    if qq is None:
        print("ERROR: QUIVER_QUANT_API_KEY unset — add it to .env.",
              file=sys.stderr)
        return 2

    # Fetch daily closes for universe (one Polygon call per symbol).
    print("\nFetching Polygon daily closes...")
    closes = _closes_for_universe(pg, syms, data_start, data_end)
    valid_syms = [s for s in syms if closes.get(s)]
    print(f"  {len(valid_syms)}/{len(syms)} symbols have data.\n")

    # Fetch QuiverQuant trades per symbol.
    print("Fetching QuiverQuant historical trades...")
    political_picks: List[PoliticalPick] = []
    skipped_not_whitelisted = 0
    skipped_not_purchase = 0
    skipped_outside_window = 0
    for i, s in enumerate(valid_syms, 1):
        trades = qq.historical_trades(s)
        print(f"  [{i:>3}/{len(valid_syms)}] {s:<6} {len(trades):>5} total trades",
              flush=True)
        for t in trades:
            td = _parse_trade_date(t.get("TransactionDate", ""))
            if td is None or td < start or td > end:
                skipped_outside_window += 1
                continue
            if not _is_purchase(t.get("Transaction", "")):
                skipped_not_purchase += 1
                continue
            rep = t.get("Representative", "")
            prof = lookup(rep)
            if prof is None:
                skipped_not_whitelisted += 1
                continue
            entry, forward = _forward_price(closes[s], td, _FORWARD_DAYS)
            if entry is None:
                continue
            ret_pct = (((forward / entry) - 1.0) * 100.0
                       if forward is not None else None)
            political_picks.append(PoliticalPick(
                trade_date=td.isoformat(),
                symbol=s,
                politician=prof.name,
                alpha_weight=prof.alpha_weight,
                entry_price=round(entry, 4),
                forward_price=round(forward, 4) if forward else None,
                forward_return_pct=round(ret_pct, 3) if ret_pct is not None else None,
            ))
        # Light throttle between QuiverQuant calls.
        if i < len(valid_syms):
            time.sleep(1)

    # Build random baseline: for each political pick, replace the symbol/date
    # with a random pick from the universe and a random date in the window.
    rng = random.Random(_RANDOM_SEED)
    baseline: List[PoliticalPick] = []
    total_days = (end - start).days
    for pp in political_picks:
        rsym = rng.choice(valid_syms)
        rday = start + timedelta(days=rng.randint(0, max(1, total_days - 1)))
        entry, forward = _forward_price(closes[rsym], rday, _FORWARD_DAYS)
        if entry is None:
            continue
        ret_pct = (((forward / entry) - 1.0) * 100.0
                   if forward is not None else None)
        baseline.append(PoliticalPick(
            trade_date=rday.isoformat(),
            symbol=rsym,
            politician="(random)",
            alpha_weight=0.0,
            entry_price=round(entry, 4),
            forward_price=round(forward, 4) if forward else None,
            forward_return_pct=round(ret_pct, 3) if ret_pct is not None else None,
        ))

    pol_stats = _summarize(political_picks, "political_whitelist")
    base_stats = _summarize(baseline, "random_baseline")

    print("\n" + "=" * 60)
    print("  POLITICAL BACKTEST RESULTS")
    print("=" * 60)
    print(f"  Universe size:                 {len(valid_syms)}")
    print(f"  Window:                        {start} → {end}")
    print(f"  Forward days:                  {_FORWARD_DAYS}")
    print(f"  Qualifying political trades:   {len(political_picks)}")
    print(f"  Skipped (outside window):      {skipped_outside_window}")
    print(f"  Skipped (not purchase):        {skipped_not_purchase}")
    print(f"  Skipped (not in whitelist):    {skipped_not_whitelisted}")
    print("-" * 60)
    print(f"  POLITICAL  n={pol_stats.get('n', 0):>4}  "
          f"mean={pol_stats.get('mean_return_pct', 0):>+6.2f}%  "
          f"win={pol_stats.get('win_rate_pct', 0):>5.2f}%  "
          f"sharpe={pol_stats.get('sharpe_like', 0):>+5.2f}")
    print(f"  BASELINE   n={base_stats.get('n', 0):>4}  "
          f"mean={base_stats.get('mean_return_pct', 0):>+6.2f}%  "
          f"win={base_stats.get('win_rate_pct', 0):>5.2f}%  "
          f"sharpe={base_stats.get('sharpe_like', 0):>+5.2f}")
    if pol_stats.get("n") and base_stats.get("n"):
        edge = (pol_stats["mean_return_pct"] - base_stats["mean_return_pct"])
        print("-" * 60)
        print(f"  EDGE:                        {edge:+.2f}%")
        if edge > 1.0:
            print(f"\n✅ POLITICAL SIGNAL HAS EDGE: {edge:+.2f}% over random.")
        elif edge > -1.0:
            print(f"\n⚠ POLITICAL SIGNAL IS NEUTRAL: {edge:+.2f}%.")
        else:
            print(f"\n❌ POLITICAL SIGNAL IS NEGATIVE: {edge:+.2f}%. Rethink.")
    print("=" * 60)

    try:
        with open(args.out, "w") as f:
            json.dump({
                "window_start": start.isoformat(),
                "window_end": end.isoformat(),
                "universe": valid_syms,
                "forward_days": _FORWARD_DAYS,
                "political_stats": pol_stats,
                "baseline_stats": base_stats,
                "political_picks": [asdict(p) for p in political_picks],
                "baseline_picks": [asdict(p) for p in baseline],
                "skipped": {
                    "outside_window": skipped_outside_window,
                    "not_purchase": skipped_not_purchase,
                    "not_whitelisted": skipped_not_whitelisted,
                },
            }, f, indent=2)
        print(f"\nFull result → {args.out}")
    except OSError as exc:
        print(f"\n⚠ Could not write {args.out}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
