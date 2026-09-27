#!/usr/bin/env python3
"""D-0046 research cycle runner.

Independent process from the trading Engine (Controller decision 5A
under D-0046). Runs one research pass over a Controller-supplied
symbol list; can be scheduled twice-daily per Controller decision 1B
(before market open + after close).

What it does per cycle, per symbol:
  1. Calls Perplexity Agent API — researchStock + researchNews.
  2. Fetches CapitolTrades records for the configured politician.
  3. Synthesizes a ComparisonRecord.
  4. Appends to `docs/trading/research-log.md`.
  5. Persists reports + records + comparison to SQLite.
  6. Sends a Telegram INFORMATION summary (one message per symbol).

Advisory only. No broker call anywhere in this file.

Usage:
    PYTHONPATH=src python3 scripts/run_research_cycle.py \\
        --symbols TSLA,AAPL,SPY

Env vars required:
    PERPLEXITY_API_KEY
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID
"""

from __future__ import annotations

import argparse
import os
import sys

_here = os.path.abspath(os.path.dirname(__file__))
_repo_root = os.path.abspath(os.path.join(_here, ".."))
_src = os.path.join(_repo_root, "src")
if _src not in sys.path:
    sys.path.insert(0, _src)


def _fail(msg: str) -> "SystemExit":
    print(f"[FAIL] {msg}", file=sys.stderr)
    return SystemExit(1)


def _require_env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise _fail(f"env var {name} is missing or empty")
    return v


def main() -> int:
    p = argparse.ArgumentParser(description="D-0046 research cycle")
    p.add_argument("--symbols", default="TSLA,AAPL,SPY",
                   help="Comma-separated tickers to research")
    p.add_argument("--politician", default="ro-khanna",
                   help="CapitolTrades politician slug (D-0019: Ro Khanna)")
    p.add_argument("--db-path",
                   default=os.environ.get("PAPER_SESSION_DB_PATH",
                                          "./paper_session.sqlite"))
    p.add_argument("--log-path", default="docs/trading/research-log.md")
    p.add_argument("--no-telegram", action="store_true",
                   help="Skip Telegram summaries (writes stay enabled)")
    p.add_argument("--skip-capitol-trades", action="store_true",
                   help="Skip CapitolTrades fetch (Perplexity only)")
    args = p.parse_args()

    perplexity_key = _require_env("PERPLEXITY_API_KEY")
    if not args.no_telegram:
        _require_env("TELEGRAM_BOT_TOKEN")
        _require_env("TELEGRAM_CHAT_ID")

    symbols = tuple(s.strip().upper() for s in args.symbols.split(",")
                    if s.strip())
    if not symbols:
        raise _fail("--symbols is empty")

    from persistence.db import connect, bootstrap_schema
    from research.perplexity_agent import (
        PerplexityAgentClient, PerplexityMigrationRequiredError,
        PerplexityTransportError,
    )
    from research.capitol_trades_scraper import (
        CapitolTradesScraper, CapitolTradesFetchError,
        CapitolTradesParserBrokenError,
    )
    from research.synthesis import synthesize
    from research.log_writer import ResearchLogWriter
    from research.sqlite_repository import ResearchRepository

    conn = connect(args.db_path)
    bootstrap_schema(conn)
    repo = ResearchRepository(conn)
    log = ResearchLogWriter(args.log_path)

    perplexity = PerplexityAgentClient(api_key=perplexity_key)

    ct_records: tuple = ()
    if not args.skip_capitol_trades:
        try:
            scraper = CapitolTradesScraper(politician_slug=args.politician)
            ct_records = scraper.fetch()
            repo.save_capitol_trades(ct_records)
            print(f"[OK] CapitolTrades fetched: {len(ct_records)} records")
        except CapitolTradesParserBrokenError as ex:
            _emit_critical(args, f"CapitolTrades parser broken: {ex}")
            ct_records = ()
        except CapitolTradesFetchError as ex:
            _emit_important(args, f"CapitolTrades fetch failed: {ex}")
            ct_records = ()

    for symbol in symbols:
        print(f"\n=== {symbol} ===")
        pp_reports = []
        try:
            report_stock = perplexity.research_stock(
                symbol,
                "Describe the current market context, near-term drivers, "
                "and key risks for this stock in 2-4 sentences.",
            )
            repo.save_report(report_stock)
            log.append_report(report_stock)
            pp_reports.append(report_stock)
            print(f"  [OK] researchStock -> confidence={report_stock.confidence.value}")

            report_news = perplexity.research_news(
                f"What are the most notable news items for {symbol} in the "
                "last 7 days that could affect its price?",
                subject_symbol=symbol,
            )
            repo.save_report(report_news)
            log.append_report(report_news)
            pp_reports.append(report_news)
            print(f"  [OK] researchNews  -> confidence={report_news.confidence.value}")
        except PerplexityMigrationRequiredError as ex:
            _emit_critical(args, f"Perplexity migration required: {ex}")
            break
        except PerplexityTransportError as ex:
            _emit_important(args, f"Perplexity transport error for {symbol}: {ex}")
            continue

        comparison = synthesize(
            ticker=symbol,
            perplexity_reports=pp_reports,
            capitol_trades_records=ct_records,
        )
        repo.save_comparison(comparison)
        log.append_comparison(comparison)
        print(f"  [OK] comparison -> confidence={comparison.confidence.value}, "
              f"hypothesis={'yes' if comparison.hypothesis else 'no'}")

        if not args.no_telegram:
            _send_telegram_summary(symbol, pp_reports, comparison)

    conn.close()
    return 0


def _send_telegram_summary(symbol: str, reports, comparison) -> None:
    from notifications.telegram import TelegramNotificationService
    from notifications.service import NotificationEvent, NotificationLevel
    lines = [f"Research cycle: {symbol}",
             f"Confidence: {comparison.confidence.value}"]
    if comparison.hypothesis:
        lines.append(comparison.hypothesis)
    for r in reports:
        lines.append(f"[{r.operation.value}] {r.summary[:280]}")
    if comparison.contradictions:
        lines.append("Contradictions: " + "; ".join(comparison.contradictions[:2]))
    notifier = TelegramNotificationService.from_env()
    notifier.send(NotificationEvent(
        level=NotificationLevel.OPTIONAL,
        event="research_cycle_result",
        message="\n".join(lines),
        symbol=symbol, extra=(),
    ))


def _emit_important(args, msg: str) -> None:
    print(f"[WARN] {msg}", file=sys.stderr)
    if args.no_telegram:
        return
    try:
        from notifications.telegram import TelegramNotificationService
        from notifications.service import NotificationEvent, NotificationLevel
        TelegramNotificationService.from_env().send(NotificationEvent(
            level=NotificationLevel.IMPORTANT,
            event="research_cycle_warning",
            message=msg, symbol=None, extra=(),
        ))
    except Exception as ex:
        print(f"[WARN] telegram notify failed: {ex}", file=sys.stderr)


def _emit_critical(args, msg: str) -> None:
    print(f"[CRITICAL] {msg}", file=sys.stderr)
    if args.no_telegram:
        return
    try:
        from notifications.telegram import TelegramNotificationService
        from notifications.service import NotificationEvent, NotificationLevel
        TelegramNotificationService.from_env().send(NotificationEvent(
            level=NotificationLevel.CRITICAL,
            event="research_cycle_critical",
            message=msg, symbol=None, extra=(),
        ))
    except Exception as ex:
        print(f"[CRITICAL] telegram notify failed: {ex}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
