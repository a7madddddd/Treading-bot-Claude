#!/usr/bin/env python3
"""B25 backtest runner: replays the D-0004 Ladder + D-0008 Trailing
Floor strategy against historical daily bars pulled from Alpaca.

Prints a per-trade log and a metrics summary.

Usage:
    PYTHONPATH=src python3 scripts/run_backtest.py \\
        --symbols TSLA,AAPL --years 3

Env vars required:
    ALPACA_API_KEY_ID
    ALPACA_API_SECRET_KEY
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta

_here = os.path.abspath(os.path.dirname(__file__))
_src = os.path.abspath(os.path.join(_here, "..", "src"))
if _src not in sys.path:
    sys.path.insert(0, _src)


def _fail(msg: str) -> "SystemExit":
    print(f"[FAIL] {msg}", file=sys.stderr)
    return SystemExit(1)


def _require_env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise _fail(f"env var {name} is missing")
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
        payload = json.loads(r.read().decode())
    return payload.get("bars", [])


def main() -> int:
    p = argparse.ArgumentParser(description="B25 backtest runner")
    p.add_argument("--symbols", default="TSLA,AAPL,SPY",
                   help="Comma-separated symbols to backtest.")
    p.add_argument("--years", type=float, default=2.0,
                   help="Years of history to pull.")
    p.add_argument("--verbose-trades", action="store_true",
                   help="Print every trade, not just the summary.")
    args = p.parse_args()

    key = _require_env("ALPACA_API_KEY_ID")
    sec = _require_env("ALPACA_API_SECRET_KEY")

    from backtesting.models import Bar
    from backtesting.simulator import BacktestSimulator

    sim = BacktestSimulator()
    symbols = tuple(s.strip().upper() for s in args.symbols.split(",")
                    if s.strip())
    start_date = date.today() - timedelta(days=int(args.years * 365))

    for symbol in symbols:
        print(f"\n{'='*72}")
        print(f"Backtest: {symbol} from {start_date} (last ~{args.years:.1f}y)")
        print(f"{'='*72}")
        raw_bars = _fetch_bars(symbol, start_date, key, sec)
        bars = []
        for b in raw_bars:
            try:
                d = date.fromisoformat(b["t"].split("T")[0])
                bars.append(Bar(bar_date=d,
                                open=float(b["o"]), high=float(b["h"]),
                                low=float(b["l"]), close=float(b["c"]),
                                volume=float(b["v"])))
            except (KeyError, ValueError):
                continue
        if not bars:
            print(f"  no bars fetched for {symbol}; skipping")
            continue

        print(f"  loaded {len(bars)} daily bars "
              f"({bars[0].bar_date} .. {bars[-1].bar_date})")

        result = sim.run(symbol, bars)
        m = result.metrics
        print(f"\n  trades: {m.total_trades}")
        print(f"  win rate: {m.win_rate*100:.1f}% "
              f"({m.winning_trades} wins / {m.losing_trades} losses)")
        print(f"  total P&L: {m.total_pnl:,.2f} USD")
        print(f"  avg return per trade: {m.average_return*100:.2f}%")
        pf = "inf" if m.profit_factor == float("inf") else f"{m.profit_factor:.2f}"
        print(f"  profit factor: {pf}")
        print(f"  max drawdown: {m.max_drawdown:,.2f} USD")
        print(f"  sharpe (per-trade): {m.sharpe_ratio:.3f}")

        if args.verbose_trades:
            print("\n  per-trade log:")
            for t in result.trades:
                print(f"    {t.entry_date}→{t.exit_date} "
                      f"entry={t.entry_price:.2f} exit={t.exit_price:.2f} "
                      f"shares={t.final_shares} pnl={t.pnl():+.2f} "
                      f"({t.exit_reason.value})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
