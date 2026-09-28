#!/usr/bin/env python3
"""B24 daily universe selection runner.

Runs one D-0026 pipeline pass against real Alpaca data and saves
the resulting ApprovedUniverseSnapshot to SQLite. Advisory-only:
never places any broker order.

Flow:
  1. AlpacaAssetsProvider fetches active US equities (optionally
     filtered by --whitelist).
  2. Identity resolver assigns a security_id per symbol.
  3. AlpacaFeatureEnricher pulls the last ~45 daily bars per
     symbol and computes liquidity / ATR / momentum / execution
     quality proxy.
  4. UniversePipeline runs the eight percentage-only stages
     (D-0048).
  5. SnapshotUniverseSource-compatible ApprovedUniverseSnapshot
     is saved via SqliteSnapshotRepository.
  6. A Telegram summary is sent (optional).

Usage:
    PYTHONPATH=src python3 scripts/run_universe_selection.py \\
        --whitelist TSLA,AAPL,SPY,NVDA,MSFT --send-telegram
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timezone


_here = os.path.abspath(os.path.dirname(__file__))
_repo_root = os.path.abspath(os.path.join(_here, ".."))
_src = os.path.join(_repo_root, "src")
if _src not in sys.path:
    sys.path.insert(0, _src)


def _require_env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        print(f"[FAIL] env var {name} is missing", file=sys.stderr)
        raise SystemExit(1)
    return v


def main() -> int:
    p = argparse.ArgumentParser(description="B24 universe selection runner")
    p.add_argument("--whitelist", default="",
                   help="Comma-separated symbols to consider (empty = "
                        "every tradable US equity in the account).")
    p.add_argument("--db-path",
                   default=os.environ.get("PAPER_SESSION_DB_PATH",
                                          "./paper_session.sqlite"))
    p.add_argument("--send-telegram", action="store_true",
                   help="Send an OPTIONAL-level summary to Telegram.")
    p.add_argument("--effective-date",
                   help="Override effective trading date (YYYY-MM-DD); "
                        "default is today UTC.")
    args = p.parse_args()

    key = _require_env("ALPACA_API_KEY_ID")
    sec = _require_env("ALPACA_API_SECRET_KEY")
    base = _require_env("ALPACA_BASE_URL")
    if args.send_telegram:
        _require_env("TELEGRAM_BOT_TOKEN")
        _require_env("TELEGRAM_CHAT_ID")

    whitelist = tuple(s.strip().upper() for s in args.whitelist.split(",")
                      if s.strip())
    effective = (date.fromisoformat(args.effective_date)
                 if args.effective_date else date.today())

    from persistence.db import connect, bootstrap_schema
    from d0026.alpaca_provider import AlpacaAssetsProvider
    from d0026.alpaca_enricher import AlpacaFeatureEnricher
    from d0026.config import UniverseSelectionConfig
    from d0026.identity import (
        IdentityConfidence, IdentityResolution, IdentityResolver,
        ResolutionOutcome, SecurityIdentity, TickerAlias,
    )
    from d0026.models import RegimeLabel, RegimeState
    from d0026.observability import AuditSink
    from d0026.pipeline import UniversePipeline
    from d0026.sqlite_repository import SqliteSnapshotRepository
    from d0026.stages import default_percentage_evaluators

    conn = connect(args.db_path)
    bootstrap_schema(conn)

    provider = AlpacaAssetsProvider(
        key_id=key, secret_key=sec, base_url=base,
        symbol_whitelist=whitelist or None,
    )
    enricher = AlpacaFeatureEnricher(key_id=key, secret_key=sec)

    class _TickerAsIdentityResolver(IdentityResolver):
        """Interim resolver: treats ticker itself as the stable
        security_id. Sufficient for the paper-trading path where
        ticker reuse is not currently in scope; a proper CIK-backed
        resolver replaces this in a future decision if delisted-
        ticker reuse becomes a real concern."""
        def resolve(self, ticker, as_of):
            i = SecurityIdentity(
                security_id=f"alpaca-{ticker}",
                display_name=ticker, cik=None,
                confidence=IdentityConfidence.PROVISIONAL,
            )
            a = TickerAlias(security_id=i.security_id, ticker=ticker,
                            effective_start=None, effective_end=None)
            return IdentityResolution(
                outcome=ResolutionOutcome.RESOLVED, ticker=ticker,
                as_of_date=as_of, identity=i, alias=a,
            )

    class _StdoutSink(AuditSink):
        def record(self, event):
            print(f"[audit] {type(event).__name__}")

    regime = RegimeState(
        as_of_date=effective,
        label=RegimeLabel.UNCLASSIFIED_PENDING_CALIBRATION,
        reference_series_values=(("vix_percentile", 0.5),),
        classification_method_version="stub-v1",
    )

    repo = SqliteSnapshotRepository(conn)
    pipeline = UniversePipeline(
        provider=provider,
        identity_resolver=_TickerAsIdentityResolver(),
        stage_evaluators=default_percentage_evaluators(
            UniverseSelectionConfig()
        ),
        snapshot_repository=repo,
        audit_sink=_StdoutSink(),
        selection_version="D-0048-v1",
        universe_source_version="alpaca-assets-v1",
        identity_mapping_version="ticker-as-id-v1",
        feature_enricher=enricher,
    )
    print(f"[run] effective={effective}, "
          f"whitelist={list(whitelist) or 'ALL'}")
    outcome = pipeline.run(effective, regime)

    from d0026.failure import CrashOutcome, SnapshotOutcome
    if isinstance(outcome, CrashOutcome):
        print(f"[CRASH] {outcome.category.value}: {outcome.detail}",
              file=sys.stderr)
        _maybe_telegram(args, "CRITICAL",
                        f"universe selection CRASH: {outcome.detail[:200]}")
        return 1

    snap = outcome.snapshot
    print(f"[snapshot] id={snap.snapshot_id[:16]}..., "
          f"is_empty={snap.is_empty}, symbols={len(snap.symbols)}")
    for s in snap.symbols[:10]:
        print(f"  #{s.rank} {s.ticker_as_of_date}")
    _maybe_telegram(
        args, "OPTIONAL",
        (f"Universe {effective}: {len(snap.symbols)} symbols. "
         + ", ".join(s.ticker_as_of_date for s in snap.symbols[:10])),
    )
    return 0


def _maybe_telegram(args, level: str, message: str) -> None:
    if not args.send_telegram:
        return
    try:
        from notifications.telegram import TelegramNotificationService
        from notifications.service import (
            NotificationEvent, NotificationLevel,
        )
        TelegramNotificationService.from_env().send(NotificationEvent(
            level=NotificationLevel[level],
            event="universe_selection_result",
            message=message, symbol=None, extra=(),
        ))
    except Exception as ex:
        print(f"[warn] telegram send failed: {ex}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
