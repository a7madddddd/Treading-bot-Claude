"""Engine -- the persistent-process skeleton that composes the
already-existing, unchanged services into one coherent runtime
(Controller-approved Engine design review, this session).

Owns ONLY sequencing/scheduling/recovery/notification-routing. It
never re-implements a trading rule: every trigger comparison uses
values already frozen on `Trade` (`ladder1_price`/`ladder2_price`/
`active_floor_price`, set once at `freeze_initial_reference()` time);
every approval/D-0007/execution-mechanics/Ladder-2-confirmation/Floor
decision is delegated unchanged to `TradeProposalService`/
`ExecutionService`/`ProposalRepository`.

TWO independent cadences, per the Controller-approved design:
    - Ladder 1/Ladder 2 trigger detection AND the watchlist-driven
      creation of new Trades are BOTH gated by D-0021's existing,
      unchanged hourly schedule (see `schedule.is_d0021_check_time()`)
      -- `run_trigger_check()`. Neither is protective, so neither
      needs faster-than-hourly detection.
    - Reconciliation, decision intake, AND Floor detection are on a
      separate, tighter, independently-approved cadence --
      `run_reconciliation_tick()`. Floor was moved to THIS cadence
      (Controller-approved revision, this session) specifically
      because a protective exit benefits from faster detection than a
      discretionary entry does -- this does not touch or "silently
      change" D-0021 itself, which only ever governed Ladder/entry
      detection.
`run_forever()` composes both against a real clock; every other method
is a plain, synchronous, fully unit-testable function of `now`.

Floor SELL (Controller-approved, this session): reuses
`ExecutionService.submit_protective_exit()` -- proposal-independent,
approval-free, quantity always `trade.total_shares` read fresh, and
auto-applies even a partial fill (no Controller confirmation gate,
unlike Ladder 2 -- see `submit_protective_exit()`'s own docstring for
why).

Floor LIMIT PRICE (Controller-approved, this session): an execution
range of -1% to -0.5% off the current price at the moment Floor fires,
mirroring the SAME "or better" reasoning already approved for Ladder
1/Ladder 2's own execution range (`execution.service.
LADDER_EXECUTION_RANGE_FRACTION`) -- a SELL limit executes at the
specified limit price OR BETTER (i.e. at or above it), so using the
LOWER (more negative, -1%) boundary as the limit gives the order the
widest room to actually fill while still capping the worst acceptable
price at 1% below the trigger-time price -- see
`FLOOR_EXECUTION_RANGE_FRACTION` below and `_check_floor_trigger()`.

Watchlist-driven Trade creation (Controller-approved, this session):
the Engine never selects symbols itself -- `WatchlistSource.
get_active_symbols()` is the sole source, per `docs/architecture/
universe.md` §1. A symbol with no open Trade gets a new Trade +
Initial Entry proposal via `TradeProposalService.start_trade()`,
exactly the same discretionary/approval-gated Initial Entry flow that
already existed -- the Engine only decides WHEN to call it, never
invents any selection logic of its own.
"""

from __future__ import annotations

import time as _time_module
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional, Set, Tuple

from execution.broker_client import BrokerSubmissionAmbiguousError
from execution.repository import OrderExecutionRepository
from execution.service import (
    ExecutionAlreadyResolvedError,
    ExecutionAlreadySubmittedError,
    ExecutionService,
    Ladder2PartialFillNotPendingError,
    SubmissionNotAllowedError,
)
from marketdata.source import MarketDataSource, MarketDataUnavailableError
from notifications.service import INotificationService, NotificationEvent, NotificationLevel
from orchestration.trade_proposal_service import LadderAlreadyFilledError, TradeProposalService
from proposals.models import ApprovalState, TradeAction, approved_strategy_rule_set
from proposals.models import FloorContext
from proposals.proposal import build_trade_proposal
from proposals.repository import ProposalDecisionConflictError, ProposalRepository
from trade.models import describe_status
from trade.repository import TradeRepository

