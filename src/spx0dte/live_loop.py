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

KNOWN GAP, not silently assumed away: there is no live intraday VIX
source wired in yet (Alpaca serves no index data at all, and FRED's
VIXCLS series updates once daily like SP500 -- no use intraday).
`entry_filters.vix_in_tradeable_range` already degrades gracefully
when given `vix_level=None` ("filter skipped, not rejected") -- this
loop passes `None` today, which means that ONE filter is effectively
off until a live VIX source is found and wired in. Recorded here, not
hidden.

Concurrency bookkeeping uses each candidate's PLANNED exit time
(entry_time + that day's dynamic hold cap) to reserve a slot in
`ConcurrentPositionTracker`, since no automated exit/close exists yet
(a separate, explicitly deferred item) -- a real exit via target/
stop/stall would free the slot earlier than this, making the
bookkeeping conservative (slightly UNDER-admits), never permissive.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional, Set

from execution.broker_client import BrokerClient

from spx0dte.alpaca_client import AlpacaDataError, AlpacaOptionsClient
from spx0dte.entry_filters import run_all_filters
from spx0dte.exit_logic import DailyPaceTracker, session_pace_width_pct
from spx0dte.live_pricing import LivePricingUnavailableError
from spx0dte.orchestration import build_live_proposal, create_and_notify, expire_stale_pending, process_decisions
from spx0dte.ratio_calibration import RatioProvider
from spx0dte.signal import find_breakout_opportunities
from spx0dte.sizing import ConcurrentPositionTracker
from spx0dte.sqlite_repository import SpxProposalRepository
from spx0dte.telegram_approval import ControllerDecision

STALE_PROPOSAL_MAX_AGE = timedelta(minutes=30)
"""A PENDING proposal the Controller never acted on this long is
expired -- mirrors the equity-ladder flow's own supersession/expiry
discipline, applied here on every tick via expire_stale_pending."""


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
    current_date: Optional[str] = None
    dynamic_cap_today: Optional[float] = None
    processed_entry_keys: Set[str] = field(default_factory=set)


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
    notes: List[str]


def run_one_tick(
    *, now: datetime, options_client: AlpacaOptionsClient, broker: BrokerClient,
    ratio_provider: RatioProvider, repo: SpxProposalRepository,
    pending_decisions: List[ControllerDecision],
    bot_token: str, chat_id: str, quantity: int,
    state: LiveSessionState,
) -> TickResult:
    """One pass: roll the day forward if needed, detect+propose new
    opportunities, apply any pending Controller decisions (re-pricing
    live at submission), and expire stale proposals. Never raises for
    an ordinary data gap (no bars yet, no strike found) -- those are
    recorded in `TickResult.notes`; only a genuine construction error
    (bad arguments) would raise."""
    notes: List[str] = []
    today = now.strftime("%Y-%m-%d")

    try:
        spy_bars = options_client.get_spy_minute_bars(today)
    except AlpacaDataError as e:
        return TickResult(
            date=today, dynamic_cap=state.dynamic_cap_today, new_opportunities_seen=0,
            proposals_created=0, proposals_rejected_by_filter=0,
            proposals_rejected_by_concurrency=0, decisions_processed=0,
            orders_submitted=0, proposals_expired=0, notes=[f"SPY fetch failed: {e}"],
        )

    if state.current_date != today:
        state.current_date = today
        state.dynamic_cap_today = None
        state.processed_entry_keys = set()

    if state.dynamic_cap_today is None and len(spy_bars) >= 15:
        pace = session_pace_width_pct(spy_bars, minutes=15)
        state.dynamic_cap_today = state.pace_tracker.record_and_get_cap(pace)
        notes.append(f"day-pace cap locked in for {today}: {state.dynamic_cap_today:.1f} min")

    proposals_created = 0
    rejected_by_filter = 0
    rejected_by_concurrency = 0
    new_opportunities_seen = 0

    if state.dynamic_cap_today is not None:
        opportunities = find_breakout_opportunities(_bars_to_signal_series(spy_bars))
        for entry in opportunities:
            key = _entry_key(entry)
            if key in state.processed_entry_keys:
                continue
            new_opportunities_seen += 1
            state.processed_entry_keys.add(key)

            filter_results = run_all_filters(
                vix_level=None,  # KNOWN GAP -- see module docstring
                entry_time=entry.entry_time, hold_cap_minutes=state.dynamic_cap_today,
            )
            if any(not r.passed for r in filter_results):
                rejected_by_filter += 1
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

    submission_results = process_decisions(
        repo, pending_decisions, broker, quantity=quantity,
        options_client=options_client, ratio_provider=ratio_provider, now=now,
    )

    expired = expire_stale_pending(repo, older_than=now - STALE_PROPOSAL_MAX_AGE)

    return TickResult(
        date=today, dynamic_cap=state.dynamic_cap_today,
        new_opportunities_seen=new_opportunities_seen,
        proposals_created=proposals_created,
        proposals_rejected_by_filter=rejected_by_filter,
        proposals_rejected_by_concurrency=rejected_by_concurrency,
        decisions_processed=len(pending_decisions),
        orders_submitted=len(submission_results),
        proposals_expired=expired, notes=notes,
    )
