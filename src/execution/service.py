"""ExecutionService -- orchestrates submission-initiation and
restart/reconciliation for approved Proposals, calling into existing,
unchanged authorities exactly (never re-deriving D-0007, never
duplicating Trade/Proposal invariants, never interpreting broker
vocabulary itself).

Two public operations only:

    submit_approved_proposal(proposal_id, ...)  -- initiates or resumes
        submission of one approved, D-0007-valid Proposal.
    reconcile_unresolved(...)                    -- restart recovery:
        queries the broker for every unresolved OrderExecution and
        records what is known. NEVER calls the broker's submit
        operation -- see reconcile_unresolved()'s own docstring for why.

Governance boundary: this module has no knowledge of Alpaca or any
broker SDK (it depends only on the broker-agnostic `BrokerClient`
interface). It never re-implements Option E, D-0007, or any Trade
invariant -- it only sequences calls into `ProposalRepository`,
`TradeRepository`, `OrderExecutionRepository`, and `validate_for_submission()`,
exactly as `TradeProposalService` already does for the earlier phase of
this same lifecycle.

Ladder execution range (Controller-approved): Ladder 1/Ladder 2 are
submitted as LIMIT orders at the upper boundary of a 1-percentage-point
execution range beyond the strategy trigger (e.g. Ladder 1 trigger
-5% -> limit price at -4%; Ladder 2 trigger -8% -> limit price at
-7%), never at the raw trigger price itself. D-0007's own trigger
price (used only for the price-band/floor-priority check) is
completely unaffected -- it is a separate, unchanged value. Initial
Entry is unaffected -- it still submits at proposal.proposed_entry.

Ladder 2 partial fill (Controller-approved, Ladder 2 ONLY -- never
extended to Ladder 1 or Initial Entry): a live, still-open Ladder 2
execution with a non-terminal partial fill triggers an immediate
cancellation request for the remainder (reactive, never predicting
liquidity) -- see BrokerClient.cancel_order(). The fill quantity
observed at that moment is never treated as final; only a
subsequently-confirmed TERMINAL filled_qty is authoritative. A
genuinely terminal partial fill is never auto-applied to Trade -- see
confirm_ladder2_partial_fill(), the only path that may ever record it,
requiring an explicit Controller-approved call. A terminal FULL or
ZERO fill is still applied automatically, exactly as before (no
approval needed for either). Ladder 1's terminal partial fills remain
unrepresented in Trade (Trade is simply never touched for that case) --
no confirmation path exists for Ladder 1, by design, pending a
separate, future Controller decision.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import List, Optional

logger = logging.getLogger(__name__)

from proposals.models import ApprovalState, TradeAction, TradeProposal, approved_strategy_rule_set
from proposals.repository import ProposalRepository
from proposals.revalidation import validate_for_submission
from trade.models import InitialOrderStatus
from trade.repository import TradeRepository

from .broker_client import BrokerClient
from .models import CREATED, OrderExecution
from .repository import OrderExecutionRecord, OrderExecutionRepository


class ExecutionServiceError(RuntimeError):
    """Base class for ExecutionService errors."""


class ProposalNotFoundError(ExecutionServiceError):
    """Raised by submit_approved_proposal() when no Proposal exists for
    the given proposal_id."""


class ProposalNotApprovedError(ExecutionServiceError):
    """Raised when the Proposal is not currently APPROVED. Submission
    may only ever be initiated for an approved Proposal -- Proposal's
    own approval_state remains the sole authority for this fact."""


class SubmissionNotAllowedError(ExecutionServiceError):
    """Raised when D-0007 revalidation (validate_for_submission(),
    never re-derived here) refuses submission -- carries that
    function's own reason string unchanged."""


class ExecutionAlreadyResolvedError(ExecutionServiceError):
    """Raised when the Proposal's OrderExecution has already reached a
    terminal broker outcome -- nothing left to submit."""


class ExecutionAlreadySubmittedError(ExecutionServiceError):
    """Raised when the Proposal's OrderExecution already has a known,
    live broker_order_id -- there is nothing to (re)submit; only
    reconcile_unresolved() polls a live order forward."""


