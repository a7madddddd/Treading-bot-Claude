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
    p.add_argument("--exclude", default="",
                   help="Comma-separated extra symbols to exclude from the "
                        "universe pool before enrichment (Controller-approved "
                        "override on top of the auto-exclude of currently-open "
                        "trades).")
    p.add_argument("--max-symbols", type=int, default=0,
                   help="Operational cap on the number of candidates "
                        "entering stage A (after whitelist + exclusion). "
                        "0 (default) = no cap, process the full fetched "
                        "universe. Use a positive value (e.g. 500) for "
                        "fast interactive runs; percentile math stays "
                        "well-defined on any sample >= ~50.")
    p.add_argument("--progress-every", type=int, default=25,
                   help="Print a progress line every N identity "
                        "resolutions (default 25). Set 0 to disable.")
    p.add_argument("--no-exclude-open", action="store_true",
                   help="Disable the automatic exclusion of symbols with an "
                        "open (AWAITING_INITIAL_FILL or ACTIVE) trade in the "
                        "DB. By default those are excluded so the pipeline "
                        "never spends enrichment API calls on symbols the "
                        "engine would skip at proposal time anyway. ABANDONED "
                        "trades are NOT excluded -- Controller-approved "
                        "2026-10-01: a symbol rejected on an earlier day may "
                        "be re-proposed if it ranks today.")
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

    # Build the exclusion set. Controller-approved 2026-10-01:
    #   - auto-exclude symbols with an open (AWAITING_INITIAL_FILL /
    #     ACTIVE) trade -- the engine would skip them anyway, so
    #     spending enrichment API calls on them is pure waste.
    #   - DO NOT exclude ABANDONED trades: a symbol the Controller
    #     rejected yesterday may legitimately rank today if it is
    #     trending. The engine's _check_watchlist will still ask for
    #     approval, which is the right human-in-the-loop gate.
    #   - allow a manual --exclude override for any extra symbols the
    #     Controller does not want proposed today.
    exclude: set = set()
    manual_exclude = {s.strip().upper() for s in args.exclude.split(",")
                      if s.strip()}
    exclude.update(manual_exclude)
    if not args.no_exclude_open:
        from trade.sqlite_repository import SqliteTradeRepository
        from trade.models import describe_status
        trade_repo = SqliteTradeRepository(conn)
        auto_excluded = set()
        for record in trade_repo.list_active():
            # list_active() returns AWAITING_INITIAL_FILL + ACTIVE only
            # (src/trade/sqlite_repository.py:_ACTIVE_STATUSES) -- EXCLUDES
            # ABANDONED, which is exactly the Controller-approved shape here.
            if describe_status(record.trade) in ("AWAITING_INITIAL_FILL", "ACTIVE"):
                auto_excluded.add(record.trade.symbol)
        exclude.update(auto_excluded)
        if auto_excluded:
            print(f"[exclude] auto-excluded open positions: "
                  f"{sorted(auto_excluded)}")
    if manual_exclude:
        print(f"[exclude] manual --exclude: {sorted(manual_exclude)}")

    # Apply exclusion to whitelist (if any) OR let the provider know not
    # to fetch excluded symbols from the all-equities feed. For
    # whitelist mode we filter here; for all-equities mode we also
    # filter here after the fetch (AlpacaAssetsProvider does not have
    # an exclude-list parameter today, and adding one is out of scope
    # for this small change -- a wrapper around the provider is enough).
    if whitelist:
        whitelist = tuple(s for s in whitelist if s not in exclude)
        if not whitelist:
            print("[FAIL] every whitelist entry is in the exclusion set",
                  file=sys.stderr)
            raise SystemExit(1)

    raw_provider = AlpacaAssetsProvider(
        key_id=key, secret_key=sec, base_url=base,
        symbol_whitelist=whitelist or None,
    )

    class _ExcludingProvider:
        """Thin wrapper: drops excluded tickers after the base provider
        fetches, so the whole-market path respects --exclude /
        --no-exclude-open too. Matches the provider protocol
        (get_raw_candidates(as_of_date) -> Tuple[RawCandidateRef, ...])."""
        def __init__(self, inner, excluded):
            self._inner = inner
            self._excluded = frozenset(excluded)
        def get_raw_candidates(self, as_of_date):
            raw = self._inner.get_raw_candidates(as_of_date)
            if not self._excluded:
                return raw
            return tuple(r for r in raw if r.ticker not in self._excluded)

    provider = _ExcludingProvider(raw_provider, exclude) if exclude else raw_provider

    # --max-symbols: operational cap for interactive runs.
    if args.max_symbols and args.max_symbols > 0:
        class _CappingProvider:
            def __init__(self, inner, cap):
                self._inner = inner
                self._cap = cap
            def get_raw_candidates(self, as_of_date):
                raw = self._inner.get_raw_candidates(as_of_date)
                if len(raw) <= self._cap:
                    return raw
                print(f"[cap] fetched {len(raw)} candidates; keeping first "
                      f"{self._cap} per --max-symbols")
                return raw[: self._cap]
        provider = _CappingProvider(provider, args.max_symbols)
    # Wire the sector provider so stage G's D-0048 concentration cap
    # actually enforces in production. Without this, every enriched
    # candidate carried source_reference="alpaca-iex" with no sector
    # fragment and stage G silently fell back to its "unknown sector,
    # fail-open" branch (2026-09-30 Bug #2).
    from d0026.sector_provider import load_default_sector_provider
    try:
        sector_provider = load_default_sector_provider()
    except Exception:
        sector_provider = None  # ship data/sectors.json broken -> fail open
    enricher = AlpacaFeatureEnricher(
        key_id=key, secret_key=sec,
        sector_provider=sector_provider,
    )

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
        """Progress-oriented stdout audit sink. Prints one line per
        identity resolution batch (every N) rather than one per symbol —
        for a 10k-symbol universe the per-symbol version buries the
        actual signal."""
        def __init__(self, progress_every: int = 25):
            self._progress_every = progress_every
            self._identity_count = 0
            self._rejected_count = 0
        def record(self, event):
            name = type(event).__name__
            if name == "IdentityResolutionEvent":
                self._identity_count += 1
                if (self._progress_every
                        and self._identity_count % self._progress_every == 0):
                    print(f"[progress] identity-resolved: "
                          f"{self._identity_count}", flush=True)
            elif name == "CandidateRejectedEvent":
                self._rejected_count += 1
                if (self._progress_every
                        and self._rejected_count % self._progress_every == 0):
                    print(f"[progress] rejected so far: "
                          f"{self._rejected_count}", flush=True)
            else:
                # One-off events (regime, snapshot, crash) always print.
                print(f"[audit] {name}", flush=True)

    # D-0050 Phase 1: live regime from FRED when FRED_API_KEY is set,
    # placeholder otherwise. The classifier itself fails open on any
    # error -- see src/d0026/regime_classifier.py.
    from d0026.regime_classifier import classify_regime
    from marketdata.fred_source import FredSource
    regime = classify_regime(FredSource.from_env(), effective)
    print(f"[regime] label={regime.label.value} "
          f"version={regime.classification_method_version} "
          f"values={dict(regime.reference_series_values)}")

    repo = SqliteSnapshotRepository(conn)
    pipeline = UniversePipeline(
        provider=provider,
        identity_resolver=_TickerAsIdentityResolver(),
        stage_evaluators=default_percentage_evaluators(
            UniverseSelectionConfig()
        ),
        snapshot_repository=repo,
        audit_sink=_StdoutSink(progress_every=args.progress_every),
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
