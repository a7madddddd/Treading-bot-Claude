"""Thin orchestration for the SPX 0DTE module (D-0095 execution wiring).

Owns NO business rule of its own -- sequences calls into existing,
unmodified authorities exactly like `orchestration.trade_proposal_service`
does for the equity-ladder flow: `OptionTradeProposal` governs its own
lifecycle (`spx0dte.proposal`), `SpxProposalRepository` is the only
persistence (`spx0dte.sqlite_repository`), `submit_entry_order` is the
only path to the broker (`spx0dte.execution`), and Telegram send/receive
stays inside `spx0dte.telegram_approval`.

Scope of this module: ENTRY only. Takes a candidate (from
`spx0dte.signal`/`entry_filters`, already run through those), creates a
proposal, notifies the Controller, and on an APPROVE decision still
inside the 5-minute window, submits a single buy order for the
approved quantity. Exit/close submission is explicitly out of scope
(see `execution.py`'s own docstring).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from execution.broker_client import BrokerClient, BrokerClientError

from spx0dte.alpaca_client import AlpacaOptionsClient
from spx0dte.execution import SubmissionResult, submit_entry_order, submit_exit_order
from spx0dte.live_pricing import (
    LivePricingUnavailableError, get_live_entry_quote, reprice_known_symbol,
)
from spx0dte.position import OpenPosition
from spx0dte.proposal import OptionTradeProposal, ProposalApprovalState
from spx0dte.ratio_calibration import RatioProvider
from spx0dte.sizing import MAX_CONCURRENT_POSITIONS, ConcurrentPositionTracker
from spx0dte.sqlite_repository import SpxPositionRepository, SpxProposalRepository
from spx0dte.telegram_approval import ControllerDecision, DecisionKind, send_proposal_message


def new_proposal_id() -> str:
    return f"SPX-{uuid.uuid4().hex[:12]}"


def build_proposal(
    *, date: str, direction: str, strike: int, occ_symbol: str,
    entry_time: datetime, hold_cap_minutes: float, estimated_premium: float,
    vix: Optional[float], filters_passed: str, now: datetime,
) -> OptionTradeProposal:
    return OptionTradeProposal(
        proposal_id=new_proposal_id(), date=date, direction=direction, strike=strike,
        occ_symbol=occ_symbol, entry_time=entry_time, hold_cap_minutes=hold_cap_minutes,
        estimated_premium=estimated_premium, vix=vix, filters_passed=filters_passed,
        created_at=now,
    )


def build_live_proposal(
    client: AlpacaOptionsClient, ratio_provider: RatioProvider,
    *, date: str, direction: str, entry_time: datetime, hold_cap_minutes: float,
    vix: Optional[float], filters_passed: str, now: datetime,
) -> OptionTradeProposal:
    """Builds a proposal using a LIVE strike search + Option B reprice
    (`live_pricing.get_live_entry_quote`) instead of a caller-supplied
    strike/price -- the path a real live/unattended loop uses. Raises
    `live_pricing.LivePricingUnavailableError` if no live quote could
    be produced; the caller decides whether that means "skip this
    candidate for now" or "retry shortly", this function does not
    guess either way."""
    quote = get_live_entry_quote(client, ratio_provider, direction, now=now)
    return build_proposal(
        date=date, direction=direction, strike=quote.strike, occ_symbol=quote.occ_symbol,
        entry_time=entry_time, hold_cap_minutes=hold_cap_minutes,
        estimated_premium=quote.bias_corrected_price, vix=vix, filters_passed=filters_passed, now=now,
    )


def _format_message(p: OptionTradeProposal) -> str:
    vix_str = f"{p.vix:.1f}" if p.vix is not None else "n/a"
    return (
        f"SPX 0DTE entry proposal\n"
        f"Date:       {p.date}\n"
        f"Direction:  {p.direction.upper()}\n"
        f"Strike:     {p.strike}\n"
        f"Symbol:     {p.occ_symbol}\n"
        f"Entry time: {p.entry_time.isoformat()}\n"
        f"Hold cap:   {p.hold_cap_minutes:.0f} min\n"
        f"Est. premium: ${p.estimated_premium:.2f} (informational -- re-priced at submission)\n"
        f"VIX:        {vix_str}\n"
        f"Filters:    {p.filters_passed}\n"
        f"Proposal id: {p.proposal_id}\n"
        f"Approval valid <= 5 min after you decide."
    )


def create_and_notify(
    repo: SpxProposalRepository, proposal: OptionTradeProposal,
    *, bot_token: str, chat_id: str,
) -> bool:
    """Persists the proposal, then attempts to notify. The proposal
    row is saved either way -- a failed Telegram send is a logged,
    non-fatal event, not a reason to lose the candidate (matches the
    equity-ladder flow's own failure-mode table in
    docs/architecture/telegram-approval.md §5)."""
    repo.save(proposal)
    return send_proposal_message(
        bot_token=bot_token, chat_id=chat_id,
        text=_format_message(proposal), proposal_id=proposal.proposal_id,
    )


def _resolve_fill_price(
    broker: BrokerClient, *, client_order_id: str, result: SubmissionResult, fallback_price: float,
) -> float:
    """The real confirmed fill price, when one can be determined, else
    `fallback_price` (the pre-trade estimate used to submit the order).

    2026-11-22 fix: `submit_order`'s own synchronous response almost
    never carries a fill yet (Alpaca typically reports "accepted", not
    "filled", in that same response) -- the project's own measurement
    (`scripts/spx0dte_measure_entry_fill_gap.py`, decisions.md) found
    this was true in 6 of 6 real sampled entries AND exits, meaning the
    earlier "prefer the broker's real fill" fix rarely actually fired.
    This makes ONE follow-up `get_order_by_client_order_id` call to
    check again right after submission, rather than relying solely on
    the original response. Still not a full reconciliation loop (no
    sleep/retry/wait here -- that would block the tick) -- a fill that
    isn't confirmed by this one extra check still falls back to the
    estimate, same as before."""
    if result.order_state is not None and result.order_state.filled_avg_price is not None:
        return result.order_state.filled_avg_price
    try:
        latest = broker.get_order_by_client_order_id(client_order_id)
    except BrokerClientError:
        return fallback_price
    if latest is not None and latest.filled_avg_price is not None:
        return latest.filled_avg_price
    return fallback_price


def process_decisions(
    repo: SpxProposalRepository, decisions: List[ControllerDecision],
    broker: BrokerClient, *, quantity: int,
    options_client: Optional[AlpacaOptionsClient] = None,
    ratio_provider: Optional[RatioProvider] = None,
    position_repo: Optional[SpxPositionRepository] = None,
    now: Optional[datetime] = None,
    notes: Optional[List[str]] = None,
    position_tracker: Optional[ConcurrentPositionTracker] = None,
) -> List[SubmissionResult]:
    """Applies each decision to its proposal and, for an APPROVE still
    inside the 5-minute window, submits a buy order. Returns one
    SubmissionResult per decision that reached submission (a REJECT,
    an unknown proposal_id, or an already-decided proposal produces no
    SubmissionResult -- nothing was submitted).

    SAFETY GATES at submission time (D-0095-A, 2026-11-19 -- found
    live, during the Controller's requested full-cycle dry run, when
    three proposals arrived close together and were all approved at
    once; `entry_filters`/`sizing.ConcurrentPositionTracker` only gate
    proposal CREATION, using each candidate's PLANNED exit, so by
    approval time they can be stale). If `position_repo` is given,
    an APPROVE is skipped (no order submitted, proposal stays marked
    APPROVED but nothing is sent to the broker) when EITHER:
    1. `MAX_CONCURRENT_POSITIONS` real open positions already exist
       (defense in depth alongside the creation-time check, using the
       REAL current count, not a planned-exit estimate), or
    2. a position with the SAME `occ_symbol` is already open -- the
       real incident this fixes: two proposals for the literal same
       option contract (same strike/direction/expiry), approved
       together, both re-priced identically and closed identically
       (same stop hit at the same tick, same $ loss on both).
    Each skip is appended to `notes` (if given) so it is visible in
    the tick log, not silently dropped.

    If `options_client`/`ratio_provider` are given, the limit price is
    RE-PRICED live at submission time via
    `live_pricing.reprice_known_symbol` (D-0007's own "re-check the
    price before submitting" principle -- a proposal's
    `estimated_premium` can be several minutes stale by the time the
    Controller decides). Falls back to the proposal's own
    `estimated_premium` if either is omitted (e.g. in a test, or a
    caller that hasn't wired live pricing yet) or if re-pricing fails
    -- a failed re-price is not a reason to silently skip an approved
    trade, but IS recorded as a note so it is visible, not hidden.

    If `position_repo` is given and submission succeeds, an
    `OpenPosition` is created and persisted (D-0095-A item 4) so
    `live_loop.monitor_open_positions` has something to track for an
    eventual exit/close. The entry price recorded is the broker's own
    `filled_avg_price` when the submission response includes one --
    the real confirmed fill, not the pre-trade estimate -- falling
    back to the submitted `limit_price` only when no fill price is
    yet available (D-0095-A, 2026-11-19 correction: recording the
    submitted limit unconditionally made every position's target/stop/
    armed state, all computed from this same baseline in
    `exit_logic.py`, drift from the real cost basis by however far the
    pre-trade stale-quote reprice missed -- the Controller caught this
    from a live trade that opened already showing a loss purely from
    this mismatch, not from a real price move)."""
    now = now or datetime.now(timezone.utc)
    results: List[SubmissionResult] = []

    for decision in decisions:
        proposal = repo.get(decision.proposal_id)
        if proposal is None:
            continue  # unknown proposal_id -- nothing to apply a decision to
        if proposal.approval_state != ProposalApprovalState.PENDING:
            continue  # already decided once; a decision may only be recorded once

        approved = decision.kind == DecisionKind.APPROVE
        decided = proposal.with_decision(approved=approved, decided_by=decision.decided_by, decided_at=now)
        repo.save(decided)

        if not approved:
            # 2026-11-22: a REJECT frees this proposal's concurrency-cap
            # reservation immediately -- it was made at creation time,
            # before this decision was known (sizing.ConcurrentPositionTracker's
            # own docstring).
            if position_tracker is not None:
                position_tracker.release(decided.proposal_id)
            continue

        if not decided.is_approval_still_valid(now=now):
            if position_tracker is not None:
                position_tracker.release(decided.proposal_id)
            continue  # expired between decision and this processing pass

        if position_repo is not None:
            currently_open = position_repo.list_open()
            if len(currently_open) >= MAX_CONCURRENT_POSITIONS:
                if position_tracker is not None:
                    position_tracker.release(decided.proposal_id)
                if notes is not None:
                    notes.append(
                        f"{decided.proposal_id}: approved but NOT submitted -- "
                        f"{len(currently_open)} positions already open (cap {MAX_CONCURRENT_POSITIONS})"
                    )
                continue
            if any(p.occ_symbol == decided.occ_symbol for p in currently_open):
                if position_tracker is not None:
                    position_tracker.release(decided.proposal_id)
                if notes is not None:
                    notes.append(
                        f"{decided.proposal_id}: approved but NOT submitted -- "
                        f"a position in {decided.occ_symbol} is already open"
                    )
                continue

        limit_price = decided.estimated_premium
        if options_client is not None and ratio_provider is not None:
            try:
                quote = reprice_known_symbol(
                    options_client, ratio_provider, occ_symbol=decided.occ_symbol,
                    strike=decided.strike, direction=decided.direction, now=now,
                )
                limit_price = quote.bias_corrected_price
            except LivePricingUnavailableError:
                pass  # fall back to estimated_premium, already assigned above

        result = submit_entry_order(
            broker, client_order_id=decided.proposal_id, occ_symbol=decided.occ_symbol,
            quantity=quantity, limit_price=limit_price,
        )
        results.append(result)

        if result.success and position_repo is not None:
            recorded_entry_price = _resolve_fill_price(
                broker, client_order_id=decided.proposal_id, result=result, fallback_price=limit_price,
            )
            position_repo.save(OpenPosition(
                proposal_id=decided.proposal_id, occ_symbol=decided.occ_symbol,
                direction=decided.direction, quantity=quantity, strike=decided.strike,
                date=decided.date, entry_time=now, entry_price=recorded_entry_price,
                hold_cap_minutes=decided.hold_cap_minutes,
            ))

    return results


def close_position(
    position_repo: SpxPositionRepository, position: OpenPosition, broker: BrokerClient,
    *, exit_price: float, exit_reason: str, now: datetime,
) -> SubmissionResult:
    """Submits the SELL order that closes `position` and, ONLY if the
    submission actually succeeded, persists the closed state -- called
    by `live_loop.monitor_open_positions` once `live_exit.
    LiveExitTracker` signals an exit trigger. Per `.claude/rules/
    01-roles-and-authority.md`, a protective exit needs no Controller
    approval (the exit RULE was already approved under D-0095); this
    function only submits and records, it does not ask.

    2026-11-22 fix: the position row is NO LONGER marked closed when
    the broker rejects/fails the order -- doing so previously left a
    real, still-open position silently untracked (popped from the
    caller's in-memory trackers) and recorded a fabricated realized
    pnl from the pre-trade estimate, while the real position could
    still be open and unmonitored at the broker. On a failed
    submission the row is left untouched so the caller keeps
    monitoring it and `LiveExitTracker` can re-trigger (and this
    function re-submit, under the SAME idempotent client_order_id) on
    a later tick. `SubmissionResult.success` is always returned so the
    caller can react -- it must check it, not assume a close happened.

    `exit_price` is the pre-trade estimate used to SUBMIT the order;
    on success, the row's recorded `exit_price` prefers the broker's
    real confirmed fill (via `_resolve_fill_price`'s one follow-up
    check, 2026-11-22 -- the original synchronous response rarely
    carries a fill yet) over the estimate, so realized pnl is computed
    from what the trade actually paid/received, not from the
    stale-quote reprice that was only ever a submission target."""
    client_order_id = f"{position.proposal_id}-exit"
    result = submit_exit_order(
        broker, client_order_id=client_order_id, occ_symbol=position.occ_symbol,
        quantity=position.quantity, limit_price=exit_price,
    )
    if not result.success:
        return result
    recorded_exit_price = _resolve_fill_price(
        broker, client_order_id=client_order_id, result=result, fallback_price=exit_price,
    )
    position_repo.save(
        position.with_exit(exit_time=now, exit_price=recorded_exit_price, exit_reason=exit_reason)
    )
    return result


def expire_stale_pending(
    repo: SpxProposalRepository, *, older_than: datetime,
    position_tracker: Optional[ConcurrentPositionTracker] = None,
) -> int:
    """Expires every PENDING proposal created before `older_than`.
    Returns the count expired. A proposal the Controller never acted
    on must not sit PENDING forever -- mirrors the equity-ladder
    flow's own supersession/expiry discipline. Also frees its
    concurrency-cap reservation (2026-11-22), the same as a REJECT
    does in `process_decisions` -- an expiry is a decision outcome
    too, just one the Controller never made in time."""
    count = 0
    for proposal in repo.list_pending():
        if proposal.created_at < older_than:
            repo.save(proposal.expire(expired_at=older_than))
            if position_tracker is not None:
                position_tracker.release(proposal.proposal_id)
            count += 1
    return count
