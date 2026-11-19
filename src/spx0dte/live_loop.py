"""One testable "tick" of the unattended SPX 0DTE loop (D-0095-A,
item 3 of the original open-items list). `scripts/run_spx0dte_session.py`
is the thin runner that calls `run_one_tick` in a real sleep loop;
this module holds the actual sequencing so it can be tested without a
real clock, network, or Telegram bot -- same split already used by
`TelegramDecisionSource._process_one_batch` vs `._run`.

Owns no business rule of its own: signal detection stays in `signal`,
filters stay in `entry_filters`, the day-pace cap stays in
`exit_logic.DailyPaceTracker`, concurrency admission stays in
`sizing.ConcurrentPositionTracker`, pricing stays in `live_pricing`,
persistence/notification/decisions stay in `orchestration`. This
module only decides WHEN to call each, exactly like
`orchestration.trade_proposal_service`'s own stated scope for the
equity-ladder flow.

Live VIX proxy (D-0095-A, 2026-11-18 -- market-context workstream,
priority 3 of 3): `vix_provider` (a `vix_proxy.VixProxyProvider`) is
OPTIONAL and defaults to `None`, in which case `vix_level` stays
`None` and `vix_in_tradeable_range` degrades gracefully exactly as
before ("filter skipped, not rejected") -- the same contract that
held when no VIX source existed at all. When a provider is given,
its `get_vix_level()` is called once per tick (cheap after the first
call of the day) and the comprehensive VIXY-ratio estimate is passed
through -- validated against 630 real historical samples before
being trusted (median 2.1% error); see `vix_proxy.py`'s own
docstring for the full story and why an SPY-realized-volatility
candidate was measured and rejected alongside it.

Concurrency bookkeeping uses each candidate's PLANNED exit time
(entry_time + that day's dynamic hold cap) to reserve a slot in
`ConcurrentPositionTracker` -- a real exit via target/stop/stall now
DOES free the slot earlier when it happens (see
`monitor_open_positions` below), but the slot is still reserved
pessimistically at open time since the real exit moment isn't known
in advance.

Exit/close (D-0095-A item 4, 2026-11-17): `monitor_open_positions`
re-prices every open position each tick (via `live_pricing`, the
same Option B mechanism used for entries) and feeds that estimate into
a per-position `live_exit.LiveExitTracker`. A protective exit needs
no Controller approval (`.claude/rules/01-roles-and-authority.md`) --
`orchestration.close_position` submits and records the close
directly, same tick.

Daily loss limit (D-0095-A, 2026-11-18 -- Controller-approved: whole
account, 2% of start-of-day equity, from `sizing.
DailyLossLimitTracker`): locked once per day (same "lock once" pattern
as `DailyPaceTracker`) via `broker.get_account_equity()`, right where
the day rolls over. Every real close in `monitor_open_positions` feeds
its realized dollar pnl (via `sizing.realized_pnl_dollars`, which
applies the 100x contract multiplier the rest of this module's history
did not -- see docs/trading/decisions.md D-0095-A "CORRECTION") into
the tracker. Once the day's realized losses reach the limit, NEW
opportunities are rejected (`proposals_rejected_by_loss_limit`) for
the rest of that day -- already-open positions are NOT force-closed;
they keep being managed by their own exit logic, a Controller-made
design choice, not an assumption.

Unusual-day notice (D-0095-A, 2026-11-18): the daily trade-count CAP
sweep (`spx0dte_measure_risk_sizing.py` section 4) found no profit
case for blocking a high-count day, so this is informational only,
never a rejection. The threshold is SELF-UPDATING, not a manually
typed number (the Controller explicitly asked for no manual step):
each day, `run_one_tick` queries `repo.recent_daily_counts()` for the
trailing `UNUSUAL_DAY_LOOKBACK_DAYS` real trading days and recomputes
the 90th percentile itself -- the same self-measuring pattern
`DailyPaceTracker` already uses for the hold-time cap, just sourced
from this module's own proposal history instead of SPY bars. Stays
off (`None`) until `UNUSUAL_DAY_MIN_HISTORY_DAYS` days of real
history exist.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set

from execution.broker_client import BrokerClient, BrokerClientError

from spx0dte.alpaca_client import AlpacaDataError, AlpacaOptionsClient
from spx0dte.entry_filters import run_all_filters
from spx0dte.exit_logic import ARM_PCT, STOP_PCT, TARGET_PCT, DailyPaceTracker, session_pace_width_pct
from spx0dte.live_exit import LiveExitTracker
from spx0dte.live_pricing import LivePricingUnavailableError, reprice_known_symbol
from spx0dte.orchestration import (
    build_live_proposal, close_position, create_and_notify, expire_stale_pending, process_decisions,
)
from spx0dte.ratio_calibration import RatioProvider
from spx0dte.signal import find_breakout_opportunities
from spx0dte.sizing import ConcurrentPositionTracker, DailyLossLimitTracker, percentile, realized_pnl_dollars
from spx0dte.vix_proxy import VixProxyProvider
from spx0dte.sqlite_repository import SpxPositionRepository, SpxProposalRepository
from spx0dte.telegram_approval import ControllerDecision, send_message

STALE_PROPOSAL_MAX_AGE = timedelta(minutes=30)
"""A PENDING proposal the Controller never acted on this long is
expired -- mirrors the equity-ladder flow's own supersession/expiry
discipline, applied here on every tick via expire_stale_pending."""

UNUSUAL_DAY_LOOKBACK_DAYS = 20
"""Same trailing window length as `exit_logic.PACE_LOOKBACK_DAYS`,
for the same reason: recent history only, never the whole dataset."""

UNUSUAL_DAY_MIN_HISTORY_DAYS = 5
"""Bootstrap judgment call, conservative side (Controller, 2026-11-18,
after asking for this to be fully automatic rather than a manually
typed number): below this many trailing days, a 90th percentile is
too noisy to trust, so the notice stays off (`None`) rather than
fire from a handful of points. Not a trading threshold -- this
module never rejects a trade either way."""


def _bars_to_signal_series(bars: List[dict]) -> List[dict]:
    """Alpaca's {"t": iso_str, "c": float} bar shape -> the
    {"datetime": ..., "spx": ...} shape signal.find_breakout_opportunities
    expects. "spx" here is actually the SPY proxy, matching how the
    whole D-0095 backtest and this live path both treat SPY as the
    ORB+VWAP signal's underlying series."""
    return [{"datetime": b["t"], "spx": b["c"]} for b in bars]