class Ladder2PartialFillNotPendingError(ExecutionServiceError):
    """Raised by confirm_ladder2_partial_fill() when there is no
    genuine, terminal, unconfirmed partial fill pending for this
    proposal -- e.g. the proposal is not LADDER_2, no execution exists,
    the execution is not yet terminal, the fill is not actually partial
    (zero or full), or Ladder 2 is already marked filled on Trade
    (already confirmed once -- confirmation is not repeatable)."""


def _generate_id() -> str:
    """Provisional, implementation-specific placeholder. The exact
    format (e.g. length/character-set constraints for a real broker)
    remains explicitly deferred, per the Controller-approved
    identifier-generation design review -- only the OWNERSHIP (this
    module, at OrderExecution-creation time, never regenerated on
    retry) was resolved by that review."""
    return uuid.uuid4().hex


def _trigger_price_for(proposal: TradeProposal) -> float:
    """The D-0007 trigger price -- the raw strategy trigger (-5%/-8%/
    proposed_entry), used ONLY for D-0007's price-band/floor-priority
    revalidation. Never used as the broker limit price for a ladder --
    see _execution_limit_price_for() for that, a deliberately separate
    value. Unaffected by the Controller-approved execution-range
    decision."""
    if proposal.proposed_action is TradeAction.INITIAL_ENTRY:
        return proposal.proposed_entry
    if proposal.proposed_action is TradeAction.LADDER_1:
        return proposal.ladder_1_trigger
    return proposal.ladder_2_trigger


LADDER_EXECUTION_RANGE_FRACTION = 0.01
"""Controller-approved execution mechanics (not a strategy trigger
level): a ladder's BUY limit is placed 1 percentage point above its
own trigger (e.g. -5% trigger -> -4% limit; -8% trigger -> -7% limit),
giving the order some real room to fill without ever paying worse than
that boundary. Deliberately NOT in proposals/models.py -- Proposal
stays untouched; this is purely an ExecutionService/broker-submission
concern, exactly like D-0007's own PRICE_BAND_FRACTION is a
revalidation concern, not a Proposal concern."""


def _execution_limit_price_for(proposal: TradeProposal) -> float:
    """The actual BUY LIMIT price sent to the broker for LADDER_1/
    LADDER_2 -- the upper boundary of the Controller-approved 1-point
    execution range, computed from the SAME base price the trigger
    itself was computed from (back-derived from the already-stored
    trigger, since TradeProposal does not separately store that base
    price) so the D-0007 trigger and the execution-range limit price
    never drift from inconsistent reference prices. INITIAL_ENTRY is
    unaffected -- returns proposed_entry unchanged, exactly as
    _trigger_price_for() does; no execution range has been approved
    for Initial Entry."""
    if proposal.proposed_action is TradeAction.INITIAL_ENTRY:
        return proposal.proposed_entry

    strategy = approved_strategy_rule_set()
    if proposal.proposed_action is TradeAction.LADDER_1:
        trigger_price = proposal.ladder_1_trigger
        ladder_pct = strategy.ladder_1_pct
    else:
        trigger_price = proposal.ladder_2_trigger
        ladder_pct = strategy.ladder_2_pct

    base_price = trigger_price / (1 + ladder_pct)
    return round(base_price * (1 + ladder_pct + LADDER_EXECUTION_RANGE_FRACTION), 4)


def _requested_qty_for(proposal: TradeProposal, strategy) -> int:
    """INITIAL_ENTRY quantity is not stored on TradeProposal (a real,
    surfaced data-completeness gap -- see module docstring/design
    review) -- resolved by reading the already-approved, frozen
    StrategyRuleSet directly, exactly as build_trade_proposal() itself
    already does. Ladder quantities are read from the Proposal's own
    stored fields (the historically-approved record), not re-derived
    from the current strategy."""
    if proposal.proposed_action is TradeAction.INITIAL_ENTRY:
        return strategy.initial_qty
    if proposal.proposed_action is TradeAction.LADDER_1:
        return proposal.ladder_1_quantity
    return proposal.ladder_2_quantity


