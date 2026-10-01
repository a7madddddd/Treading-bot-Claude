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
from typing import Callable, List, Optional, Set, Tuple

from execution.broker_client import (
    BrokerClientError,
    BrokerCommunicationError,
    BrokerSubmissionAmbiguousError,
)
from execution.repository import OrderExecutionRepository
from execution.service import (
    ExecutionAlreadyResolvedError,
    ExecutionAlreadySubmittedError,
    ExecutionService,
    Ladder2PartialFillNotPendingError,
    PortfolioRiskViolatedError,
    SubmissionNotAllowedError,
)
from marketdata.source import MarketDataSource, MarketDataUnavailableError
from notifications.service import INotificationService, NotificationEvent, NotificationLevel
from orchestration.trade_proposal_service import LadderAlreadyFilledError, TradeProposalService
from proposals.models import ApprovalState, TradeAction, approved_strategy_rule_set
from proposals.models import FloorContext
from proposals.proposal import build_trade_proposal
from proposals.repository import ProposalDecisionConflictError, ProposalRepository
from trade.models import TradeStateError, describe_status
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


def _format_price_context_block(
    buy_price: float, price_context: Optional[dict]
) -> str:
    """Formats the 'Price context' block appended above the Buy price
    line in an Initial Entry notification. Skips any field that is
    absent from `price_context`. Returns an empty string if there is
    nothing worth showing."""

    if not isinstance(price_context, dict) or not price_context:
        return ""

    lines: List[str] = []
    prev = price_context.get("previous_close")
    if isinstance(prev, (int, float)) and prev > 0:
        change = buy_price - prev
        pct = (change / prev) * 100.0
        arrow = "▲" if change > 0 else ("▼" if change < 0 else "•")
        lines.append(f"  Previous close: ${prev:,.2f}")
        lines.append(
            f"  Change now:     {arrow} ${change:+,.2f}  ({pct:+.2f}%)"
        )

    hi = price_context.get("today_high")
    lo = price_context.get("today_low")
    if (
        isinstance(hi, (int, float)) and hi > 0
        and isinstance(lo, (int, float)) and lo > 0
    ):
        lines.append(f"  Today range:    ${lo:,.2f} - ${hi:,.2f}")

    op = price_context.get("today_open")
    if isinstance(op, (int, float)) and op > 0:
        lines.append(f"  Today open:     ${op:,.2f}")

    if not lines:
        return ""
    return (
        "\n"
        "Price context (why this level now):\n"
        + "\n".join(lines)
        + "\n"
    )


