"""Political-signal backtest via FREE CapitolTrades scraper
(Combined-Solution Phase 1, QuiverQuant-free fallback).

Scrapes each of the 15 whitelisted politicians' pages on
capitoltrades.com (public web page, no API key, no subscription).
For each Purchase trade in the backtest window, computes the stock's
30-day forward return and compares the mean to a random-date baseline.

Zero paid APIs. Uses:
  * capitoltrades.com public pages — one request per politician
  * Polygon REST aggregates — one call per discovered ticker,
    13s throttle between calls to respect the 5/min free limit.

Usage:
  cd ~/Treading-bot-Claude && set -a && source .env && set +a && \\
  PYTHONPATH=src python3.11 scripts/run_political_scraper_backtest.py \\
      --months 12

Writes ./political_scraper_backtest_result.json.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from marketdata.polygon_source import PolygonSource               # noqa: E402
from research.capitol_trades_scraper import CapitolTradesScraper  # noqa: E402
from research.politicians import WHITELIST                        # noqa: E402


_FORWARD_DAYS = 30
_RANDOM_SEED = 42


def _name_to_slug(name: str) -> str:
    """'Nancy Pelosi' → 'nancy-pelosi'."""
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"\s+", "-", s)
    return s


@dataclass
class PoliticalPick:
    trade_date: str
    symbol: str
    politician: str
    alpha_weight: float
    entry_price: float
    forward_price: Optional[float]
    forward_return_pct: Optional[float]


def _scrape_politician(slug: str) -> List[dict]:
    """Returns list of {ticker, trade_date, transaction_type}."""
    try:
        scraper = CapitolTradesScraper(politician_slug=slug)
        records = scraper.fetch()
    except Exception as ex:  # noqa: BLE001
        print(f"      ⚠ scrape failed for {slug}: {type(ex).__name__}",
              flush=True)
        return []
    out: List[dict] = []
    for r in records:
        if not r.ticker:
            continue
        # publication_date is tz-aware datetime
        td = r.disclosed_trade_date or r.publication_date
        if td is None:
            continue
        if isinstance(td, datetime):
            td = td.date()
        out.append({
            "ticker": r.ticker.upper(),
            "trade_date": td,
            "transaction_type": r.transaction_type or "",
        })
    return out


def _is_purchase(txn: str) -> bool:
    t = (txn or "").lower()
    return "buy" in t or "purchase" in t


def _closes(pg: PolygonSource, syms: List[str], start: date, end: date,
            throttle_s: float = 13.0) -> Dict[str, Dict[date, float]]:
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
            print(f"      ⚠ rate-limited; waiting 65s and retrying...",
                  flush=True)
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
            time.sleep(throttle_s)
    return out


def _forward(closes: Dict[date, float], trade_date: date,
             forward_days: int) -> Tuple[Optional[float], Optional[float]]:
    if not closes:
        return None, None
    sd = sorted(closes.keys())
    entry_d = next((d for d in sd if d >= trade_date), None)
    if entry_d is None:
        return None, None
    exit_d = next((d for d in sd if d >= entry_d + timedelta(days=forward_days)),
                  None)
    return closes[entry_d], (closes[exit_d] if exit_d else None)


def _summarize(picks: List[PoliticalPick], label: str) -> dict:
    completed = [p for p in picks if p.forward_return_pct is not None]
    if not completed:
        return {"label": label, "n": 0}
    rets = [p.forward_return_pct for p in completed]
    mean = sum(rets) / len(rets)
    wins = sum(1 for r in rets if r > 0) / len(rets) * 100
    median = sorted(rets)[len(rets) // 2]
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    stdev = var ** 0.5
    return {
        "label": label,
        "n": len(completed),
        "mean_return_pct": round(mean, 3),
        "median_return_pct": round(median, 3),
        "win_rate_pct": round(wins, 2),
        "best_pct": round(max(rets), 3),
        "worst_pct": round(min(rets), 3),
        "sharpe_like": round(mean / stdev if stdev > 0 else 0.0, 3),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Political-only backtest (scraper)")
    p.add_argument("--months", type=int, default=12)
    p.add_argument("--out", default="./political_scraper_backtest_result.json")
    args = p.parse_args()

    pg = PolygonSource.from_env()
    if pg is None:
        print("ERROR: POLYGON_API_KEY unset.", file=sys.stderr)
        return 2

    end = date.today()
    start = end - timedelta(days=args.months * 31)

    print(f"Scraping {len(WHITELIST)} politicians from capitoltrades.com...")
    all_trades: List[dict] = []
    for i, prof in enumerate(WHITELIST, 1):
        slug = _name_to_slug(prof.name)
        rec = _scrape_politician(slug)
        kept = [t for t in rec
                if _is_purchase(t["transaction_type"])
                and start <= t["trade_date"] <= end]
        for t in kept:
            t["politician"] = prof.name
            t["alpha_weight"] = prof.alpha_weight
        all_trades.extend(kept)
        print(f"  [{i:>2}/{len(WHITELIST)}] {prof.name:<28} "
              f"total={len(rec):>4}  in-window-purchases={len(kept):>3}",
              flush=True)
        time.sleep(2)  # polite throttle on capitoltrades.com

    if not all_trades:
        print("\n⚠ No qualifying trades found. CapitolTrades HTML may have "
              "changed or politicians have no recent purchases.")
        return 1

    unique_syms = sorted({t["ticker"] for t in all_trades})
    print(f"\n{len(unique_syms)} unique tickers across {len(all_trades)} "
          f"purchases.")

    data_start = start - timedelta(days=30)
    data_end = end + timedelta(days=1)

    print("\nFetching Polygon daily closes...")
    closes = _closes(pg, unique_syms, data_start, data_end)
    valid_syms = [s for s in unique_syms if closes.get(s)]
    print(f"  {len(valid_syms)}/{len(unique_syms)} tickers have data.\n")

    political_picks: List[PoliticalPick] = []
    for t in all_trades:
        s = t["ticker"]
        if s not in closes or not closes[s]:
            continue
        entry, forward = _forward(closes[s], t["trade_date"], _FORWARD_DAYS)
        if entry is None:
            continue
        ret_pct = (((forward / entry) - 1.0) * 100.0
                   if forward is not None else None)
        political_picks.append(PoliticalPick(
            trade_date=t["trade_date"].isoformat(),
            symbol=s,
            politician=t["politician"],
            alpha_weight=t["alpha_weight"],
            entry_price=round(entry, 4),
            forward_price=round(forward, 4) if forward else None,
            forward_return_pct=round(ret_pct, 3) if ret_pct is not None else None,
        ))

    # Random baseline: same number of picks, random symbol+date.
    rng = random.Random(_RANDOM_SEED)
    baseline: List[PoliticalPick] = []
    total_days = (end - start).days
    for _ in political_picks:
        rsym = rng.choice(valid_syms)
        rday = start + timedelta(days=rng.randint(0, max(1, total_days - 1)))
        entry, forward = _forward(closes[rsym], rday, _FORWARD_DAYS)
        if entry is None:
            continue
        ret_pct = (((forward / entry) - 1.0) * 100.0
                   if forward is not None else None)
        baseline.append(PoliticalPick(
            trade_date=rday.isoformat(), symbol=rsym, politician="(random)",
            alpha_weight=0.0, entry_price=round(entry, 4),
            forward_price=round(forward, 4) if forward else None,
            forward_return_pct=round(ret_pct, 3) if ret_pct is not None else None,
        ))

    pol = _summarize(political_picks, "political_whitelist")
    base = _summarize(baseline, "random_baseline")

    print("=" * 60)
    print("  POLITICAL BACKTEST (scraper) RESULTS")
    print("=" * 60)
    print(f"  Window:                   {start} → {end}")
    print(f"  Politicians scraped:      {len(WHITELIST)}")
    print(f"  In-window purchases:      {len(political_picks)}")
    print(f"  Unique tickers:           {len(valid_syms)}")
    print("-" * 60)
    print(f"  POLITICAL  n={pol.get('n', 0):>4}  "
          f"mean={pol.get('mean_return_pct', 0):>+6.2f}%  "
          f"win={pol.get('win_rate_pct', 0):>5.2f}%  "
          f"sharpe={pol.get('sharpe_like', 0):>+5.2f}")
    print(f"  BASELINE   n={base.get('n', 0):>4}  "
          f"mean={base.get('mean_return_pct', 0):>+6.2f}%  "
          f"win={base.get('win_rate_pct', 0):>5.2f}%  "
          f"sharpe={base.get('sharpe_like', 0):>+5.2f}")
    if pol.get("n") and base.get("n"):
        edge = pol["mean_return_pct"] - base["mean_return_pct"]
        print("-" * 60)
        print(f"  EDGE:                     {edge:+.2f}%")
        if edge > 1.0:
            print(f"\n✅ POLITICAL SIGNAL HAS EDGE: {edge:+.2f}%")
        elif edge > -1.0:
            print(f"\n⚠ POLITICAL SIGNAL NEUTRAL: {edge:+.2f}%")
        else:
            print(f"\n❌ POLITICAL SIGNAL NEGATIVE: {edge:+.2f}%")
    print("=" * 60)

    try:
        with open(args.out, "w") as f:
            json.dump({
                "window_start": start.isoformat(),
                "window_end": end.isoformat(),
                "politicians_scraped": [p.name for p in WHITELIST],
                "universe": valid_syms,
                "forward_days": _FORWARD_DAYS,
                "political_stats": pol,
                "baseline_stats": base,
                "political_picks": [asdict(p) for p in political_picks],
                "baseline_picks": [asdict(p) for p in baseline],
            }, f, indent=2)
        print(f"\nFull result → {args.out}")
    except OSError as exc:
        print(f"\n⚠ Could not write {args.out}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