class ExecutionService:
    def __init__(
        self,
        execution_repo: OrderExecutionRepository,
        proposal_repo: ProposalRepository,
        trade_repo: TradeRepository,
        broker: BrokerClient,
    ) -> None:
        self._execution_repo = execution_repo
        self._proposal_repo = proposal_repo
        self._trade_repo = trade_repo
        self._broker = broker

    def submit_approved_proposal(
        self,
        proposal_id: str,
        *,
        current_price: float,
        active_floor_price: Optional[float],
        now: datetime,
    ) -> OrderExecutionRecord:
        """Initiates (or resumes) submission of one approved Proposal.
        D-0007 is ALWAYS re-validated fresh here, using the caller-supplied
        current_price/active_floor_price, before any broker call --
        whether this is a brand-new attempt or a resume of an execution
        already confirmed never to have reached the broker. This is the
        ONLY method in this service that ever calls the broker's submit
        operation."""

        proposal = self._proposal_repo.get(proposal_id)
        if proposal is None:
            raise ProposalNotFoundError(f"no proposal found for id {proposal_id!r}")
        if proposal.approval_state != ApprovalState.APPROVED:
            raise ProposalNotApprovedError(
                f"proposal {proposal_id!r} is {proposal.approval_state.value!r}, not approved"
            )

        existing = self._execution_repo.get_by_proposal_id(proposal_id)

        if existing is not None:
            execution = existing.execution
            if execution.is_broker_terminal:
                raise ExecutionAlreadyResolvedError(
                    f"proposal {proposal_id!r} already has a resolved execution "
                    f"({execution.execution_id!r}, status={execution.status!r})"
                )
            if execution.broker_order_id is not None:
                raise ExecutionAlreadySubmittedError(
                    f"proposal {proposal_id!r} already has a live broker order "
                    f"({execution.broker_order_id!r}) -- nothing to (re)submit"
                )
            if execution.status != CREATED:
                # A prior attempt may have reached the broker without us
                # knowing -- check BEFORE deciding anything else,
                # regardless of D-0007: we must never resubmit if the
                # broker already has it, no matter what D-0007 says.
                broker_state = self._broker.get_order_by_client_order_id(execution.client_order_id)
                if broker_state is not None:
                    updated = execution.record_broker_response(
                        broker_order_id=broker_state.broker_order_id,
                        status=broker_state.status,
                        is_terminal=broker_state.is_terminal,
                        now=now,
                        filled_qty=broker_state.filled_qty,
                        filled_avg_price=broker_state.filled_avg_price,
                    )
                    new_record = self._execution_repo.update(
                        updated,
                        expected_revision=existing.revision,
                        transition="resume_found_at_broker",
                        now=now,
                    )
                    self._maybe_cancel_ladder2_remainder(new_record, proposal)
                    self._apply_to_trade_if_terminal(new_record, proposal, now=now)
                    return new_record
                # confirmed: the broker never received the prior attempt.

        strategy = approved_strategy_rule_set()
        trigger_price = _trigger_price_for(proposal)
        result = validate_for_submission(
            proposal,
            trigger_price=trigger_price,
            current_price=current_price,
            active_floor_price=active_floor_price,
            now=now,
            action=proposal.approved_action,
        )
        if not result.allowed:
            raise SubmissionNotAllowedError(result.reason)

        if existing is None:
            new_execution = OrderExecution(
                proposal_id=proposal_id,
                execution_id=_generate_id(),
                trade_id=proposal.trade_id,
                client_order_id=_generate_id(),
                side="buy",
                requested_qty=_requested_qty_for(proposal, strategy),
                created_at=now,
            )
            record = self._execution_repo.save(new_execution, now=now)
        else:
            record = existing

        return self._do_submit(record, proposal, now=now)

    def _do_submit(
        self, record: OrderExecutionRecord, proposal: TradeProposal, *, now: datetime
    ) -> OrderExecutionRecord:
        execution = record.execution
        if execution.status == CREATED:
            submitting = execution.start_submission(now=now)
            record = self._execution_repo.update(
                submitting, expected_revision=record.revision, transition="submitting", now=now
            )
        execution = record.execution

        try:
            broker_state = self._broker.submit_order(
                client_order_id=execution.client_order_id,
                symbol=proposal.symbol,
                side="buy",
                quantity=execution.requested_qty,
                limit_price=_execution_limit_price_for(proposal),
            )
        except Exception:
            unknown = execution.mark_submission_unknown(now=now)
            self._execution_repo.update(
                unknown, expected_revision=record.revision, transition="submission_unknown", now=now
            )
            raise

        acked = execution.record_broker_response(
            broker_order_id=broker_state.broker_order_id,
            status=broker_state.status,
            is_terminal=broker_state.is_terminal,
            now=now,
            filled_qty=broker_state.filled_qty,
            filled_avg_price=broker_state.filled_avg_price,
        )
        new_record = self._execution_repo.update(
            acked, expected_revision=record.revision, transition="acknowledged", now=now
        )
        self._maybe_cancel_ladder2_remainder(new_record, proposal)
        self._apply_to_trade_if_terminal(new_record, proposal, now=now)
        return new_record

    def _maybe_cancel_ladder2_remainder(self, record: OrderExecutionRecord, proposal: TradeProposal) -> None:
        """Controller-approved, LADDER_2-ONLY: the moment a live,
        still-open Ladder 2 order shows ANY fill (0 < filled_qty <
        requested_qty), immediately request cancellation of the
        remainder -- reactive to an observed fact, never a prediction
        of market liquidity. Never extended to Ladder 1 or Initial
        Entry. Safe/idempotent to call repeatedly: cancelling an
        already-cancelled or already-terminal order is a harmless
        no-op per BrokerClient.cancel_order()'s own contract. A failed
        or ambiguous cancellation attempt is never guessed at -- it is
        simply retried on a later reconciliation pass, and must never
        abort processing of this or any other execution."""

        execution = record.execution
        if execution.side != "buy" or proposal is None:
            return  # SELL (Floor) executions never go through this ladder-only mechanism
        if execution.is_broker_terminal:
            return
        if proposal.proposed_action is not TradeAction.LADDER_2:
            return
        if not (0 < execution.filled_qty < execution.requested_qty):
            return
        try:
            self._broker.cancel_order(execution.client_order_id)
        except Exception:
            logger.warning(
                "cancel_order failed for execution %s (proposal %s, client_order_id %s) -- "
                "will be retried on a later reconciliation pass",
                execution.execution_id,
                execution.proposal_id,
                execution.client_order_id,
                exc_info=True,
            )

    def reconcile_unresolved(self, *, now: datetime) -> List[OrderExecutionRecord]:
        """Restart recovery: queries the broker for every unresolved
        OrderExecution and records what is known. NEVER calls
        submit_order() -- resubmitting always requires a fresh D-0007
        check against current market data, which a generic sweep over
        every unresolved row does not have; that decision belongs
        exclusively to submit_approved_proposal(), called explicitly
        per-proposal by whatever caller has fresh price data.

        Each unresolved execution is reconciled INDEPENDENTLY. A failure
        reconciling any single execution -- the broker no longer
        recognizing a known client_order_id, a transient broker/API/
        network exception, or any other execution-specific error raised
        by _reconcile_one() -- is caught and logged here, and never
        aborts processing of the other unresolved executions in this
        same sweep. The failed execution is never silently dropped: its
        current persisted state (whatever _reconcile_one() actually
        wrote before failing, or its original state if nothing was
        written) is still included in the returned list. Since a caught
        failure never marks the execution resolved, it naturally remains
        in list_unresolved() and is retried on the next reconciliation
        pass -- no separate retry bookkeeping is introduced."""

        results: List[OrderExecutionRecord] = []
        for record in self._execution_repo.list_unresolved():
            try:
                results.append(self._reconcile_one(record, now=now))
            except Exception:
                logger.exception(
                    "reconciliation failed for execution %s (proposal %s, client_order_id %s) -- "
                    "left unresolved, will be retried on a later reconciliation pass",
                    record.execution.execution_id,
                    record.execution.proposal_id,
                    record.execution.client_order_id,
                )
                current = self._execution_repo.get(record.execution.execution_id)
                results.append(current if current is not None else record)
        return results

    def _reconcile_one(self, record: OrderExecutionRecord, *, now: datetime) -> OrderExecutionRecord:
        execution = record.execution

        if execution.status == CREATED:
            # Never attempted -- reconciliation never initiates a first
            # submission; only submit_approved_proposal() does.
            return record

        proposal = self._proposal_repo.get(execution.proposal_id)
        broker_state = self._broker.get_order_by_client_order_id(execution.client_order_id)

        if broker_state is None:
            if execution.broker_order_id is None:
                if execution.status != "SUBMITTED_UNKNOWN":
                    unknown = execution.mark_submission_unknown(now=now)
                    return self._execution_repo.update(
                        unknown,
                        expected_revision=record.revision,
                        transition="reconcile_confirmed_unknown",
                        now=now,
                    )
                return record
            raise ExecutionServiceError(
                f"execution {execution.execution_id!r} has broker_order_id "
                f"{execution.broker_order_id!r} but the broker has no record of client_order_id "
                f"{execution.client_order_id!r} -- reconciliation refuses to guess"
            )

        if execution.broker_order_id is None:
            updated = execution.record_broker_response(
                broker_order_id=broker_state.broker_order_id,
                status=broker_state.status,
                is_terminal=broker_state.is_terminal,
                now=now,
                filled_qty=broker_state.filled_qty,
                filled_avg_price=broker_state.filled_avg_price,
            )
            transition = "reconciled_from_unknown"
        else:
            updated = execution.record_fill_update(
                filled_qty=broker_state.filled_qty,
                filled_avg_price=broker_state.filled_avg_price,
                status=broker_state.status,
                is_terminal=broker_state.is_terminal,
                now=now,
            )
            transition = "reconciled_poll"

        new_record = self._execution_repo.update(
            updated, expected_revision=record.revision, transition=transition, now=now
        )
        self._maybe_cancel_ladder2_remainder(new_record, proposal)
        self._apply_to_trade_if_terminal(new_record, proposal, now=now)
        return new_record

    def _apply_to_trade_if_terminal(
        self,
        record: OrderExecutionRecord,
        proposal: Optional[TradeProposal],
        *,
        now: datetime,
    ) -> None:
        execution = record.execution
        if not execution.is_broker_terminal:
            return

        if execution.side == "sell":
            trade_record = self._trade_repo.get(execution.trade_id)
            if trade_record is None:
                raise ExecutionServiceError(f"no trade found for id {execution.trade_id!r}")
            self._apply_protective_exit(trade_record, execution, now=now)
            return

        if proposal is None:
            proposal = self._proposal_repo.get(execution.proposal_id)

        trade_record = self._trade_repo.get(proposal.trade_id)
        if trade_record is None:
            raise ExecutionServiceError(f"no trade found for id {proposal.trade_id!r}")
        trade = trade_record.trade
        strategy = approved_strategy_rule_set()

        if proposal.proposed_action is TradeAction.INITIAL_ENTRY:
            if trade.initial_order_reconciled:
                return
            if execution.filled_qty == execution.requested_qty:
                order_status = InitialOrderStatus.FILLED
            else:
                # Zero or partial terminal fill -- D-0009/D-0010 already
                # supports both via the same "not a full fill" bucket;
                # the raw broker status string remains available on
                # OrderExecution itself for full audit detail.
                order_status = InitialOrderStatus.CANCELLED
            updated_trade = trade.freeze_initial_reference(
                order_status=order_status,
                filled_shares=execution.filled_qty,
                fill_price=execution.filled_avg_price,
                strategy=strategy,
                now=now,
            )
            self._trade_repo.update(
                updated_trade,
                expected_revision=trade_record.revision,
                transition="initial_entry_reconciled",
                now=now,
            )
            return

        action = proposal.proposed_action
        already_filled = trade.ladder1_filled if action is TradeAction.LADDER_1 else trade.ladder2_filled
        if already_filled:
            return

        if execution.filled_qty == 0:
            return

        if execution.filled_qty != execution.requested_qty:
            # Genuine partial ladder fill. Never auto-applied to Trade,
            # for EITHER ladder -- for LADDER_2 this is the
            # Controller-approved "awaiting explicit confirmation"
            # state (see confirm_ladder2_partial_fill()), fully
            # derivable right here (terminal + partial + not yet
            # ladderN_filled) with no new persistence. For LADDER_1, no
            # confirmation path exists (not approved) -- the fill
            # simply stays unrepresented in Trade pending a separate,
            # future Controller decision. Either way: never raise here
            # -- this runs inside a reconciliation sweep over
            # potentially many rows and must never abort processing of
            # any other one.
            return

        self._apply_ladder_fill(trade_record, action, execution, now=now, transition=f"{action.value}_reconciled")

    def _apply_ladder_fill(
        self,
        trade_record,
        action: TradeAction,
        execution: OrderExecution,
        *,
        now: datetime,
        transition: str,
    ):
        """Shared by the automatic full-fill path above and the
        explicit confirm_ladder2_partial_fill() below -- both ultimately
        record a fill onto Trade via the same, unmodified
        Trade.record_ladder_fill(); this helper exists only to avoid
        duplicating the weighted-average arithmetic in two places."""

        trade = trade_record.trade
        strategy = approved_strategy_rule_set()
        new_total_shares = trade.total_shares + execution.filled_qty
        if trade.weighted_avg_entry_price is None:
            new_weighted_avg = execution.filled_avg_price
        else:
            new_weighted_avg = (
                trade.total_shares * trade.weighted_avg_entry_price
                + execution.filled_qty * execution.filled_avg_price
            ) / new_total_shares

        updated_trade = trade.record_ladder_fill(
            action,
            order_id=execution.broker_order_id,
            fill_price=execution.filled_avg_price,
            fill_qty=execution.filled_qty,
            new_total_shares=new_total_shares,
            new_weighted_avg_entry_price=new_weighted_avg,
            strategy=strategy,
        )
        return self._trade_repo.update(
            updated_trade, expected_revision=trade_record.revision, transition=transition, now=now
        )

    def _apply_protective_exit(self, trade_record, execution: OrderExecution, *, now: datetime) -> None:
        """Floor terminal application -- SELL side only (Controller-
        approved: automatic, no approval gate, auto-applies even a
        partial fill). Idempotent by construction, with NO new "applied"
        flag anywhere: the target total_shares
        (execution.requested_qty - execution.filled_qty) is computed
        once from this execution's own immutable fields, never by
        subtracting execution.filled_qty from whatever total_shares
        happens to be right now. Re-applying an already-applied
        execution is a safe no-op, because Trade.total_shares will
        already be at or below that same target -- Trade.total_shares
        itself IS the idempotency signal here, exactly mirroring how
        ladder1_filled/ladder2_filled already serve that role for BUY
        executions. Relies on execution.requested_qty having been set
        to trade.total_shares at submission time (submit_protective_exit()'s
        own contract) -- see its docstring."""

        trade = trade_record.trade
        target_total_shares = execution.requested_qty - execution.filled_qty
        if trade.total_shares <= target_total_shares:
            return  # already applied, or nothing to apply (e.g. a zero fill)

        strategy = approved_strategy_rule_set()
        new_weighted_avg = trade.weighted_avg_entry_price if target_total_shares > 0 else None
        updated_trade = trade.reconcile_position(
            total_shares=target_total_shares,
            weighted_avg_entry_price=new_weighted_avg,
            strategy=strategy,
            now=now,
        )
        self._trade_repo.update(
            updated_trade,
            expected_revision=trade_record.revision,
            transition="protective_exit_reconciled",
            now=now,
        )

    def submit_protective_exit(
        self, trade_id: str, *, quantity: int, limit_price: float, now: datetime
    ) -> OrderExecutionRecord:
        """Floor SELL -- Controller-approved, proposal-independent,
        approval-free entry point (D-0007/Controller-approval
        revalidation never applies to Floor; TradeAction deliberately
        excludes FLOOR for the same reason). Deliberately SEPARATE from
        submit_approved_proposal() -- Floor has no TradeProposal to
        revalidate, so routing it through that entry point would
        misrepresent it as Controller-gated when it structurally isn't.

        `quantity` MUST be the caller's freshly-read trade.total_shares
        at the moment of this call (SELL ALL) -- this method does not
        re-derive it, but does refuse a quantity exceeding what the
        Trade currently holds. `limit_price` is caller-supplied (never
        invented here) -- still a Limit Order per the approved "no
        Market Orders" constraint; the exact Floor limit-price formula
        is the caller's (Engine's) decision, not this service's.

        Duplicate-prevention: refuses if a live (non-terminal)
        protective-exit execution already exists for this trade -- a
        partial fill that still leaves shares to sell is handled by
        calling this again, with the new remaining quantity, on a
        LATER cycle -- never automatically from within this same call
        (no automatic top-up/retry, same discipline as Ladder 2)."""

        trade_record = self._trade_repo.get(trade_id)
        if trade_record is None:
            raise ExecutionServiceError(f"no trade found for id {trade_id!r}")
        if quantity <= 0:
            raise ExecutionServiceError(f"quantity must be positive, got {quantity}")
        if quantity > trade_record.trade.total_shares:
            raise ExecutionServiceError(
                f"quantity {quantity} exceeds trade {trade_id!r}'s total_shares "
                f"{trade_record.trade.total_shares} -- cannot sell more than is currently held"
            )

        existing = self._execution_repo.get_by_trade_id_and_side(trade_id, "sell")
        if existing is not None and not existing.execution.is_broker_terminal:
            raise ExecutionAlreadySubmittedError(
                f"trade {trade_id!r} already has a live protective-exit execution "
                f"({existing.execution.execution_id!r}) -- nothing to (re)submit"
            )

        new_execution = OrderExecution(
            proposal_id=None,
            execution_id=_generate_id(),
            trade_id=trade_id,
            client_order_id=_generate_id(),
            side="sell",
            requested_qty=quantity,
            created_at=now,
        )
        record = self._execution_repo.save(new_execution, now=now)
        return self._do_submit_sell(record, trade_record.trade.symbol, limit_price, now=now)

    def _do_submit_sell(
        self, record: OrderExecutionRecord, symbol: str, limit_price: float, *, now: datetime
    ) -> OrderExecutionRecord:
        execution = record.execution
        if execution.status == CREATED:
            submitting = execution.start_submission(now=now)
            record = self._execution_repo.update(
                submitting, expected_revision=record.revision, transition="submitting", now=now
            )
        execution = record.execution

        try:
            broker_state = self._broker.submit_order(
                client_order_id=execution.client_order_id,
                symbol=symbol,
                side="sell",
                quantity=execution.requested_qty,
                limit_price=limit_price,
            )
        except Exception:
            unknown = execution.mark_submission_unknown(now=now)
            self._execution_repo.update(
                unknown, expected_revision=record.revision, transition="submission_unknown", now=now
            )
            raise

        acked = execution.record_broker_response(
            broker_order_id=broker_state.broker_order_id,
            status=broker_state.status,
            is_terminal=broker_state.is_terminal,
            now=now,
            filled_qty=broker_state.filled_qty,
            filled_avg_price=broker_state.filled_avg_price,
        )
        new_record = self._execution_repo.update(
            acked, expected_revision=record.revision, transition="acknowledged", now=now
        )
        self._apply_to_trade_if_terminal(new_record, None, now=now)
        return new_record

    def confirm_ladder2_partial_fill(self, proposal_id: str, *, decided_by: str, now: datetime):
        """The ONLY path that may ever record a Ladder 2 PARTIAL fill
        onto Trade. Must be called explicitly, only after the
        Controller has approved accepting the broker's final, confirmed
        (terminal) reduced fill quantity -- never automatically. Scoped
        to LADDER_2 ONLY; refuses for any other action. Not repeatable:
        once Ladder 2 is marked filled, a second call refuses. Does not
        submit anything -- the remaining, unfilled quantity is
        permanently forfeited for this Ladder 2 event, exactly per the
        approved design (no automatic top-up)."""

        proposal = self._proposal_repo.get(proposal_id)
        if proposal is None:
            raise ProposalNotFoundError(f"no proposal found for id {proposal_id!r}")
        if proposal.proposed_action is not TradeAction.LADDER_2:
            raise Ladder2PartialFillNotPendingError(
                f"proposal {proposal_id!r} is {proposal.proposed_action.value!r}, not LADDER_2 -- "
                "this confirmation path is scoped to Ladder 2 only"
            )

        execution_record = self._execution_repo.get_by_proposal_id(proposal_id)
        if execution_record is None:
            raise Ladder2PartialFillNotPendingError(f"no execution found for proposal {proposal_id!r}")
        execution = execution_record.execution
        if not execution.is_broker_terminal:
            raise Ladder2PartialFillNotPendingError(
                f"execution {execution.execution_id!r} is not yet terminal -- nothing to confirm"
            )
        if execution.filled_qty == 0 or execution.filled_qty == execution.requested_qty:
            raise Ladder2PartialFillNotPendingError(
                f"execution {execution.execution_id!r} is not a partial fill "
                f"(filled_qty={execution.filled_qty}, requested_qty={execution.requested_qty})"
            )

        trade_record = self._trade_repo.get(proposal.trade_id)
        if trade_record is None:
            raise ExecutionServiceError(f"no trade found for id {proposal.trade_id!r}")
        if trade_record.trade.ladder2_filled:
            raise Ladder2PartialFillNotPendingError(
                f"trade {proposal.trade_id!r} Ladder 2 is already marked filled -- already confirmed"
            )

        return self._apply_ladder_fill(
            trade_record,
            TradeAction.LADDER_2,
            execution,
            now=now,
            transition=f"ladder_2_partial_fill_confirmed_by_{decided_by}",
        )

    def recover_if_terminal(self, proposal_id: str, *, now: datetime) -> Optional[OrderExecutionRecord]:
        """Thin, idempotent public wrapper around _apply_to_trade_if_terminal()
        (Controller-approved Engine design review, this session):
        Engine startup recovery calls this for a proposal whose
        execution may have reached a terminal broker outcome without
        its Trade-side effect (or notification) ever completing --
        e.g. the process crashed between the execution being recorded
        terminal and _apply_to_trade_if_terminal() running.

        Adds NO new logic: it is the exact same idempotent function
        already called from _do_submit()/_reconcile_one(), whose own
        guards (trade.initial_order_reconciled / ladder1_filled /
        ladder2_filled checks, plus Trade.record_ladder_fill()'s own
        refusal to fill twice) already make calling it again on an
        already-applied execution a safe no-op. This is exactly why no
        new "Trade effect applied" persistence flag is needed: Trade's
        own fields already are that flag.

        Returns None if no execution exists yet for this proposal
        (nothing to recover). Returns the execution's current record
        otherwise, whether or not this call actually changed Trade."""

        record = self._execution_repo.get_by_proposal_id(proposal_id)
        if record is None:
            return None
        proposal = self._proposal_repo.get(proposal_id)
        self._apply_to_trade_if_terminal(record, proposal, now=now)
        return record

    def recover_protective_exit_if_terminal(self, trade_id: str, *, now: datetime) -> Optional[OrderExecutionRecord]:
        """The SELL-side counterpart to recover_if_terminal() -- a
        protective-exit (Floor) execution has no proposal_id to look
        it up by, so it needs its own lookup, by trade_id/side. Same
        idempotency guarantee, via the same mechanism: re-applying an
        already-applied execution is a safe no-op because
        _apply_protective_exit()'s target-total_shares comparison
        already handles it. Returns None if no protective-exit
        execution exists yet for this trade."""

        record = self._execution_repo.get_by_trade_id_and_side(trade_id, "sell")
        if record is None:
            return None
        self._apply_to_trade_if_terminal(record, None, now=now)
        return record
