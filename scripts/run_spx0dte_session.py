#!/usr/bin/env python3
"""D-0095-A unattended SPX 0DTE session runner.

Ties together everything built under D-0095/D-0095-A into a running
loop: candidate detection (signal + entry_filters) -> live pricing
(live_pricing, "Option B") -> Telegram proposal (a SEPARATE bot from
the equity-ladder flow's, see telegram_approval.py's own docstring for
why) -> Controller decision -> re-priced order submission
(AlpacaBrokerClient, unmodified) -> position tracking and automatic
protective exit (live_exit.LiveExitTracker + orchestration.close_position,
no Controller approval needed for a close, per
.claude/rules/01-roles-and-authority.md). Paper trading only -- no live
orders anywhere in this file; AlpacaBrokerClient's own paper-only
guardrail (D-0002) applies unchanged.

Each tick's actual sequencing lives in `spx0dte.live_loop.run_one_tick`,
tested independently against fakes (tests/spx0dte/test_live_loop.py).
This script is a thin runner: construct real clients from env vars,
call run_one_tick in a real sleep loop, log and notify.

KNOWN GAP, carried over from D-0095-A, not fixed by this script:
  - No live intraday VIX source -- entry_filters' VIX check runs with
    vix_level=None (its own documented "skip, don't reject" behavior).

Usage (on the Oracle VM, same .env the equity-ladder engine sources):
    cd ~/Treading-bot-Claude && set -a && source .env && set +a && \\
    PYTHONPATH=src python3.11 scripts/run_spx0dte_session.py --max-hours 7

Env vars required:
    ALPACA_API_KEY_ID, ALPACA_API_SECRET_KEY, ALPACA_BASE_URL (paper)
    FRED_API_KEY                               (ratio calibration, VIX proxy anchor)
    SPX_TELEGRAM_BOT_TOKEN, SPX_TELEGRAM_ADMIN_USER_IDS
    SPX_TELEGRAM_CHAT_ID                       (where proposals are sent)
"""
from __future__ import annotations

import argparse
import fcntl
import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_REPO_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from execution.alpaca_broker_client import AlpacaBrokerClient  # noqa: E402

from spx0dte.alpaca_client import AlpacaOptionsClient  # noqa: E402
from spx0dte.live_loop import LiveSessionState, run_one_tick  # noqa: E402
from spx0dte.ratio_calibration import RatioProvider  # noqa: E402
from spx0dte.sizing import CONTRACTS_PER_TRADE  # noqa: E402
from spx0dte.sqlite_repository import SpxPositionRepository, SpxProposalRepository  # noqa: E402
from spx0dte.telegram_approval import SpxTelegramDecisionSource, send_message  # noqa: E402

from spx0dte_dashboard_server import start_server_in_background  # noqa: E402 -- same scripts/ directory
from spx0dte.vix_proxy import VixProxyProvider  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("spx0dte_session")

_stop_requested = False


def _handle_stop_signal(signum, frame) -> None:
    global _stop_requested
    _stop_requested = True
    logger.info("Received signal %s -- will stop after the current tick.", signum)


_LOCK_FILE_PATH = os.path.join(_REPO_ROOT, "spx0dte_session.lock")
_lock_file_handle = None  # module-level so the held flock survives for the process's whole life