from .decision_source import ControllerDecision, DecisionKind, PendingDecisionSource
from .lock import EngineLock
from .schedule import is_d0021_check_time
from .watchlist import WatchlistSource

_LADDER_ACTIONS: Tuple[TradeAction, ...] = (TradeAction.LADDER_1, TradeAction.LADDER_2)

FLOOR_EXECUTION_RANGE_FRACTION = 0.01
"""Controller-approved (this session): Floor's SELL execution range is
-1% to -0.5% off the current price at the moment Floor fires. Only the
LOWER (-1%, more negative) boundary is actually used as the submitted
limit price -- exactly mirroring how Ladder 1/Ladder 2 only ever use
ONE boundary of their own execution range as the real limit price (see
`execution.service.LADDER_EXECUTION_RANGE_FRACTION` and
`_execution_limit_price_for()`), not both. A SELL limit executes at
the specified price OR BETTER (at or above it) -- using the lower
boundary as the limit therefore gives the order room to fill anywhere
from -1% up through -0.5% and beyond, while never accepting a price
worse than 1% below the trigger-time price."""


class Engine:
    def __init__(
        self,
        *,
        trade_repo: TradeRepository,
        proposal_repo: ProposalRepository,
        execution_repo: OrderExecutionRepository,
        trade_proposal_service: TradeProposalService,
        execution_service: ExecutionService,
        market_data: MarketDataSource,
        watchlist: WatchlistSource,
        decision_source: PendingDecisionSource,
        notifier: INotificationService,
        lock: EngineLock,
    ) -> None:
        self._trade_repo = trade_repo
        self._proposal_repo = proposal_repo
        self._execution_repo = execution_repo
        self._trade_proposal_service = trade_proposal_service
        self._execution_service = execution_service
        self._market_data = market_data
        self._watchlist = watchlist
        self._decision_source = decision_source
        self._notifier = notifier
        self._lock = lock
        self._notified: Set[Tuple[str, str]] = set()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, *, now: datetime) -> None:
        """Acquires the lock, then runs the full recovery pass. Raises
        EngineLockHeldError (propagated from EngineLock.acquire()) if
        another live Engine already holds it -- this method never
        silently proceeds unprotected."""

        self._lock.acquire(now=now)
        self._heartbeat(now=now)
        self.recover(now=now)

    def shutdown(self) -> None:
        """Clean shutdown: releases the lock. Safe to call even if
        start() was never called or the lock was already lost."""
        self._lock.release()

    def _heartbeat(self, *, now: datetime) -> None:
        self._lock.heartbeat(now=now)

    # ------------------------------------------------------------------
    # Recovery (startup, before entering the normal loop)
    # ------------------------------------------------------------------

    def recover(self, *, now: datetime) -> None:
        """Startup recovery pass. Per the Controller-approved design:
        no new persistence, no new broad query -- walks
        TradeRepository.list_active() (already exists, already
        documented for restart recovery) -> ProposalRepository.
        list_for_trade() -> existing execution lookups, re-deriving
        everything from state that is already there. Never marks
        anything resolved on its own; only calls existing, idempotent
        service methods and re-fires notifications for anything still
        genuinely pending."""

        self._heartbeat(now=now)
        self._execution_service.reconcile_unresolved(now=now)
        self._heartbeat(now=now)
        for trade_record in self._trade_repo.list_active():
            self._recover_trade(trade_record.trade.trade_id, now=now)
        self._heartbeat(now=now)

    def _recover_trade(self, trade_id: str, *, now: datetime) -> None:
        record = self._trade_repo.get(trade_id)
        if record is None:
            return

        proposals = self._proposal_repo.list_for_trade(trade_id)
        if not proposals and describe_status(record.trade) == "AWAITING_INITIAL_FILL":
            # Crash window documented in TradeProposalService.start_trade()'s
            # own docstring: the Trade was persisted but the process died
            # before its Initial Entry proposal was. Re-create the missing
            # proposal -- never creates a second Trade, never touches an
            # existing proposal.
            self._recreate_missing_initial_entry(trade_id, record.trade.symbol, now=now)

        for proposal in proposals:
            if proposal.approval_state is ApprovalState.PENDING:
                self._notify_once(
                    kind="pending_approval",
                    key=proposal.proposal_id,
                    level=NotificationLevel.IMPORTANT,
                    event="proposal_awaiting_approval",
                    message=(
                        f"[recovery] Proposal {proposal.proposal_id} ({proposal.proposed_action.value}) "
                        f"for trade {trade_id} is still awaiting Controller approval."
                    ),
                    symbol=proposal.symbol,
                )
                continue

            if proposal.approval_state is ApprovalState.APPROVED:
                self._execution_service.recover_if_terminal(proposal.proposal_id, now=now)
                self._maybe_notify_ladder2_pending_confirmation(proposal.proposal_id, now=now)

        # SELL-side (Floor) recovery -- has no proposal to anchor a
        # lookup to, so it needs its own call regardless of the
        # proposal walk above. Safe/idempotent: a no-op if no
        # protective-exit execution exists, or if it already applied.
        self._execution_service.recover_protective_exit_if_terminal(trade_id, now=now)

    def _recreate_missing_initial_entry(self, trade_id: str, symbol: str, *, now: datetime) -> None:
        try:
            price = self._market_data.get_last_trade(symbol)
        except MarketDataUnavailableError as exc:
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="market_data_unavailable",
                message=(
                    f"[recovery] Could not re-create the missing Initial Entry proposal for "
                    f"trade {trade_id!r} ({symbol}): {exc}"
                ),
                symbol=symbol,
            )
            return

        proposal_id = f"{trade_id}-initial_entry-{uuid.uuid4().hex[:8]}"
        proposal = build_trade_proposal(
            proposal_id=proposal_id,
            trade_id=trade_id,
            action=TradeAction.INITIAL_ENTRY,
            symbol=symbol,
            current_price=price,
            as_of=now,
            strategy=approved_strategy_rule_set(),
            floor_context=FloorContext.no_existing_position(),
        )
        self._proposal_repo.save(proposal)
        self._notify_once(
            kind="pending_approval",
            key=proposal.proposal_id,
            level=NotificationLevel.IMPORTANT,
            event="proposal_awaiting_approval",
            message=(
                f"[recovery] Re-created the missing Initial Entry proposal {proposal.proposal_id} "
                f"for trade {trade_id} ({symbol})."
            ),
            symbol=symbol,
        )

    # ------------------------------------------------------------------
    # Reconciliation + decision intake (tighter, independent cadence)
    # ------------------------------------------------------------------

    def run_reconciliation_tick(self, *, now: datetime) -> None:
        self._heartbeat(now=now)
        self._execution_service.reconcile_unresolved(now=now)
        self._heartbeat(now=now)
        self._apply_decisions(now=now)
        self._heartbeat(now=now)
        for trade_record in self._trade_repo.list_active():
            trade_id = trade_record.trade.trade_id
            self._check_floor_trigger(trade_id, now=now)
            for proposal in self._proposal_repo.list_for_trade(trade_id):
                if proposal.proposed_action is TradeAction.LADDER_2 and proposal.approval_state is ApprovalState.APPROVED:
                    self._maybe_notify_ladder2_pending_confirmation(proposal.proposal_id, now=now)
        self._heartbeat(now=now)

    def _check_floor_trigger(self, trade_id: str, *, now: datetime) -> None:
        """Floor detection -- Controller-approved to run on THIS
        (faster, independent) cadence rather than D-0021, specifically
        because a protective exit benefits from faster detection than
        a discretionary entry. No Controller approval gate at any
        point in this method, by design (execution.md §1/§2; TradeAction
        deliberately excludes FLOOR)."""

        record = self._trade_repo.get(trade_id)
        if record is None:
            return
        trade = record.trade
        if describe_status(trade) != "ACTIVE":
            return
        active_floor = trade.active_floor_price
        if active_floor is None:
            return

        try:
            price = self._market_data.get_last_trade(trade.symbol)
        except MarketDataUnavailableError as exc:
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="market_data_unavailable",
                message=f"Could not get current price for {trade.symbol} (trade {trade_id}) for Floor check: {exc}",
                symbol=trade.symbol,
            )
            return

        if price > active_floor:
            return

        existing = self._execution_repo.get_by_trade_id_and_side(trade_id, "sell")
        if existing is not None and not existing.execution.is_broker_terminal:
            return  # already has a live protective exit in flight -- never a duplicate submission

        # Controller-approved execution range: -1% to -0.5% off the
        # current price. Only the lower (-1%) boundary is submitted as
        # the actual limit price -- see FLOOR_EXECUTION_RANGE_FRACTION's
        # own docstring for why (mirrors the Ladder 1/Ladder 2 execution
        # range's "one boundary only" pattern exactly).
        limit_price = round(price * (1 - FLOOR_EXECUTION_RANGE_FRACTION), 4)

        self._notify(
            level=NotificationLevel.CRITICAL,
            event="floor_triggered",
            message=(
                f"Floor triggered for trade {trade_id} ({trade.symbol}): price {price} at or below "
                f"active floor {active_floor}. Submitting protective SELL for {trade.total_shares} "
                f"shares at limit {limit_price} (or better)."
            ),
            symbol=trade.symbol,
        )

        try:
            exit_record = self._execution_service.submit_protective_exit(
                trade_id, quantity=trade.total_shares, limit_price=limit_price, now=now
            )
        except ExecutionAlreadySubmittedError:
            return  # benign: another cycle already has one in flight
        except BrokerSubmissionAmbiguousError as exc:
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="floor_submission_ambiguous",
                message=(
                    f"Floor SELL submission for trade {trade_id} had an ambiguous outcome: {exc}. "
                    "Will resolve via reconciliation."
                ),
                symbol=trade.symbol,
            )
            return
        except Exception as exc:
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="floor_submission_failed",
                message=f"Floor SELL submission failed for trade {trade_id}: {exc}",
                symbol=trade.symbol,
            )
            return

        self._notify_floor_outcome(trade_id, exit_record.execution, symbol=trade.symbol)

    def _notify_floor_outcome(self, trade_id: str, execution, *, symbol: str) -> None:
        if not execution.is_broker_terminal:
            return
        if execution.filled_qty == execution.requested_qty:
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="floor_executed_full",
                message=(
                    f"Floor executed for trade {trade_id}: sold {execution.filled_qty}/"
                    f"{execution.requested_qty} shares. Trade closed."
                ),
                symbol=symbol,
            )
        elif execution.filled_qty > 0:
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="floor_executed_partial",
                message=(
                    f"Floor partially filled for trade {trade_id}: sold {execution.filled_qty}/"
                    f"{execution.requested_qty} shares (auto-applied, no approval needed). The "
                    "remaining shares will be re-offered for exit on a later cycle."
                ),
                symbol=symbol,
            )
        else:
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="floor_execution_rejected",
                message=f"Floor SELL for trade {trade_id} was rejected/expired with zero fill.",
                symbol=symbol,
            )

    def _apply_decisions(self, *, now: datetime) -> None:
        for decision in self._decision_source.poll():
            self._apply_decision(decision, now=now)

    def _apply_decision(self, decision: ControllerDecision, *, now: datetime) -> None:
        proposal = self._proposal_repo.get(decision.proposal_id)
        if proposal is None:
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="decision_unknown_proposal",
                message=f"Controller decision received for unknown proposal_id {decision.proposal_id!r} -- ignored.",
            )
            return

        if decision.kind is DecisionKind.CONFIRM_LADDER2_PARTIAL_FILL:
            try:
                self._execution_service.confirm_ladder2_partial_fill(
                    decision.proposal_id, decided_by=decision.decided_by, now=now
                )
            except Ladder2PartialFillNotPendingError as exc:
                self._notify(
                    level=NotificationLevel.CRITICAL,
                    event="ladder2_confirmation_refused",
                    message=f"Ladder 2 partial-fill confirmation for {decision.proposal_id} refused: {exc}",
                    symbol=proposal.symbol,
                )
            return

        approved = decision.kind is DecisionKind.APPROVE
        try:
            self._proposal_repo.record_decision(
                decision.proposal_id,
                approved=approved,
                decided_by=decision.decided_by,
                decided_at=now,
                action=proposal.proposed_action,
            )
        except ProposalDecisionConflictError as exc:
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="decision_conflict",
                message=f"Controller decision for {decision.proposal_id} could not be recorded: {exc}",
                symbol=proposal.symbol,
            )

    # ------------------------------------------------------------------
    # Trigger detection (D-0021-gated cadence)
    # ------------------------------------------------------------------

    def run_trigger_check(self, *, now: datetime) -> None:
        self._heartbeat(now=now)
        self._check_watchlist(now=now)
        self._heartbeat(now=now)
        for trade_record in self._trade_repo.list_active():
            self._process_trade(trade_record.trade.trade_id, now=now)
            self._heartbeat(now=now)

    def _check_watchlist(self, *, now: datetime) -> None:
        """Watchlist-driven Trade creation (Controller-approved, this
        session). The Engine never selects symbols -- only asks
        WatchlistSource what to watch and reacts. A symbol already
        having an open (AWAITING_INITIAL_FILL or ACTIVE) Trade is
        never given a second one -- list_for_symbol() (already exists)
        is the duplicate-prevention check, done synchronously right
        before start_trade() with no race window in this single-
        process design."""

        for symbol in self._watchlist.get_active_symbols():
            records = self._trade_repo.list_for_symbol(symbol)
            has_open_trade = any(describe_status(r.trade) in ("AWAITING_INITIAL_FILL", "ACTIVE") for r in records)
            if has_open_trade:
                continue
            self._start_new_trade(symbol, now=now)

    def _start_new_trade(self, symbol: str, *, now: datetime) -> None:
        try:
            price = self._market_data.get_last_trade(symbol)
        except MarketDataUnavailableError as exc:
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="market_data_unavailable",
                message=f"Could not get current price for {symbol} to start a new watchlist-driven trade: {exc}",
                symbol=symbol,
            )
            return

        trade_id = f"{symbol}-{uuid.uuid4().hex[:8]}"
        proposal_id = f"{trade_id}-initial_entry-{uuid.uuid4().hex[:8]}"
        _trade_record, proposal = self._trade_proposal_service.start_trade(
            trade_id=trade_id,
            symbol=symbol,
            proposal_id=proposal_id,
            current_price=price,
            strategy=approved_strategy_rule_set(),
            floor_context=FloorContext.no_existing_position(),
            now=now,
        )
        self._notify(
            level=NotificationLevel.IMPORTANT,
            event="proposal_awaiting_approval",
            message=(
                f"New trade {trade_id} started for {symbol} (from watchlist); Initial Entry proposal "
                f"{proposal.proposal_id} awaiting Controller approval."
            ),
            symbol=symbol,
        )
        self._notified.add(("pending_approval", proposal.proposal_id))

    def _process_trade(self, trade_id: str, *, now: datetime) -> None:
        record = self._trade_repo.get(trade_id)
        if record is None:
            return
        trade = record.trade
        if describe_status(trade) != "ACTIVE":
            return  # AWAITING_INITIAL_FILL: the Initial Entry proposal/approval/
            # submission/reconciliation flow is handled by _check_watchlist()/
            # start_trade() and the normal Proposal/Execution machinery, not
            # by this per-cycle Ladder/Floor loop.

        try:
            price = self._market_data.get_last_trade(trade.symbol)
        except MarketDataUnavailableError as exc:
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="market_data_unavailable",
                message=f"Could not get current price for {trade.symbol} (trade {trade_id}): {exc}",
                symbol=trade.symbol,
            )
            return

        active_floor = trade.active_floor_price

        for action, filled, trigger_price in (
            (TradeAction.LADDER_1, trade.ladder1_filled, trade.ladder1_price),
            (TradeAction.LADDER_2, trade.ladder2_filled, trade.ladder2_price),
        ):
            if filled or trigger_price is None:
                continue

            # D-0011's proposal-creation gate / strategy.md §4: never
            # propose a Ladder at or below the active floor. D-0007's
            # own floor-priority check (validate_for_submission(),
            # unchanged) remains the authoritative enforcement at
            # submission time regardless of this pre-check.
            if active_floor is not None and trigger_price <= active_floor:
                continue

            latest = self._latest_proposal_for(trade_id, action)
            has_live_attempt = latest is not None and latest.approval_state in (
                ApprovalState.PENDING,
                ApprovalState.APPROVED,
            )

            if price <= trigger_price and not has_live_attempt:
                self._create_ladder_proposal(
                    trade_id,
                    action,
                    original_entry_price=trade.original_initial_entry_fill_price,
                    active_floor=active_floor,
                    now=now,
                )
            elif latest is not None and latest.approval_state is ApprovalState.APPROVED:
                self._submit_approved(latest.proposal_id, current_price=price, active_floor_price=active_floor, now=now)

    def _latest_proposal_for(self, trade_id: str, action: TradeAction):
        relevant = [p for p in self._proposal_repo.list_for_trade(trade_id) if p.proposed_action is action]
        return relevant[-1] if relevant else None

    def _create_ladder_proposal(
        self,
        trade_id: str,
        action: TradeAction,
        *,
        original_entry_price: Optional[float],
        active_floor: Optional[float],
        now: datetime,
    ) -> None:
        """`build_trade_proposal()` (unchanged, called unmodified via
        TradeProposalService.propose_next_action()) always recomputes
        `ladder_1_trigger`/`ladder_2_trigger` from its `current_price`
        argument, for BOTH Initial Entry and Ladder proposals -- its
        own docstring only documents the Initial Entry meaning of that
        parameter. For a Ladder proposal this argument must be the
        Trade's frozen ORIGINAL initial-entry fill price (D-0001: every
        percentage is relative to the original fill, never a floating
        reference) so that the resulting ladder_1_trigger/ladder_2_trigger
        exactly reproduce Trade.ladder1_price/ladder2_price (already
        frozen at freeze_initial_reference() time) -- NEVER the live
        market price at proposal-creation time, which would silently
        recompute a drifting, strategy-incorrect trigger."""

        proposal_id = f"{trade_id}-{action.value}-{uuid.uuid4().hex[:8]}"
        floor_context = FloorContext.known(active_floor) if active_floor is not None else FloorContext.no_existing_position()
        try:
            proposal = self._trade_proposal_service.propose_next_action(
                trade_id=trade_id,
                action=action,
                proposal_id=proposal_id,
                current_price=original_entry_price,
                strategy=approved_strategy_rule_set(),
                floor_context=floor_context,
                now=now,
            )
        except LadderAlreadyFilledError:
            return

        self._notify(
            level=NotificationLevel.IMPORTANT,
            event="proposal_awaiting_approval",
            message=f"New proposal {proposal.proposal_id} ({action.value}) for trade {trade_id} awaiting Controller approval.",
            symbol=proposal.symbol,
        )
        self._notified.add(("pending_approval", proposal.proposal_id))

    def _submit_approved(
        self, proposal_id: str, *, current_price: float, active_floor_price: Optional[float], now: datetime
    ) -> None:
        try:
            self._execution_service.submit_approved_proposal(
                proposal_id, current_price=current_price, active_floor_price=active_floor_price, now=now
            )
        except (ExecutionAlreadyResolvedError, ExecutionAlreadySubmittedError):
            return  # benign: already handled by a prior cycle
        except SubmissionNotAllowedError as exc:
            self._notify(
                level=NotificationLevel.IMPORTANT,
                event="submission_not_allowed",
                message=f"Submission for {proposal_id} refused by D-0007 revalidation: {exc}",
            )
        except BrokerSubmissionAmbiguousError as exc:
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="submission_ambiguous",
                message=f"Submission for {proposal_id} had an ambiguous outcome: {exc}. Will resolve via reconciliation.",
            )

    # ------------------------------------------------------------------
    # Ladder 2 partial-fill confirmation notification (shared by
    # recovery and the reconciliation tick -- same derivable condition)
    # ------------------------------------------------------------------

    def _maybe_notify_ladder2_pending_confirmation(self, proposal_id: str, *, now: datetime) -> None:
        proposal = self._proposal_repo.get(proposal_id)
        if proposal is None or proposal.proposed_action is not TradeAction.LADDER_2:
            return
        execution_record = self._execution_repo.get_by_proposal_id(proposal_id)
        if execution_record is None:
            return
        execution = execution_record.execution
        if not execution.is_broker_terminal:
            return
        if execution.filled_qty == 0 or execution.filled_qty == execution.requested_qty:
            return

        trade_record = self._trade_repo.get(proposal.trade_id)
        if trade_record is None or trade_record.trade.ladder2_filled:
            return

        self._notify_once(
            kind="ladder2_partial_fill",
            key=proposal_id,
            level=NotificationLevel.IMPORTANT,
            event="ladder2_partial_fill_pending_confirmation",
            message=(
                f"Ladder 2 for proposal {proposal_id} intended {execution.requested_qty} shares; "
                f"broker's final terminal fill was {execution.filled_qty}. Awaiting explicit "
                "Controller approval before recording this as Ladder 2 completion. No automatic "
                "top-up will be submitted for the remainder."
            ),
            symbol=proposal.symbol,
        )

    # ------------------------------------------------------------------
    # Notification dedup (in-memory, Controller-approved)
    # ------------------------------------------------------------------

    def _notify_once(
        self,
        *,
        kind: str,
        key: str,
        level: NotificationLevel,
        event: str,
        message: str,
        symbol: Optional[str] = None,
    ) -> None:
        dedup_key = (kind, key)
        if dedup_key in self._notified:
            return
        self._notified.add(dedup_key)
        self._notify(level=level, event=event, message=message, symbol=symbol)

    def _notify(
        self, *, level: NotificationLevel, event: str, message: str, symbol: Optional[str] = None
    ) -> None:
        self._notifier.send(NotificationEvent(level=level, event=event, message=message, symbol=symbol))

    # ------------------------------------------------------------------
    # Real-clock composition. Every method above is a plain function of
    # `now` and is exercised directly by tests; this is the only method
    # that actually sleeps, and is intentionally thin (composition only,
    # no new logic) so nothing load-bearing lives here untested.
    # ------------------------------------------------------------------

    def run_forever(
        self,
        *,
        reconciliation_interval_seconds: float,
        now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        sleep_fn: Callable[[float], None] = _time_module.sleep,
        max_iterations: Optional[int] = None,
    ) -> None:
        """Composes run_reconciliation_tick() (every
        reconciliation_interval_seconds) with run_trigger_check() (only
        when is_d0021_check_time(now) is True) against a real clock.
        `max_iterations` is test-only -- bounds the loop instead of
        running forever, so this method itself is exercisable without
        mocking an infinite loop. Does NOT call start()/shutdown() --
        the caller acquires the lock and runs recovery before this, and
        releases the lock after, exactly like any other Engine method."""

        iterations = 0
        while max_iterations is None or iterations < max_iterations:
            now = now_fn()
            self.run_reconciliation_tick(now=now)
            if is_d0021_check_time(now):
                self.run_trigger_check(now=now)
            self._heartbeat(now=now_fn())
            iterations += 1
            if max_iterations is None or iterations < max_iterations:
                sleep_fn(reconciliation_interval_seconds)
