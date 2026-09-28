#!/usr/bin/env python3
"""B25b portfolio backtest runner (Alpaca history).

Runs the D-0004 strategy across a portfolio of symbols with D-0047
risk limits applied, using historical daily bars.

Usage:
    PYTHONPATH=src python3 scripts/run_portfolio_backtest.py \\
        --symbols TSLA,AAPL,SPY,NVDA,MSFT,AMZN --years 3
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.request
from datetime import date, timedelta

_here = os.path.abspath(os.path.dirname(__file__))
_src = os.path.abspath(os.path.join(_here, "..", "src"))
if _src not in sys.path:
    sys.path.insert(0, _src)


def _require_env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        print(f"[FAIL] env var {name} missing", file=sys.stderr)
        raise SystemExit(1)
    return v


def _fetch_bars(symbol: str, start: date, key: str, secret: str) -> list:
    url = (f"https://data.alpaca.markets/v2/stocks/{symbol}/bars"
           f"?feed=iex&timeframe=1Day&start={start.isoformat()}"
           f"&limit=10000&sort=asc")
    req = urllib.request.Request(url, headers={
        "APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret,
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30,
                                context=ssl.create_default_context()) as r:
        return json.loads(r.read().decode()).get("bars", [])


def main() -> int:
    p = argparse.ArgumentParser(description="B25b portfolio backtest")
    p.add_argument("--symbols", default="TSLA,AAPL,SPY,NVDA,MSFT",
                   help="Comma-separated symbols.")
    p.add_argument("--years", type=float, default=3.0)
    p.add_argument("--initial-cash", type=float, default=50_000.0)
    p.add_argument("--verbose-trades", action="store_true")
    args = p.parse_args()

    key = _require_env("ALPACA_API_KEY_ID")
    sec = _require_env("ALPACA_API_SECRET_KEY")

    from backtesting.models import Bar
    from backtesting.portfolio_simulator import PortfolioSimulator
    from backtesting.portfolio_models import PortfolioBacktestConfig

    symbols = tuple(s.strip().upper() for s in args.symbols.split(",")
                    if s.strip())
    start = date.today() - timedelta(days=int(args.years * 365))

    bars_by_symbol = {}
    print(f"Fetching bars for {len(symbols)} symbols since {start}...")
    for sym in symbols:
        raw = _fetch_bars(sym, start, key, sec)
        bars = []
        for b in raw:
            try:
                d = date.fromisoformat(b["t"].split("T")[0])
                bars.append(Bar(bar_date=d, open=float(b["o"]),
                                high=float(b["h"]), low=float(b["l"]),
                                close=float(b["c"]), volume=float(b["v"])))
            except (KeyError, ValueError):
                continue
        bars_by_symbol[sym] = bars
        print(f"  {sym}: {len(bars)} bars")

    cfg = PortfolioBacktestConfig(initial_cash=args.initial_cash)
    print(f"\nRunning portfolio backtest (D-0047 limits applied)...")
    result = PortfolioSimulator(cfg).run(bars_by_symbol)
    m = result.metrics

    print(f"\n{'='*72}")
    print(f"PORTFOLIO BACKTEST RESULT")
    print(f"{'='*72}")
    print(f"  initial cash: {m.initial_cash:,.2f} USD")
    print(f"  final equity: {m.final_equity:,.2f} USD")
    print(f"  total P&L:    {m.total_pnl:+,.2f} USD")
    print(f"  total return: {m.total_return*100:+.2f}%")
    print(f"  peak equity:  {m.peak_equity:,.2f} USD")
    print(f"  max drawdown: {m.max_drawdown:,.2f} USD "
          f"({m.max_drawdown_fraction*100:.2f}%)")
    print(f"\n  trades: {m.total_trades} "
          f"({m.winning_trades} wins / {m.losing_trades} losses)")
    print(f"  win rate: {m.win_rate*100:.1f}%")
    pf = "inf" if m.profit_factor == float("inf") else f"{m.profit_factor:.2f}"
    print(f"  profit factor: {pf}")
    print(f"  per-trade sharpe: {m.sharpe_ratio:.3f}")
    print(f"\n  D-0047 rejections: {m.total_rejections}")
    for reason, count in m.rejection_counts:
        print(f"    {reason}: {count}")

    if args.verbose_trades:
        print(f"\n{'='*72}")
        print("PER-TRADE LOG")
        print(f"{'='*72}")
        for t in result.trades:
            print(f"  {t.entry_date}→{t.exit_date} {t.symbol:6s} "
                  f"entry={t.entry_price:8.2f} exit={t.exit_price:8.2f} "
                  f"shares={t.final_shares:3d} pnl={t.pnl():+9.2f} "
                  f"({t.exit_reason.value})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