def _format_proposal_message(
    proposal, *, recovery: bool = False,
    price_context: Optional[dict] = None,
) -> str:
    """Human-readable notification body for a proposal_awaiting_approval
    event. Deliberately shows prices, quantities, dollar cost, and every
    downside threshold in plain language -- so the Controller can decide
    without opening a database or a trade log. IDs (proposal_id,
    trade_id) are intentionally omitted from the visible text; the
    buttons' callback_data still carries the proposal_id, and the
    text-command fallback (/approve <id>) is unchanged.

    `price_context`, when supplied, is a dict from
    MarketDataSource.get_price_context (previous_close, today_open,
    today_high, today_low, today_volume). Any subset may be present;
    the format helper shows only the fields it received. Enrichment
    only -- never affects a trigger or execution decision.
    """

    symbol = proposal.symbol
    action = proposal.proposed_action
    prefix = "[recovery] " if recovery else ""

    if action is TradeAction.INITIAL_ENTRY:
        strategy = approved_strategy_rule_set()
        buy_price = proposal.proposed_entry
        qty = strategy.initial_qty
        cost = buy_price * qty
        l1 = proposal.ladder_1_trigger
        l2 = proposal.ladder_2_trigger
        fl = proposal.floor_trigger
        context_block = _format_price_context_block(buy_price, price_context)
        return (
            f"{prefix}🎯 {symbol} — Initial Entry\n"
            f"{context_block}"
            f"\n"
            f"Buy price: ${buy_price:,.2f}\n"
            f"Quantity:  {qty} shares\n"
            f"Cost:      ${cost:,.2f}\n"
            f"\n"
            f"Downside safeguards (auto-computed from buy price):\n"
            f"  Ladder 1 buy at: ${l1:,.2f}  (-5%)\n"
            f"  Ladder 2 buy at: ${l2:,.2f}  (-8%)\n"
            f"  Auto-sell floor: ${fl:,.2f}  (-10%, protective)"
        )

    if action in (TradeAction.LADDER_1, TradeAction.LADDER_2):
        if action is TradeAction.LADDER_1:
            trigger = proposal.ladder_1_trigger
            qty = proposal.ladder_1_quantity
            pct = 5
            label = "Ladder 1"
        else:
            trigger = proposal.ladder_2_trigger
            qty = proposal.ladder_2_quantity
            pct = 8
            label = "Ladder 2"
        entry_ref = proposal.proposed_entry
        cost = trigger * qty
        avg_at = proposal.weighted_avg_entry_at_proposal
        floor = proposal.active_floor_at_proposal
        after = ""
        if avg_at is not None:
            after = (
                f"\n"
                f"Current avg entry: ${avg_at:,.2f}"
            )
        if floor is not None:
            after += (
                f"\n"
                f"Active floor: ${floor:,.2f}  (unchanged by this ladder)"
            )
        return (
            f"{prefix}📉 {symbol} — {label} (buy more, price down {pct}%)\n"
            f"\n"
            f"Trigger price:  ${trigger:,.2f}\n"
            f"Reference entry: ${entry_ref:,.2f}\n"
            f"Change from entry: -{pct}%\n"
            f"\n"
            f"Additional buy: {qty} shares\n"
            f"Cost:           ${cost:,.2f}"
            f"{after}"
        )

    # Fallback for any future action -- keep something human-readable
    # rather than silently omitting the action.
    return f"{prefix}{symbol} — {action.value} awaiting Controller approval."


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
                    message=_format_proposal_message(
                        proposal,
                        recovery=True,
                        price_context=(
                            self._safe_price_context(proposal.symbol)
                            if proposal.proposed_action is TradeAction.INITIAL_ENTRY
                            else None
                        ),
                    ),
                    symbol=proposal.symbol,
                    interactive_actions=(
                        ("✅ Approve", f"approve:{proposal.proposal_id}"),
                        ("❌ Reject", f"reject:{proposal.proposal_id}"),
                    ),
                )
                continue

            if proposal.approval_state is ApprovalState.APPROVED:
                # Same live-observed gap as in _apply_decision: an
                # Initial Entry approved before a process crash never
                # got a broker order (its per-cycle submission path is
                # gated behind ACTIVE status, which it does not yet
                # have). On startup, retry the submission if no
                # execution record exists yet. Ladders are submitted
                # by _process_trade at the next tick as before.
                if (
                    proposal.proposed_action is TradeAction.INITIAL_ENTRY
                    and self._execution_repo.get_by_proposal_id(proposal.proposal_id) is None
                ):
                    try:
                        price = self._market_data.get_last_trade(proposal.symbol)
                    except MarketDataUnavailableError:
                        # Leave APPROVED; next reconciliation tick with
                        # working market data will retry via the same
                        # gap-fix in _apply_decision (queue drain) OR
                        # future recovery ticks. Not fatal.
                        continue
                    self._submit_approved(
                        proposal.proposal_id,
                        current_price=price,
                        active_floor_price=proposal.floor_trigger,
                        now=now,
                    )
                    continue
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
            message=_format_proposal_message(
                proposal, recovery=True,
                price_context=self._safe_price_context(symbol),
            ),
            symbol=symbol,
            interactive_actions=(
                ("✅ Approve", f"approve:{proposal.proposal_id}"),
                ("❌ Reject", f"reject:{proposal.proposal_id}"),
            ),
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
        # Recovery pass for APPROVED proposals whose submission never
        # happened (crash between record_decision and _submit_approved,
        # or any other reason the execution row is absent). Without
        # this, an INITIAL_ENTRY approved just before a container
        # restart stays APPROVED-but-unsubmitted forever (2026-10-01
        # Finding #4).
        self._recover_approved_without_execution(now=now)
        self._heartbeat(now=now)
        for trade_record in self._trade_repo.list_active():
            trade_id = trade_record.trade.trade_id
            self._check_floor_trigger(trade_id, now=now)
            for proposal in self._proposal_repo.list_for_trade(trade_id):
                if proposal.proposed_action is TradeAction.LADDER_2 and proposal.approval_state is ApprovalState.APPROVED:
                    self._maybe_notify_ladder2_pending_confirmation(proposal.proposal_id, now=now)
        self._heartbeat(now=now)

    def _recover_approved_without_execution(self, *, now: datetime) -> None:
        """Finds proposals in state APPROVED that have no execution
        row (never submitted) and retries submission.

        Scope: INITIAL_ENTRY only. Ladder APPROVED-but-unsubmitted is
        already retried by _process_trade on every D-0021 :30 tick --
        this method deliberately does not duplicate that, since D-0007
        requires ladder revalidation to use a fresh market price
        anyway and _process_trade already has that structure.
        """

        for trade_record in self._trade_repo.list_active():
            trade = trade_record.trade
            for proposal in self._proposal_repo.list_for_trade(trade.trade_id):
                if proposal.proposed_action is not TradeAction.INITIAL_ENTRY:
                    continue
                if proposal.approval_state is not ApprovalState.APPROVED:
                    continue
                if self._execution_repo.get_by_proposal_id(proposal.proposal_id) is not None:
                    continue
                # APPROVED, INITIAL_ENTRY, no execution row -- retry.
                try:
                    price = self._market_data.get_last_trade(proposal.symbol)
                except MarketDataUnavailableError as exc:
                    self._notify(
                        level=NotificationLevel.CRITICAL,
                        event="market_data_unavailable",
                        message=(
                            f"Recovery: could not fetch current price for "
                            f"{proposal.symbol} to retry approved Initial Entry "
                            f"{proposal.proposal_id}: {exc}."
                        ),
                        symbol=proposal.symbol,
                    )
                    continue
                self._notify(
                    level=NotificationLevel.IMPORTANT,
                    event="initial_entry_recovery_submit",
                    message=(
                        f"Recovery: retrying submission of approved Initial Entry "
                        f"{proposal.proposal_id} for {proposal.symbol} (no execution "
                        f"row found)."
                    ),
                    symbol=proposal.symbol,
                )
                self._submit_approved(
                    proposal.proposal_id,
                    current_price=price,
                    active_floor_price=proposal.floor_trigger,
                    now=now,
                    initial_entry_trade_id=proposal.trade_id,
                )

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

        # Trailing floor activation + ratchet (D-0004/D-0008). Must run
        # BEFORE reading active_floor so the latest trailing_floor_price
        # participates in the floor comparison this same tick.
        trade, record = self._maybe_update_trailing(record, price, now=now)
        if record is None:
            return

        active_floor = trade.active_floor_price
        if active_floor is None:
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
        # range's "one boundary only" pattern exactly). Rounding is left
        # to the broker-client formatter (SEC Rule 612, side-aware
        # ROUND_UP for sell) so precision is quantized in exactly one
        # place.
        limit_price = price * (1 - FLOOR_EXECUTION_RANGE_FRACTION)

        self._notify(
            level=NotificationLevel.CRITICAL,
            event="floor_triggered",
            message=(
                f"Floor triggered for trade {trade_id} ({trade.symbol}): price {price} at or below "
                f"active floor {active_floor}. Submitting protective SELL for {trade.total_shares} "
                f"shares at limit {limit_price:.4f} (or better; broker quantizes to SEC-compliant precision)."
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

    def _maybe_update_trailing(self, record, price, *, now):
        """Trailing floor activation + ratchet (strategy.md Sec.5 /
        D-0004 / D-0008). Threshold-based, not price-based: activates
        the first time price reaches weighted_avg_entry_price * 1.10
        and the trailing floor becomes activation_threshold * 0.95;
        each subsequent threshold is previous * 1.05 and the new floor
        is new_threshold * 0.95. The floor only ever moves UP (defended
        in trade.models.ratchet_trailing). Called from
        _check_floor_trigger on the faster reconciliation cadence,
        BEFORE active_floor_price is read, so the latest trailing
        state participates in the floor comparison this same tick.
        Idempotent: a tick where no activation/ratchet condition holds
        is a plain no-op; a tick exactly on the activation threshold
        activates once and returns.

        Returns the (possibly-updated) (trade, record) tuple. If a
        write failed (concurrent revision, repo error) returns the
        original record so the floor check still runs against the
        pre-update state and the next tick retries."""

        trade = record.trade
        wae = trade.weighted_avg_entry_price
        if wae is None:
            return trade, record

        # Activation.
        if not trade.trailing_activated:
            activation_threshold = round(wae * 1.10, 4)
            if price >= activation_threshold:
                try:
                    updated = trade.activate_trailing(
                        current_price=activation_threshold, now=now,
                    )
                    new_record = self._trade_repo.update(
                        updated,
                        expected_revision=record.revision,
                        transition="trailing_activated",
                        now=now,
                    )
                    self._notify(
                        level=NotificationLevel.IMPORTANT,
                        event="trailing_activated",
                        message=(
                            f"Trailing floor ACTIVATED for {trade.symbol} (trade "
                            f"{trade.trade_id}): price {price:.4f} reached activation "
                            f"threshold {activation_threshold:.4f} (WAE {wae:.4f} x 1.10). "
                            f"Trailing floor now at {updated.trailing_floor_price:.4f} "
                            f"(vs original floor {trade.original_floor_price:.4f})."
                        ),
                        symbol=trade.symbol,
                    )
                    record = new_record
                    trade = record.trade
                except TradeStateError:
                    # price slipped below threshold between read and
                    # activate -- benign, retry next tick.
                    return trade, record
                except Exception as exc:  # noqa: BLE001
                    self._notify(
                        level=NotificationLevel.CRITICAL,
                        event="trailing_activation_failed",
                        message=(
                            f"Trailing activation write failed for {trade.symbol} "
                            f"(trade {trade.trade_id}): {exc}. Will retry on the next tick."
                        ),
                        symbol=trade.symbol,
                    )
                    return trade, record

        # Ratchet (loop while the current price keeps clearing the next
        # +5% threshold -- matches the backtest simulator's `while` so
        # live and simulated trades behave identically for the same
        # price path).
        while (
            trade.trailing_activated
            and trade.trailing_current_threshold is not None
        ):
            next_threshold = round(trade.trailing_current_threshold * 1.05, 4)
            if price < next_threshold:
                break
            try:
                updated = trade.ratchet_trailing(current_price=next_threshold, now=now)
            except TradeStateError:
                break
            try:
                new_record = self._trade_repo.update(
                    updated,
                    expected_revision=record.revision,
                    transition="trailing_ratcheted",
                    now=now,
                )
            except Exception as exc:  # noqa: BLE001
                self._notify(
                    level=NotificationLevel.CRITICAL,
                    event="trailing_ratchet_failed",
                    message=(
                        f"Trailing ratchet write failed for {trade.symbol} (trade "
                        f"{trade.trade_id}): {exc}. Will retry on the next tick."
                    ),
                    symbol=trade.symbol,
                )
                return trade, record
            self._notify(
                level=NotificationLevel.IMPORTANT,
                event="trailing_ratcheted",
                message=(
                    f"Trailing floor RATCHETED for {trade.symbol} (trade "
                    f"{trade.trade_id}): new threshold {next_threshold:.4f}, "
                    f"new trailing floor {updated.trailing_floor_price:.4f}."
                ),
                symbol=trade.symbol,
            )
            record = new_record
            trade = record.trade

        return trade, record

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
            return

        # For a REJECTED Initial Entry, mark the Trade as ABANDONED so
        # the symbol is available on the next watchlist tick. A Trade
        # row is created at proposal-generation time before Controller
        # approval; if it stays in AWAITING_INITIAL_FILL after the
        # rejection, _check_watchlist's "already has an open trade for
        # this symbol" guard permanently locks the symbol out (a
        # live-observed 2026-09-30 audit finding).
        if (
            not approved
            and proposal.proposed_action is TradeAction.INITIAL_ENTRY
        ):
            trade_record = self._trade_repo.get(proposal.trade_id)
            if trade_record is not None:
                trade = trade_record.trade
                # Only abandon if the trade is still fresh (never
                # reconciled). A rejection arriving for a
                # somehow-already-reconciled trade is odd but not
                # dangerous -- ignore it and leave the trade alone.
                if not trade.initial_order_reconciled:
                    try:
                        self._trade_repo.update(
                            trade.abandon_before_fill(now=now),
                            expected_revision=trade_record.revision,
                            transition="rejected_before_fill",
                            now=now,
                        )
                    except Exception as exc:  # noqa: BLE001
                        # Shielded like the submission path -- a repo
                        # write failure here must never crash the
                        # engine.
                        self._notify(
                            level=NotificationLevel.CRITICAL,
                            event="abandon_failed",
                            message=(
                                f"Could not mark {proposal.trade_id} as ABANDONED after "
                                f"rejection of {proposal.proposal_id}: {exc}. "
                                f"Symbol may be blocked from new proposals until DB is cleaned."
                            ),
                            symbol=proposal.symbol,
                        )
            return

        # For an Initial Entry approval, submit to the broker immediately.
        # The per-cycle _process_trade loop skips AWAITING_INITIAL_FILL
        # trades (see the guard there), so nothing else in this file
        # would ever fire submit_approved_proposal for an Initial Entry
        # -- the proposal would sit APPROVED-but-unsubmitted forever
        # (a real bug observed live in the 2026-09-29 paper session).
        # Ladder approvals continue to be submitted by _process_trade at
        # the next tick, which also revalidates the trigger against the
        # current price and D-0007's ±0.5% band.
        if approved and proposal.proposed_action is TradeAction.INITIAL_ENTRY:
            try:
                price = self._market_data.get_last_trade(proposal.symbol)
            except MarketDataUnavailableError as exc:
                self._notify(
                    level=NotificationLevel.CRITICAL,
                    event="market_data_unavailable",
                    message=(
                        f"Could not fetch current price for {proposal.symbol} to submit "
                        f"approved Initial Entry proposal {proposal.proposal_id}: {exc}. "
                        f"Proposal remains APPROVED; submission will be retried on the next "
                        f"reconciliation tick that has market data."
                    ),
                    symbol=proposal.symbol,
                )
                return
            # An INITIAL_ENTRY has no prior position and therefore no
            # PRIOR active floor -- but Controller-approved D-0007
            # revalidation still requires a non-None floor for every
            # submission (see tests/proposals/test_revalidation.py's
            # TestD2UnknownFloorBlocks). The proposal itself already
            # carries the floor level this trade WILL have once it
            # opens (proposal.floor_trigger = entry * (1 + floor_pct)),
            # which is the semantically correct value to revalidate
            # the entry price against.
            self._submit_approved(
                proposal.proposal_id,
                current_price=price,
                active_floor_price=proposal.floor_trigger,
                now=now,
                initial_entry_trade_id=proposal.trade_id,
            )
            return

        # For a LADDER approval, submit immediately too -- the per-cycle
        # _process_trade loop only submits on D-0021 :30 ticks, so a
        # Controller approval at (say) 10:45 would sit unsubmitted until
        # 11:30. That 45-minute wait guarantees D-0007's 5-minute /
        # +/-0.5% window expires before submission ever runs, and every
        # legitimate Ladder approval gets refused as EXPIRED / PRICE_DRIFT.
        # Ladder pricing is anchored on the trade's frozen original entry
        # and its already-computed trigger, so we still need the live
        # price to satisfy D-0007's band check plus the active floor
        # from the trade itself (an existing position always has a
        # concrete original floor).
        if approved and proposal.proposed_action in _LADDER_ACTIONS:
            trade_record = self._trade_repo.get(proposal.trade_id)
            if trade_record is None:
                # Genuinely broken state; the shield above already
                # handles unknown-proposal, but a missing trade is
                # different and worth its own CRITICAL notification.
                self._notify(
                    level=NotificationLevel.CRITICAL,
                    event="decision_missing_trade",
                    message=(
                        f"Approved LADDER {proposal.proposal_id} references trade "
                        f"{proposal.trade_id} which is missing from the repository. "
                        f"Submission skipped; investigate the state store."
                    ),
                    symbol=proposal.symbol,
                )
                return
            trade = trade_record.trade
            try:
                price = self._market_data.get_last_trade(proposal.symbol)
            except MarketDataUnavailableError as exc:
                self._notify(
                    level=NotificationLevel.CRITICAL,
                    event="market_data_unavailable",
                    message=(
                        f"Could not fetch current price for {proposal.symbol} to submit "
                        f"approved LADDER proposal {proposal.proposal_id}: {exc}. "
                        f"Proposal remains APPROVED; the per-cycle _process_trade path "
                        f"will retry on the next :30 tick with a fresh price."
                    ),
                    symbol=proposal.symbol,
                )
                return
            self._submit_approved(
                proposal.proposal_id,
                current_price=price,
                active_floor_price=trade.active_floor_price,
                now=now,
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
            message=_format_proposal_message(
                proposal,
                price_context=self._safe_price_context(symbol),
            ),
            symbol=symbol,
            interactive_actions=(
                ("✅ Approve", f"approve:{proposal.proposal_id}"),
                ("❌ Reject", f"reject:{proposal.proposal_id}"),
            ),
        )
        self._notified.add(("pending_approval", proposal.proposal_id))

    def _safe_price_context(self, symbol: str) -> Optional[dict]:
        """Best-effort wrapper around MarketDataSource.get_price_context.
        Never raises: a failure just means the price-context block is
        omitted from the notification (per contract)."""
        try:
            return self._market_data.get_price_context(symbol)
        except Exception:  # noqa: BLE001 - never block a proposal on this
            return None

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
            message=_format_proposal_message(proposal),
            symbol=proposal.symbol,
            interactive_actions=(
                ("✅ Approve", f"approve:{proposal.proposal_id}"),
                ("❌ Reject", f"reject:{proposal.proposal_id}"),
            ),
        )
        self._notified.add(("pending_approval", proposal.proposal_id))

    def _abandon_initial_entry_after_refusal(
        self, trade_id: str, proposal_id: str, *, reason: str, now: datetime
    ) -> None:
        """Mark an INITIAL_ENTRY trade ABANDONED when its approved
        submission is refused BEFORE any fill (D-0007 revalidation or
        D-0047 portfolio risk). Mirrors the REJECT-branch abandonment
        in _apply_decision so the symbol is freed on the next
        watchlist tick instead of staying permanently locked out in
        AWAITING_INITIAL_FILL."""

        trade_record = self._trade_repo.get(trade_id)
        if trade_record is None:
            return
        trade = trade_record.trade
        if trade.initial_order_reconciled:
            # Any fill already happened -- never abandon a trade that
            # has reached a reconciled state.
            return
        try:
            self._trade_repo.update(
                trade.abandon_before_fill(now=now),
                expected_revision=trade_record.revision,
                transition="abandoned_after_refusal",
                now=now,
            )
            self._notify(
                level=NotificationLevel.IMPORTANT,
                event="initial_entry_abandoned",
                message=(
                    f"Initial Entry {proposal_id} ({trade.symbol}, trade "
                    f"{trade_id}) marked ABANDONED after submission refusal "
                    f"({reason}). Symbol released for the next watchlist tick."
                ),
                symbol=trade.symbol,
            )
        except Exception as exc:  # noqa: BLE001
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="abandon_failed",
                message=(
                    f"Could not mark {trade_id} as ABANDONED after submission "
                    f"refusal for {proposal_id}: {exc}. Symbol may remain blocked "
                    f"until the DB is cleaned."
                ),
                symbol=trade.symbol,
            )

    def _submit_approved(
        self,
        proposal_id: str,
        *,
        current_price: float,
        active_floor_price: Optional[float],
        now: datetime,
        initial_entry_trade_id: Optional[str] = None,
    ) -> None:
        """Submission dispatch wrapper.

        `initial_entry_trade_id` -- when set, this is an INITIAL_ENTRY
        submission attempt (caller holds the trade_id) and a terminal
        pre-fill refusal (D-0007 revalidation EXPIRED/PRICE_DRIFT, or
        D-0047 portfolio risk) must abandon the Trade so the symbol is
        released on the next watchlist tick. Without this, the Trade
        stays in AWAITING_INITIAL_FILL forever and _check_watchlist's
        has_open_trade guard permanently locks the symbol out (same
        shape as the 2026-09-30 Bug #3 fix for the REJECT branch, now
        extended to the approve-then-refused branch -- 2026-10-01
        Finding #2)."""

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
            if initial_entry_trade_id is not None:
                self._abandon_initial_entry_after_refusal(
                    initial_entry_trade_id, proposal_id,
                    reason=f"D-0007 revalidation refused: {exc}", now=now,
                )
        except PortfolioRiskViolatedError as exc:
            self._notify(
                level=NotificationLevel.IMPORTANT,
                event="submission_risk_violated",
                message=f"Submission for {proposal_id} refused by D-0047 portfolio risk enforcer: {exc}",
            )
            if initial_entry_trade_id is not None:
                self._abandon_initial_entry_after_refusal(
                    initial_entry_trade_id, proposal_id,
                    reason=f"D-0047 portfolio risk refused: {exc}", now=now,
                )
        except BrokerSubmissionAmbiguousError as exc:
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="submission_ambiguous",
                message=f"Submission for {proposal_id} had an ambiguous outcome: {exc}. Will resolve via reconciliation.",
            )
        except BrokerCommunicationError as exc:
            # Network / transport failure talking to the broker: surface
            # the failure, keep the execution row in SUBMITTED_UNKNOWN so
            # reconciliation can resolve it, and never crash the main
            # loop.
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="broker_communication_error",
                message=f"Broker communication failure for {proposal_id}: {exc}. Reconciliation will resolve.",
            )
        except BrokerClientError as exc:
            # Definite broker-side rejection (HTTP 4xx/5xx that the
            # broker processed enough to reject) -- e.g. an invalid
            # limit price, an unknown symbol, or an account block.
            # Surface it, do NOT retry automatically, and keep the main
            # loop alive so other proposals can still be handled.
            # A real live example (2026-09-30): Alpaca rejected an
            # Initial Entry limit price with sub-penny precision --
            # AlpacaBrokerClient now formats the price to Alpaca's
            # SEC-compliant precision so this specific case no longer
            # happens, but other 4xx rejections remain possible and
            # must never crash the engine.
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="broker_rejected",
                message=f"Broker rejected submission for {proposal_id}: {exc}",
            )
        except Exception as exc:  # noqa: BLE001 - engine main-loop shield
            # Defensive final catch: any other error from the execution
            # path (e.g. an ExecutionServiceError subclass we did not
            # anticipate, or a bug during a repo write) must surface as
            # a CRITICAL notification, never as a main-loop crash.
            # Without this, a single misbehaving proposal takes down
            # the whole session and every other symbol along with it
            # -- a real live 2026-09-30 failure mode.
            self._notify(
                level=NotificationLevel.CRITICAL,
                event="submission_unexpected_error",
                message=(
                    f"Unexpected error submitting {proposal_id} "
                    f"({type(exc).__name__}): {exc}. Engine keeps running; "
                    f"reconciliation will retry as appropriate."
                ),
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
        interactive_actions: tuple = (),
    ) -> None:
        dedup_key = (kind, key)
        if dedup_key in self._notified:
            return
        self._notified.add(dedup_key)
        self._notify(
            level=level, event=event, message=message, symbol=symbol,
            interactive_actions=interactive_actions,
        )

    def _notify(
        self, *, level: NotificationLevel, event: str, message: str,
        symbol: Optional[str] = None, interactive_actions: tuple = (),
    ) -> None:
        self._notifier.send(NotificationEvent(
            level=level, event=event, message=message, symbol=symbol,
            interactive_actions=interactive_actions,
        ))

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