def _entry_key(entry) -> str:
    return f"{entry.entry_time.isoformat()}|{entry.direction}"


@dataclass
class LiveSessionState:
    """Mutable state carried across ticks by the caller (the script's
    main loop), passed into run_one_tick() each time -- kept outside
    this module's functions so they stay plain and testable."""

    pace_tracker: DailyPaceTracker = field(default_factory=DailyPaceTracker)
    position_tracker: ConcurrentPositionTracker = field(default_factory=ConcurrentPositionTracker)
    loss_limit_tracker: DailyLossLimitTracker = field(default_factory=DailyLossLimitTracker)
    current_date: Optional[str] = None
    dynamic_cap_today: Optional[float] = None
    processed_entry_keys: Set[str] = field(default_factory=set)
    exit_trackers: Dict[str, LiveExitTracker] = field(default_factory=dict)
    """proposal_id -> its LiveExitTracker. In-memory only -- a process
    restart loses each tracker's running_max/armed state, but a fresh
    tracker re-evaluates from the position's own recorded entry_price
    forward, which is a graceful (if slightly less precise)
    degradation, not a crash or a lost position."""

    last_known_price: Dict[str, float] = field(default_factory=dict)
    """proposal_id -> the latest successfully-fetched live re-priced
    estimate, updated every tick in `monitor_open_positions`. Exists
    so `build_status_snapshot` (the live dashboard's data source) can
    show a position's current price without a second network call --
    this is the exact same quote `LiveExitTracker.update()` just
    evaluated, not a separate/possibly-inconsistent read."""

    proposals_created_today: int = 0
    unusual_day_alert_sent: bool = False
    unusual_day_trade_count_threshold: Optional[float] = None
    """Controller's own suggestion (2026-11-18), after the daily
    trade-count CAP sweep (`spx0dte_measure_risk_sizing.py` section 4)
    showed no profit case for blocking a high-count day: inform
    instead of block. SELF-UPDATING, not a manually typed number
    (Controller, 2026-11-18, explicitly asked for no manual step):
    `run_one_tick` recomputes it every day from this bot's own
    `SpxProposalRepository` history (the 90th percentile of the
    trailing `UNUSUAL_DAY_LOOKBACK_DAYS` days, same self-measuring
    pattern as `exit_logic.DailyPaceTracker`). Stays `None` (feature
    off) until at least `UNUSUAL_DAY_MIN_HISTORY_DAYS` days of real
    history exist -- never guessed in the meantime. Purely
    informational either way: never rejects an opportunity, only
    sends one Telegram notice per day the first time the count is
    reached."""