def _acquire_single_instance_lock() -> None:
    """2026-11-22 fix (P-068/P-070 session's own real incident, 2026-11-19:
    two run_spx0dte_session.py processes were found running concurrently
    against the same spx0dte.sqlite and the same broker account, caught
    only by a human running `ps aux`). A non-blocking exclusive flock on a
    dedicated lock file refuses to let a second instance start at all,
    rather than relying on orchestration.py's sequential (non-atomic
    across processes) same-symbol/cap checks to catch it after the fact.
    The lock is released automatically when this process exits (or is
    killed), including by the OS if the process dies without a clean
    shutdown -- flock locks do not outlive their holding process."""
    global _lock_file_handle
    _lock_file_handle = open(_LOCK_FILE_PATH, "w")
    try:
        fcntl.flock(_lock_file_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(
            f"FATAL: another run_spx0dte_session.py is already running "
            f"(could not acquire {_LOCK_FILE_PATH}) -- refusing to start a second instance.",
            file=sys.stderr,
        )
        sys.exit(75)  # EX_TEMPFAIL -- a real other instance holds this, not a config error
    _lock_file_handle.write(str(os.getpid()))
    _lock_file_handle.flush()


def _required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        print(f"FATAL: {name} is not set -- did you `source .env`?", file=sys.stderr)
        sys.exit(78)  # EX_CONFIG, matching engine-run.sh's own convention
    return value


def main() -> int:
    _acquire_single_instance_lock()

    parser = argparse.ArgumentParser(description="D-0095-A SPX 0DTE session runner")
    parser.add_argument("--max-hours", type=float, default=7.0,
                         help="session time cap (a US trading day is ~6.5h)")
    parser.add_argument("--tick-seconds", type=float, default=60.0,
                         help="seconds between loop ticks")
    parser.add_argument("--quantity", type=int, default=CONTRACTS_PER_TRADE,
                         help="contracts per approved entry (default: the approved fixed size)")
    parser.add_argument("--db-path", default="spx0dte.sqlite")
    parser.add_argument(
        "--status-file", default=None,
        help=(
            "Optional path to write a live JSON status snapshot after every tick, for "
            "the live dashboard to serve. Omit to skip writing one entirely."
        ),
    )
    parser.add_argument(
        "--dashboard-port", type=int, default=None,
        help=(
            "If given (along with --status-file), starts the live dashboard server "
            "(scripts/spx0dte_dashboard_server.py) INSIDE this same process, on this port -- "
            "Controller's own request, 2026-11-19: one command instead of two. Omit to run no "
            "dashboard at all."
        ),
    )
    parser.add_argument(
        "--public-host", default=None,
        help=(
            "This server's own public address (e.g. an IP or domain), used ONLY to build a "
            "clickable dashboard link sent in the Telegram startup message. Not auto-detected -- "
            "the Controller provides it explicitly (no new external dependency, no guessing an "
            "address that could be wrong). Omit to skip sending a link."
        ),
    )
    args = parser.parse_args()

    key_id = _required_env("ALPACA_API_KEY_ID")
    secret_key = _required_env("ALPACA_API_SECRET_KEY")
    base_url = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    fred_key = _required_env("FRED_API_KEY")
    chat_id = _required_env("SPX_TELEGRAM_CHAT_ID")

    broker = AlpacaBrokerClient(base_url=base_url, key_id=key_id, secret_key=secret_key)
    options_client = AlpacaOptionsClient(key_id=key_id, secret_key=secret_key)
    ratio_provider = RatioProvider(client=options_client, fred_api_key=fred_key)
    vix_provider = VixProxyProvider(client=options_client, fred_api_key=fred_key)
    repo = SpxProposalRepository(args.db_path)
    position_repo = SpxPositionRepository(args.db_path)  # same file, shared schema

    decision_source = SpxTelegramDecisionSource.from_env()

    signal.signal(signal.SIGINT, _handle_stop_signal)
    signal.signal(signal.SIGTERM, _handle_stop_signal)

    decision_source.start()
    state = LiveSessionState()  # unusual-day threshold self-updates from repo history each day -- see live_loop.py

    if args.dashboard_port is not None and args.status_file is not None:
        # `state` is passed through so the dashboard's "Sell Now" button can enqueue
        # a manual_close_requests entry that this same loop's next tick acts on.
        start_server_in_background(status_file_path=args.status_file, port=args.dashboard_port, state=state)
        logger.info("Dashboard server started on port %d (status file: %s).", args.dashboard_port, args.status_file)

    bot_token = os.environ.get("SPX_TELEGRAM_BOT_TOKEN", "")
    startup_text = (
        f"SPX 0DTE session starting (max {args.max_hours}h, "
        f"tick every {args.tick_seconds}s, quantity {args.quantity}/entry)."
    )
    if args.public_host is not None and args.dashboard_port is not None:
        startup_text += f"\nLive dashboard: http://{args.public_host}:{args.dashboard_port}/"
    send_message(bot_token=bot_token, chat_id=chat_id, text=startup_text)

    start_time = datetime.now(timezone.utc)
    deadline = start_time + timedelta(hours=args.max_hours)
    stop_reason = "max-hours reached"

    try:
        while True:
            if _stop_requested:
                stop_reason = "signal received"
                break
            now = datetime.now(timezone.utc)
            if now >= deadline:
                break

            if not broker.is_market_open():
                logger.info("Market closed -- sleeping %.0fs.", args.tick_seconds)
                time.sleep(args.tick_seconds)
                continue

            pending_decisions = decision_source.poll()
            try:
                result = run_one_tick(
                    now=now, options_client=options_client, broker=broker,
                    ratio_provider=ratio_provider, repo=repo, position_repo=position_repo,
                    pending_decisions=pending_decisions, bot_token=bot_token,
                    chat_id=chat_id, quantity=args.quantity, state=state,
                    vix_provider=vix_provider, status_file_path=args.status_file,
                )
                logger.info(
                    "tick: cap=%s new=%d created=%d filter_rej=%d conc_rej=%d loss_limit_rej=%d "
                    "decisions=%d submitted=%d expired=%d monitored=%d closed=%d notes=%s",
                    result.dynamic_cap, result.new_opportunities_seen, result.proposals_created,
                    result.proposals_rejected_by_filter, result.proposals_rejected_by_concurrency,
                    result.proposals_rejected_by_loss_limit,
                    result.decisions_processed, result.orders_submitted, result.proposals_expired,
                    result.positions_monitored, result.positions_closed, result.notes,
                )
            except Exception as exc:  # noqa: BLE001 -- a tick must never kill the loop silently
                logger.exception("tick failed: %s", exc)
                send_message(bot_token=bot_token, chat_id=chat_id, text=f"SPX 0DTE tick error: {exc}")

            time.sleep(args.tick_seconds)
    finally:
        decision_source.stop()
        send_message(bot_token=bot_token, chat_id=chat_id, text=f"SPX 0DTE session stopping ({stop_reason}).")

    logger.info("Session stopped: %s", stop_reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
