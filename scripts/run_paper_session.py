#!/usr/bin/env python3
"""D-0045 paper-session runner.

Starts the Engine against the paper Alpaca account with a static
Controller-approved watchlist, Telegram notifications, and Telegram
long-polling for approval decisions. Paper trading only. No live
orders anywhere in this file.

Design gates (all approved under D-0045, 2026-09-27):
  1. Approved watchlist. Default `--symbols` is `TSLA,AAPL,SPY` — the
     full Controller-approved test set from D-0045. `_check_watchlist`
     iterates the symbols sequentially in one trigger check, so the
     three Initial Entry proposals arrive as an ordered burst (a few
     hundred milliseconds apart) with per-proposal ids and per-symbol
     Approve/Reject buttons, never a race.
  2. Time-bounded run. `--max-hours` (default 6) caps the session so a
     forgotten process cannot outlive the cloud container's window.
  3. Pre-flight before entering the loop: Alpaca /v2/account,
     Alpaca /v2/clock, Telegram getMe, sent as one Telegram summary,
     followed by a grace period (default 30s) before the Engine
     actually starts. Controller can Ctrl+C during the grace period
     to abort a bad launch without an Initial Entry proposal ever
     being created.

Usage:
    PYTHONPATH=src python3 scripts/run_paper_session.py \
        --symbols TSLA --max-hours 6

Env vars required (per D-0038; container-level):
    ALPACA_API_KEY_ID
    ALPACA_API_SECRET_KEY
    ALPACA_BASE_URL          (must be https://paper-api.alpaca.markets)
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID
    TELEGRAM_ADMIN_USER_IDS

Env vars optional:
    PAPER_SESSION_DB_PATH    (default: ./paper_session.sqlite)
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Optional


_here = os.path.abspath(os.path.dirname(__file__))
_repo_root = os.path.abspath(os.path.join(_here, ".."))
_src = os.path.join(_repo_root, "src")
if _src not in sys.path:
    sys.path.insert(0, _src)


def _fail(msg: str) -> "SystemExit":
    print(f"[FAIL] {msg}", file=sys.stderr)
    return SystemExit(1)


def _repo_root_from_db_path(db_path: str) -> str:
    """The repo root for the git persister. Defaults to the parent dir
    of the DB file; if that is not inside a git working tree, fall back
    to the script's repo root -- caller then controls the semantics via
    PAPER_SESSION_DB_PATH."""
    return os.path.abspath(os.path.dirname(os.path.abspath(db_path)) or _repo_root)


def _make_db_persister(*, repo_root: str, db_filename: str):
    """Returns a Callable[[datetime], None] that commits + pushes ONLY
    `db_filename` from `repo_root` to the current branch's upstream.

    Guarantees (Controller-approved 2026-10-01):
      - commits only the DB file -- never anything else that may be
        dirty in the working tree.
      - no-op commit if `git diff --quiet` says the DB is unchanged
        since HEAD.
      - never crashes the engine on failure: raises so the Engine's
        _persist_db_best_effort shield can surface a CRITICAL
        notification and keep the main loop alive.
    """
    import subprocess
    from datetime import datetime

    def _run(cmd, check=True, capture_output=False):
        return subprocess.run(
            cmd, cwd=repo_root, check=check,
            capture_output=capture_output, text=True, timeout=60,
        )

    def _persist(now: datetime) -> None:
        # Fast exit when nothing changed (common case: a tick that
        # neither reconciled nor applied any decision).
        diff = _run(
            ["git", "diff", "--quiet", "--", db_filename], check=False,
        )
        staged = _run(
            ["git", "diff", "--cached", "--quiet", "--", db_filename],
            check=False,
        )
        if diff.returncode == 0 and staged.returncode == 0:
            return  # DB has not changed since HEAD -- nothing to commit

        _run(["git", "add", db_filename])
        # Commit only the DB file. If somehow nothing is staged after
        # `git add` (concurrent reset?), skip to avoid an empty commit.
        status = _run(
            ["git", "diff", "--cached", "--quiet", "--", db_filename],
            check=False,
        )
        if status.returncode == 0:
            return
        msg = (
            f"chore(db): engine tick {now.isoformat()}\n\n"
            f"Automated per-tick DB persistence (Controller-approved "
            f"2026-10-01).\n"
        )
        _run(["git", "commit", "--only", db_filename, "-m", msg])
        _run(["git", "push"])

    return _persist


def _require_env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise _fail(f"env var {name} is missing or empty")
    return v


def _alpaca_get(path: str, *, key_id: str, secret: str, base_url: str,
                timeout: float = 10.0) -> dict:
    url = base_url.rstrip("/") + path
    req = urllib.request.Request(
        url,
        headers={
            "APCA-API-KEY-ID": key_id,
            "APCA-API-SECRET-KEY": secret,
            "Accept": "application/json",
        },
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _telegram_get_me(bot_token: str, timeout: float = 10.0) -> dict:
    url = f"https://api.telegram.org/bot{bot_token}/getMe"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _preflight(*, key_id: str, secret: str, base_url: str,
               bot_token: str) -> dict:
    print("=" * 72)
    print("Pre-flight checks")
    print("=" * 72)

    account = _alpaca_get("/v2/account", key_id=key_id, secret=secret,
                          base_url=base_url)
    cash = float(account.get("cash", 0.0))
    equity = float(account.get("equity", 0.0))
    status = account.get("status", "UNKNOWN")
    print(f"[OK] Alpaca /v2/account status={status}, cash={cash:.2f} USD, "
          f"equity={equity:.2f} USD")

    clock = _alpaca_get("/v2/clock", key_id=key_id, secret=secret,
                        base_url=base_url)
    is_open = bool(clock.get("is_open"))
    next_open = clock.get("next_open", "?")
    next_close = clock.get("next_close", "?")
    print(f"[OK] Alpaca /v2/clock is_open={is_open}, "
          f"next_open={next_open}, next_close={next_close}")

    me = _telegram_get_me(bot_token)
    if not me.get("ok"):
        raise _fail(f"Telegram getMe returned not-ok: {me!r}")
    bot_username = me.get("result", {}).get("username", "?")
    print(f"[OK] Telegram bot reachable, username=@{bot_username}")

    return {
        "cash": cash,
        "equity": equity,
        "is_open": is_open,
        "next_open": next_open,
        "next_close": next_close,
        "bot_username": bot_username,
    }


def _build_composite_enricher(*, enable_research: bool):
    """Builds the DeepResearchComposer (D-0050 Phase 9) from every
    available env-var API key. Each source is best-effort: a missing
    key or a failed fetch means that section stays blank, the rest of
    the report is still produced.
    """
    if not enable_research:
        return None

    from engine.deep_research import DeepResearchComposer
    attached: list = []

    px_client = None
    try:
        from research.perplexity_agent import PerplexityAgentClient
        key = os.environ.get("PERPLEXITY_API_KEY", "").strip()
        if key:
            px_client = PerplexityAgentClient(api_key=key)
            attached.append("Perplexity")
    except Exception as exc:  # noqa: BLE001
        print(f"[research] Perplexity init failed: {exc}")

    fh_client = None
    try:
        from marketdata.finnhub_source import FinnhubSource
        fh_client = FinnhubSource.from_env()
        if fh_client is not None:
            attached.append("Finnhub")
    except Exception as exc:  # noqa: BLE001
        print(f"[research] Finnhub init failed: {exc}")

    av_client = None
    try:
        from marketdata.alpha_vantage_source import AlphaVantageSource
        av_client = AlphaVantageSource.from_env()
        if av_client is not None:
            attached.append("AlphaVantage")
    except Exception as exc:  # noqa: BLE001
        print(f"[research] AlphaVantage init failed: {exc}")

    tn_client = None
    try:
        from marketdata.tiingo_source import TiingoSource
        tn_client = TiingoSource.from_env()
        if tn_client is not None:
            attached.append("Tiingo")
    except Exception as exc:  # noqa: BLE001
        print(f"[research] Tiingo init failed: {exc}")

    pg_client = None
    try:
        from marketdata.polygon_source import PolygonSource
        pg_client = PolygonSource.from_env()
        if pg_client is not None:
            attached.append("Polygon")
    except Exception as exc:  # noqa: BLE001
        print(f"[research] Polygon init failed: {exc}")

    fred_client = None
    try:
        from marketdata.fred_source import FredSource
        fred_client = FredSource.from_env()
        if fred_client is not None:
            attached.append("FRED")
    except Exception as exc:  # noqa: BLE001
        print(f"[research] FRED init failed: {exc}")

    try:
        from marketdata.polygon_source import PolygonS3Config
        s3cfg = PolygonS3Config.from_env()
        if s3cfg is not None:
            s3cfg.build_client()  # validates auth config
            print(f"[research] Polygon S3 bulk client READY: "
                  f"endpoint={s3cfg.endpoint}  "
                  f"(use scripts/download_polygon_bulk.py)")
    except Exception as exc:  # noqa: BLE001
        print(f"[research] Polygon S3 init failed: {exc}")

    if not attached:
        print("[research] NO sources could be built -- disabled")
        return None

    print(f"[research] deep research ENABLED: {', '.join(attached)}")
    return DeepResearchComposer(
        fred=fred_client,
        finnhub=fh_client,
        alpha_vantage=av_client,
        polygon=pg_client,
        tiingo=tn_client,
        perplexity=px_client,
    )


def _build_trade_evaluator(*, enable_research: bool):
    """Builds the TradeEvaluator with a hub tied to the same env-var
    API clients the composite enricher uses. Returns None when
    research is disabled or no sources are configured."""
    if not enable_research:
        return None
    try:
        from engine.research_hub import SymbolResearchHub
        from engine.trade_evaluator import TradeEvaluator
    except Exception as exc:  # noqa: BLE001
        print(f"[evaluator] import failed: {exc}")
        return None

    px = None
    try:
        from research.perplexity_agent import PerplexityAgentClient
        k = os.environ.get("PERPLEXITY_API_KEY", "").strip()
        if k:
            px = PerplexityAgentClient(api_key=k)
    except Exception:  # noqa: BLE001
        pass

    try:
        from marketdata.fred_source import FredSource
        from marketdata.finnhub_source import FinnhubSource
        from marketdata.alpha_vantage_source import AlphaVantageSource
        from marketdata.polygon_source import PolygonSource
        from marketdata.tiingo_source import TiingoSource
        hub = SymbolResearchHub(
            fred=FredSource.from_env(),
            finnhub=FinnhubSource.from_env(),
            alpha_vantage=AlphaVantageSource.from_env(),
            polygon=PolygonSource.from_env(),
            tiingo=TiingoSource.from_env(),
            perplexity=px,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[evaluator] hub build failed: {exc}")
        return None

    # Any source configured? If none, the evaluator would reject
    # everything on "no live price" -- better to leave it off.
    if not any([hub._fred, hub._fh, hub._av, hub._pg, hub._tn, hub._px]):
        print("[evaluator] no sources configured -- disabled")
        return None

    print("[evaluator] TradeEvaluator ENABLED "
          "(Top-3 per cycle, min score 60/100)")
    return TradeEvaluator(hub)


def _install_signal_handlers(stop_flag: list) -> None:
    def _handler(signum, _frame):
        print(f"[signal] received {signum}, requesting shutdown", flush=True)
        stop_flag.append(True)
    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


def main() -> int:
    parser = argparse.ArgumentParser(description="D-0045 paper-session runner")
    parser.add_argument("--symbols", default="TSLA,AAPL,SPY",
                        help="Comma-separated watchlist "
                             "(default: TSLA,AAPL,SPY per D-0045)")
    parser.add_argument("--max-hours", type=float, default=6.0,
                        help="Hard wall-clock cap on session length (default: 6)")
    parser.add_argument("--reconcile-seconds", type=float, default=30.0,
                        help="Engine reconciliation interval (default: 30s)")
    parser.add_argument("--db-path",
                        default=os.environ.get("PAPER_SESSION_DB_PATH",
                                               "./paper_session.sqlite"))
    parser.add_argument("--grace-seconds", type=float, default=30.0,
                        help="Delay between preflight and engine start "
                             "so Controller can Ctrl+C to abort (default: 30)")
    parser.add_argument("--skip-confirm", action="store_true",
                        help="Skip the grace-period wait (unsafe; only for "
                             "scripted tests).")
    parser.add_argument("--universe-mode", choices=("static", "snapshot"),
                        default="static",
                        help="static=StaticWatchlistSource (from --symbols); "
                             "snapshot=SnapshotUniverseSource reading today's "
                             "D-0026 snapshot from SQLite (B22).")
    parser.add_argument("--enable-perplexity", action="store_true",
                        help="(deprecated alias of --enable-research; kept "
                             "for backwards compatibility).")
    parser.add_argument("--enable-research", action="store_true",
                        help="D-0050: attach composite research enrichment "
                             "(Perplexity + Finnhub + Alpha Vantage + Tiingo "
                             "+ Polygon) to every new proposal notification. "
                             "Each sub-source is independent; a missing API "
                             "key or a failed fetch is silently skipped. "
                             "Strictly advisory -- never blocks a proposal.")
    parser.add_argument("--no-db-push", action="store_true",
                        help="Disable the per-tick git commit+push of "
                             "paper_session.sqlite. Local SQLite persistence "
                             "is unaffected -- the DB is still written to "
                             "disk continuously. Use on hosts with persistent "
                             "disk (e.g. a dedicated VM) where state does not "
                             "need external backup via GitHub. The original "
                             "push behavior remains the default for cloud "
                             "containers that get reclaimed on inactivity.")
    args = parser.parse_args()

    key_id = _require_env("ALPACA_API_KEY_ID")
    secret = _require_env("ALPACA_API_SECRET_KEY")
    base_url = _require_env("ALPACA_BASE_URL")
    bot_token = _require_env("TELEGRAM_BOT_TOKEN")
    chat_id = _require_env("TELEGRAM_CHAT_ID")
    admin_ids_raw = _require_env("TELEGRAM_ADMIN_USER_IDS")

    symbols = tuple(s.strip().upper() for s in args.symbols.split(",")
                    if s.strip())
    if not symbols:
        raise _fail("--symbols resolved to an empty list")

    print(f"Session config: symbols={symbols}, max_hours={args.max_hours}, "
          f"reconcile_seconds={args.reconcile_seconds}, db={args.db_path}")

    facts = _preflight(key_id=key_id, secret=secret, base_url=base_url,
                       bot_token=bot_token)

    # Wire concrete adapters and services.
    from persistence.db import connect, bootstrap_schema, bootstrap_lock_only_schema
    from trade.sqlite_repository import SqliteTradeRepository
    from proposals.sqlite_repository import SqliteProposalRepository
    from execution.sqlite_repository import SqliteOrderExecutionRepository
    from execution.alpaca_broker_client import AlpacaBrokerClient
    from execution.service import ExecutionService
    from marketdata.alpaca_source import AlpacaMarketDataSource
    from notifications.telegram import TelegramNotificationService
    from notifications.telegram_decision import TelegramDecisionSource
    from notifications.service import NotificationEvent, NotificationLevel
    from orchestration.trade_proposal_service import TradeProposalService
    from engine.engine import Engine
    from engine.lock import EngineLock
    from engine.watchlist import StaticWatchlistSource

    # Two SQLite files, by design (Controller-approved 2026-10-01):
    #   1. paper_session.sqlite -- durable trading state (trades,
    #      proposals, executions, universe snapshots, research). This
    #      file IS committed+pushed to the branch by the per-tick
    #      persister when it changes, so a fresh cloud container can
    #      rehydrate yesterday's open positions on startup.
    #   2. engine_lock.sqlite  -- session-ephemeral liveness beacon.
    #      Writes happen several times per tick (heartbeat); this file
    #      is gitignored so those writes never produce commits. On a
    #      container reclaim the lock is naturally gone (fresh clone =
    #      fresh file), which is exactly what the stale-lock-steal path
    #      already handles.
    conn = connect(args.db_path)
    bootstrap_schema(conn)

    lock_db_path = os.path.join(
        os.path.dirname(os.path.abspath(args.db_path)),
        "engine_lock.sqlite",
    )
    lock_conn = connect(lock_db_path)
    bootstrap_lock_only_schema(lock_conn)

    trade_repo = SqliteTradeRepository(conn)
    proposal_repo = SqliteProposalRepository(conn)
    execution_repo = SqliteOrderExecutionRepository(conn)

    # Shared HTTP retry policy for every Alpaca client. Without this
    # wiring B26's retry code was dormant in production and any 429 /
    # 5xx blip from Alpaca would fail an approval / risk check on the
    # first try (Bug #1 from the 2026-09-30 audit).
    from common.http_retry import RetryPolicy
    retry_policy = RetryPolicy(max_attempts=3, base_backoff_seconds=1.0)

    broker = AlpacaBrokerClient(
        base_url=base_url, key_id=key_id, secret_key=secret,
        timeout_seconds=15.0, retry_policy=retry_policy,
    )
    market_data = AlpacaMarketDataSource(
        key_id=key_id, secret_key=secret, timeout_seconds=10.0,
        retry_policy=retry_policy,
    )
    notifier = TelegramNotificationService(
        bot_token=bot_token, chat_id=chat_id,
    )
    decision_source = TelegramDecisionSource.from_env()

    trade_proposal_service = TradeProposalService(trade_repo, proposal_repo)

    # D-0047 portfolio risk enforcer: wired for real paper sessions.
    from risk.enforcer import PortfolioRiskEnforcer
    from risk.models import PortfolioRiskLimits
    from risk.portfolio_snapshot import LivePortfolioSnapshotBuilder
    snapshot_builder = LivePortfolioSnapshotBuilder(
        broker_base_url=base_url, broker_key_id=key_id,
        broker_secret_key=secret, sqlite_conn=conn,
        retry_policy=retry_policy,
    )
    risk_enforcer = PortfolioRiskEnforcer(
        limits=PortfolioRiskLimits(),  # D-0047 defaults
        snapshot_builder=snapshot_builder,
    )
    execution_service = ExecutionService(execution_repo, proposal_repo,
                                         trade_repo, broker,
                                         risk_enforcer=risk_enforcer)
    if args.universe_mode == "snapshot":
        from d0026.sqlite_repository import SqliteSnapshotRepository
        from engine.snapshot_watchlist import SnapshotUniverseSource
        snapshot_repo = SqliteSnapshotRepository(conn)
        watchlist = SnapshotUniverseSource(
            snapshot_repo, fallback_watchlist=symbols,
        )
    else:
        watchlist = StaticWatchlistSource(symbols)
    lock = EngineLock(lock_conn)

    # Controller-approved 2026-10-01: at the end of every tick, commit
    # paper_session.sqlite (and ONLY that file) to the branch and push,
    # so a cloud-container reclaim mid-session does not lose state. The
    # callback is best-effort; a git failure is a CRITICAL notification
    # and the next tick retries.
    if args.no_db_push:
        db_persister = None
        print("[db] git push DISABLED by --no-db-push; SQLite still persists "
              "to disk every tick (that behavior is intrinsic to the DB "
              "writes, not to this callback). The engine will not emit "
              "'db_persist_failed' CRITICAL notifications.")
    else:
        db_persister = _make_db_persister(
            repo_root=_repo_root_from_db_path(args.db_path),
            db_filename=os.path.basename(os.path.abspath(args.db_path)),
        )

    # D-0050 Phase 7: composite research enrichment on proposal
    # notifications. Each sub-enricher is attempted independently; a
    # missing API key or a failed fetch is silently skipped, so the
    # resulting Telegram message carries only what succeeded. All
    # strictly advisory per CLAUDE.md §5 -- never gates approval,
    # never changes trading state.
    proposal_enricher = _build_composite_enricher(
        enable_research=getattr(args, "enable_research", False)
                        or getattr(args, "enable_perplexity", False),
    )

    # D-0050 Phase 10: TradeEvaluator ranks each cycle's watchlist
    # candidates by live research. Reuses the same clients the
    # composite enricher already built.
    trade_evaluator = _build_trade_evaluator(
        enable_research=getattr(args, "enable_research", False)
                        or getattr(args, "enable_perplexity", False),
    )

    # D-0050 Phase B.27: political universe source (QuiverQuant +
    # CapitolTrades scrapers for the whitelist + Senate/House
    # eDisclosure index). ON when at least QuiverQuant is configured.
    political_universe = None
    if trade_evaluator is not None:
        try:
            from marketdata.quiverquant_source import QuiverQuantSource
            from research.political_aggregator import PoliticalAggregator
            from research.edisclosure_source import EDisclosureSource
            from engine.political_universe_source import PoliticalUniverseSource
            qq = QuiverQuantSource.from_env()
            ed = EDisclosureSource()
            agg = PoliticalAggregator(quiverquant=qq, edisclosure=ed)
            political_universe = PoliticalUniverseSource(agg)
            attached = "QuiverQuant" if qq else "eDisclosure-only"
            print(f"[political] universe source ENABLED ({attached})")
        except Exception as exc:  # noqa: BLE001
            print(f"[political] init failed: {exc}")

    # D-0050 Phase 14 + 16: portfolio filter + macro-event calendar.
    # Always ON once research is enabled; both fail-open internally.
    portfolio_filter = None
    macro_calendar = None
    if trade_evaluator is not None:
        try:
            from engine.portfolio_filter import PortfolioFilter
            from marketdata.polygon_source import PolygonSource
            pg = PolygonSource.from_env()
            if pg is not None:
                from datetime import date as _date, timedelta as _td
                def _closes_fn(sym):
                    today = _date.today()
                    bars = pg.get_aggregates(sym, 1, "day",
                                              today - _td(days=50), today,
                                              adjusted=True)
                    return [(d, b.get("c")) for d, b in bars
                            if isinstance(b, dict) and b.get("c") is not None]
                portfolio_filter = PortfolioFilter(closes_provider=_closes_fn)
                print("[portfolio] sector + correlation filter ENABLED")
        except Exception as exc:  # noqa: BLE001
            print(f"[portfolio] init failed, continuing without it: {exc}")
        try:
            from engine.macro_calendar import MacroEventCalendar
            macro_calendar = MacroEventCalendar()
            print("[macro] FOMC/CPI/NFP blackout calendar ENABLED")
        except Exception as exc:  # noqa: BLE001
            print(f"[macro] init failed: {exc}")

    engine = Engine(
        trade_repo=trade_repo,
        proposal_repo=proposal_repo,
        execution_repo=execution_repo,
        trade_proposal_service=trade_proposal_service,
        execution_service=execution_service,
        market_data=market_data,
        watchlist=watchlist,
        decision_source=decision_source,
        notifier=notifier,
        lock=lock,
        db_persister=db_persister,
        proposal_enricher=proposal_enricher,
        trade_evaluator=trade_evaluator,
        portfolio_filter=portfolio_filter,
        macro_calendar=macro_calendar,
        political_universe_source=political_universe,
    )

    # Preflight Telegram summary (before engine.start(), so still safe).
    summary_lines = [
        "[PREFLIGHT] paper session ready",
        f"symbols: {', '.join(symbols)}",
        f"cash: {facts['cash']:.2f} USD",
        f"equity: {facts['equity']:.2f} USD",
        f"market_open: {facts['is_open']}",
        f"next_open: {facts['next_open']}",
        f"next_close: {facts['next_close']}",
        f"bot: @{facts['bot_username']}",
        f"max_hours: {args.max_hours}",
    ]
    if not args.skip_confirm:
        summary_lines.append(
            f"Engine will start in {args.grace_seconds:.0f}s. "
            "Ctrl+C locally to abort."
        )
    notifier.send(NotificationEvent(
        level=NotificationLevel.IMPORTANT,
        event="paper_session_preflight",
        message="\n".join(summary_lines),
        symbol=None,
        extra=(),
    ))

    if not args.skip_confirm and args.grace_seconds > 0:
        print(f"Grace period: {args.grace_seconds:.0f}s. "
              f"Ctrl+C to abort before Engine starts.")
        time.sleep(args.grace_seconds)

    stop_flag: list = []
    _install_signal_handlers(stop_flag)

    decision_source.start()
    try:
        now = datetime.now(timezone.utc)
        engine.start(now=now)
        notifier.send(NotificationEvent(
            level=NotificationLevel.IMPORTANT,
            event="paper_session_started",
            message=f"Engine live for symbols {list(symbols)}.",
            symbol=None,
            extra=(),
        ))

        deadline = time.monotonic() + args.max_hours * 3600.0

        def now_fn() -> datetime:
            return datetime.now(timezone.utc)

        def sleep_fn(seconds: float) -> None:
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                if stop_flag or time.monotonic() >= deadline:
                    return
                time.sleep(min(1.0, end - time.monotonic()))

        # Cooperative loop instead of raw run_forever, so both the
        # deadline and SIGINT/SIGTERM can end the run cleanly.
        from engine.schedule import is_d0021_check_time
        while not stop_flag and time.monotonic() < deadline:
            now = now_fn()
            engine.run_reconciliation_tick(now=now)
            if is_d0021_check_time(now):
                engine.run_trigger_check(now=now)
            sleep_fn(args.reconcile_seconds)

        reason = ("SIGINT/SIGTERM received" if stop_flag
                  else f"max-hours cap ({args.max_hours}h) reached")
        print(f"[shutdown] {reason}")
        notifier.send(NotificationEvent(
            level=NotificationLevel.IMPORTANT,
            event="paper_session_ended",
            message=f"Engine shutting down: {reason}",
            symbol=None,
            extra=(),
        ))
    finally:
        try:
            engine.shutdown()
        except Exception as ex:  # noqa: BLE001
            print(f"[shutdown-warning] engine.shutdown raised: {ex!r}")
        try:
            decision_source.stop()
        except Exception as ex:  # noqa: BLE001
            print(f"[shutdown-warning] decision_source.stop raised: {ex!r}")
        try:
            conn.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