@dataclass(frozen=True)
class TickResult:
    date: str
    dynamic_cap: Optional[float]
    new_opportunities_seen: int
    proposals_created: int
    proposals_rejected_by_filter: int
    proposals_rejected_by_concurrency: int
    decisions_processed: int
    orders_submitted: int
    proposals_expired: int
    proposals_rejected_by_loss_limit: int
    positions_monitored: int
    positions_closed: int
    notes: List[str]


def monitor_open_positions(
    *, now: datetime, options_client: AlpacaOptionsClient, broker: BrokerClient,
    ratio_provider: RatioProvider, position_repo: SpxPositionRepository,
    state: LiveSessionState,
) -> tuple:
    """Re-prices every open position (Option B, same mechanism as a
    new entry) and feeds the estimate into that position's
    LiveExitTracker. On an exit trigger, closes it immediately via
    `orchestration.close_position` -- no Controller approval needed
    for a protective exit. Returns (positions_monitored, positions_closed, notes)."""
    notes: List[str] = []
    closed_count = 0
    open_positions = position_repo.list_open()

    for position in open_positions:
        tracker = state.exit_trackers.get(position.proposal_id)
        if tracker is None:
            tracker = LiveExitTracker(
                entry_price=position.entry_price, entry_time=position.entry_time,
                hold_cap_minutes=position.hold_cap_minutes,
            )
            state.exit_trackers[position.proposal_id] = tracker

        try:
            quote = reprice_known_symbol(
                options_client, ratio_provider, occ_symbol=position.occ_symbol,
                strike=position.strike, direction=position.direction, now=now,
            )
        except LivePricingUnavailableError as e:
            notes.append(f"{position.proposal_id}: could not re-price for exit monitoring: {e}")
            continue

        state.last_known_price[position.proposal_id] = quote.bias_corrected_price

        reason = tracker.update(quote.bias_corrected_price, now)
        if reason is not None:
            close_position(
                position_repo, position, broker,
                exit_price=quote.bias_corrected_price, exit_reason=reason, now=now,
            )
            state.exit_trackers.pop(position.proposal_id, None)
            state.last_known_price.pop(position.proposal_id, None)
            pnl = realized_pnl_dollars(position.entry_price, quote.bias_corrected_price, position.quantity)
            state.loss_limit_tracker.record_realized_pnl(pnl)
            closed_count += 1
            notes.append(
                f"{position.proposal_id}: closed ({reason}) at {quote.bias_corrected_price:.2f}, "
                f"realized ${pnl:+.2f}"
            )

    return len(open_positions), closed_count, notes


def build_status_snapshot(
    *, now: datetime, state: LiveSessionState, position_repo: SpxPositionRepository,
) -> dict:
    """Assembles one JSON-able snapshot of everything the live
    dashboard needs: today's day-pace cap, the daily loss limit's
    real status, and each open position's live vitals (current
    price, target/stop levels derived from the SAME constants
    `LiveExitTracker` itself uses, armed state, elapsed/remaining
    time). Pure function, no I/O -- `run_one_tick` is the only
    caller that writes this to a file, so this stays trivially
    testable on its own."""
    positions = []
    for position in position_repo.list_open():
        tracker = state.exit_trackers.get(position.proposal_id)
        current_price = state.last_known_price.get(position.proposal_id)
        elapsed_minutes = max((now - position.entry_time).total_seconds() / 60.0, 0.0)
        positions.append({
            "proposal_id": position.proposal_id,
            "occ_symbol": position.occ_symbol,
            "direction": position.direction,
            "strike": position.strike,
            "quantity": position.quantity,
            "entry_time": position.entry_time.isoformat(),
            "entry_price": position.entry_price,
            "current_price": current_price,
            "target_price": position.entry_price * (1 + TARGET_PCT),
            "stop_price": position.entry_price * (1 - STOP_PCT),
            "armed_price": position.entry_price * (1 + ARM_PCT),
            "armed": tracker.armed if tracker is not None else None,
            "unrealized_pnl_dollars": (
                realized_pnl_dollars(position.entry_price, current_price, position.quantity)
                if current_price is not None else None
            ),
            "hold_cap_minutes": position.hold_cap_minutes,
            "elapsed_minutes": round(elapsed_minutes, 1),
            "remaining_minutes": round(max(position.hold_cap_minutes - elapsed_minutes, 0.0), 1),
        })

    loss_limit = state.loss_limit_tracker
    return {
        "generated_at": now.isoformat(),
        "date": state.current_date,
        "dynamic_cap_today": state.dynamic_cap_today,
        "loss_limit": {
            "locked_equity": loss_limit.locked_equity,
            "limit_pct": loss_limit.limit_pct,
            "realized_pnl_today": loss_limit.realized_pnl_today,
            "limit_reached": loss_limit.limit_reached(),
        },
        "proposals_created_today": state.proposals_created_today,
        "unusual_day_trade_count_threshold": state.unusual_day_trade_count_threshold,
        "open_positions": positions,
    }


def run_one_tick(
    *, now: datetime, options_client: AlpacaOptionsClient, broker: BrokerClient,
    ratio_provider: RatioProvider, repo: SpxProposalRepository,
    position_repo: SpxPositionRepository,
    pending_decisions: List[ControllerDecision],
    bot_token: str, chat_id: str, quantity: int,
    state: LiveSessionState,
    vix_provider: Optional[VixProxyProvider] = None,
    status_file_path: Optional[str] = None,
) -> TickResult:
    """One pass: roll the day forward if needed, detect+propose new
    opportunities, apply any pending Controller decisions (re-pricing
    live at submission), and expire stale proposals. Never raises for
    an ordinary data gap (no bars yet, no strike found) -- those are
    recorded in `TickResult.notes`; only a genuine construction error
    (bad arguments) would raise.

    `status_file_path`, if given, is OPTIONAL and additive: at the
    very end of the tick, writes `build_status_snapshot`'s JSON to
    this path -- the data source for the live dashboard
    (`scripts/spx0dte_dashboard_server.py`). A write failure (disk
    full, bad path) is caught and recorded as a note, never raised --
    this is a display convenience, not core trading logic, and must
    never be the reason a tick fails."""
    notes: List[str] = []
    today = now.strftime("%Y-%m-%d")

    try:
        spy_bars = options_client.get_spy_minute_bars(today)
    except AlpacaDataError as e:
        return TickResult(
            date=today, dynamic_cap=state.dynamic_cap_today, new_opportunities_seen=0,
            proposals_created=0, proposals_rejected_by_filter=0,
            proposals_rejected_by_concurrency=0, decisions_processed=0,
            orders_submitted=0, proposals_expired=0, proposals_rejected_by_loss_limit=0,
            positions_monitored=0, positions_closed=0, notes=[f"SPY fetch failed: {e}"],
        )

    if state.current_date != today:
        state.current_date = today
        state.dynamic_cap_today = None
        state.processed_entry_keys = set()
        state.proposals_created_today = 0
        state.unusual_day_alert_sent = False
        try:
            equity = broker.get_account_equity()
            state.loss_limit_tracker.start_new_day(today, equity)
            notes.append(
                f"daily loss limit locked for {today}: equity=${equity:,.2f}, "
                f"limit={state.loss_limit_tracker.limit_pct:.1%}"
            )
        except BrokerClientError as e:
            notes.append(f"could not fetch account equity -- daily loss limit NOT locked today: {e}")

        recent_counts = repo.recent_daily_counts(before_date=today, limit=UNUSUAL_DAY_LOOKBACK_DAYS)
        if len(recent_counts) >= UNUSUAL_DAY_MIN_HISTORY_DAYS:
            state.unusual_day_trade_count_threshold = percentile(sorted(recent_counts), 0.90)
            notes.append(
                f"unusual-day threshold self-updated for {today}: "
                f"{state.unusual_day_trade_count_threshold:.1f} "
                f"(90th percentile of the last {len(recent_counts)} trading days)"
            )
        else:
            state.unusual_day_trade_count_threshold = None
            notes.append(
                f"unusual-day notice off today: only {len(recent_counts)}/{UNUSUAL_DAY_MIN_HISTORY_DAYS} "
                "trading days of history so far"
            )

    if state.dynamic_cap_today is None and len(spy_bars) >= 15:
        pace = session_pace_width_pct(spy_bars, minutes=15)
        state.dynamic_cap_today = state.pace_tracker.record_and_get_cap(pace)
        notes.append(f"day-pace cap locked in for {today}: {state.dynamic_cap_today:.1f} min")

    proposals_created = 0
    rejected_by_filter = 0
    rejected_by_concurrency = 0
    rejected_by_loss_limit = 0
    new_opportunities_seen = 0

    vix_level = vix_provider.get_vix_level(now=now) if vix_provider is not None else None

    if state.dynamic_cap_today is not None:
        opportunities = find_breakout_opportunities(_bars_to_signal_series(spy_bars))
        for entry in opportunities:
            key = _entry_key(entry)
            if key in state.processed_entry_keys:
                continue
            new_opportunities_seen += 1
            state.processed_entry_keys.add(key)

            filter_results = run_all_filters(
                vix_level=vix_level,  # comprehensive VIXY-ratio proxy, D-0095-A 2026-11-18 -- see vix_proxy.py
                entry_time=entry.entry_time, hold_cap_minutes=state.dynamic_cap_today,
            )
            if any(not r.passed for r in filter_results):
                rejected_by_filter += 1
                continue

            if state.loss_limit_tracker.limit_reached():
                rejected_by_loss_limit += 1
                continue

            if not state.position_tracker.can_open(entry.entry_time):
                rejected_by_concurrency += 1
                continue

            try:
                proposal = build_live_proposal(
                    options_client, ratio_provider, date=today, direction=entry.direction,
                    entry_time=entry.entry_time, hold_cap_minutes=state.dynamic_cap_today,
                    vix=None, filters_passed="; ".join(r.reason for r in filter_results), now=now,
                )
            except LivePricingUnavailableError as e:
                notes.append(f"{key}: live pricing unavailable, skipped: {e}")
                continue

            create_and_notify(repo, proposal, bot_token=bot_token, chat_id=chat_id)
            planned_exit = entry.entry_time + timedelta(minutes=state.dynamic_cap_today)
            state.position_tracker.record_open(entry.entry_time, planned_exit)
            proposals_created += 1
            state.proposals_created_today += 1

            threshold = state.unusual_day_trade_count_threshold
            if (
                threshold is not None
                and not state.unusual_day_alert_sent
                and state.proposals_created_today >= threshold
            ):
                state.unusual_day_alert_sent = True
                send_message(
                    bot_token=bot_token, chat_id=chat_id,
                    text=(
                        f"Unusual day notice: {state.proposals_created_today} proposals "
                        f"today ({today}), at or above the measured threshold of "
                        f"{threshold}. Informational only -- no trade has been blocked."
                    ),
                )

    submission_results = process_decisions(
        repo, pending_decisions, broker, quantity=quantity,
        options_client=options_client, ratio_provider=ratio_provider,
        position_repo=position_repo, now=now, notes=notes,
    )

    expired = expire_stale_pending(repo, older_than=now - STALE_PROPOSAL_MAX_AGE)

    positions_monitored, positions_closed, exit_notes = monitor_open_positions(
        now=now, options_client=options_client, broker=broker, ratio_provider=ratio_provider,
        position_repo=position_repo, state=state,
    )
    notes.extend(exit_notes)

    if status_file_path is not None:
        try:
            snapshot = build_status_snapshot(now=now, state=state, position_repo=position_repo)
            with open(status_file_path, "w") as f:
                json.dump(snapshot, f, indent=2)
        except OSError as e:
            notes.append(f"could not write status snapshot to {status_file_path}: {e}")

    return TickResult(
        date=today, dynamic_cap=state.dynamic_cap_today,
        new_opportunities_seen=new_opportunities_seen,
        proposals_created=proposals_created,
        proposals_rejected_by_filter=rejected_by_filter,
        proposals_rejected_by_concurrency=rejected_by_concurrency,
        decisions_processed=len(pending_decisions),
        orders_submitted=len(submission_results),
        proposals_expired=expired,
        proposals_rejected_by_loss_limit=rejected_by_loss_limit,
        positions_monitored=positions_monitored, positions_closed=positions_closed,
        notes=notes,
    )
