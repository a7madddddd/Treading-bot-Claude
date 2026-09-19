"""TradeProposalService -- the thin Trade<->Proposal orchestration
application service (Controller-approved architecture review, this
session).

Responsible ONLY for sequencing calls into existing, unchanged
authorities:

    Trade            -- Trade invariants and state (src/trade/models.py)
    TradeRepository  -- Trade persistence (Trade-first FK ordering)
    build_trade_proposal / ProposalRepository -- proposal lifecycle,
        Option E supersession, and D-0007 revalidation (all entirely
        inside src/proposals/, never re-derived here)

This service owns NO business rule of its own. It never re-implements
Option E, D-0007, or Trade's status derivation -- it only decides WHEN
to call each existing authority and refuses to proceed when an
existing precondition (Trade must exist and be ACTIVE, a ladder must
not already be filled) isn't met.

Controller-approved scope for this step (Trade<->Proposal pointer
design review): `Trade.ladder1_proposal_id`/`ladder2_proposal_id`
remain reserved and UNUSED -- this service never writes them, never
calls `TradeRepository.update()`, and never creates a `trade_snapshots`
row. `propose_next_action()` persists a Proposal only; Trade is read
via `TradeRepository.get()` and never written. Proposal history
(`ProposalRepository.list_for_trade()`) remains the sole authority for
"what was proposed for this trade" -- no cached pointer duplicates it.

Explicitly out of scope (deferred, not built here): Alpaca, order
submission, execution, fill reconciliation, broker polling,
client_order_id, order_attempts, scheduler, Trading Engine, D-0011,
Telegram, any new state machine, any new persistence table, any schema
change, trade_id/proposal_id generation, and any restart-recovery
runtime.
"""

from __future__ import annotations

from datetime import datetime
from typing import Tuple

from proposals.models import FloorContext, StrategyRuleSet, TradeAction, TradeProposal
from proposals.proposal import build_trade_proposal
from proposals.repository import ProposalRepository
from trade.models import Trade, describe_status
from trade.repository import TradeRecord, TradeRepository

_LADDER_ACTIONS = (TradeAction.LADDER_1, TradeAction.LADDER_2)


class TradeProposalServiceError(RuntimeError):
    """Base class for TradeProposalService errors."""


class TradeNotFoundError(TradeProposalServiceError):
    """Raised by propose_next_action() when no Trade exists for the
    given trade_id. TradeRepository.get() itself only returns
    Optional[TradeRecord] -- this service translates a missing Trade
    into an explicit, clearly-typed failure rather than proceeding."""


class TradeNotActiveError(TradeProposalServiceError):
    """Raised by propose_next_action() when the Trade's derived status
    (describe_status()) is not "ACTIVE" -- covers both a Trade whose
    initial order is not yet reconciled (AWAITING_INITIAL_FILL, no
    position exists yet to ladder against) and a terminal Trade
    (ABANDONED/CLOSED). describe_status() is called, never
    re-derived."""


class LadderAlreadyFilledError(TradeProposalServiceError):
    """Raised by propose_next_action() when the requested ladder
    action's corresponding Trade field (ladder1_filled/ladder2_filled)
    is already True -- reads an existing Trade field, never
    re-implements any fill-tracking rule."""


class TradeProposalService:
    """Constructor-injected with the two existing repository
    abstractions only. Holds no other state, no SQL connection, no
    business rule of its own."""

    def __init__(self, trade_repo: TradeRepository, proposal_repo: ProposalRepository) -> None:
        self._trade_repo = trade_repo
        self._proposal_repo = proposal_repo

    def start_trade(
        self,
        *,
        trade_id: str,
        symbol: str,
        proposal_id: str,
        current_price: float,
        strategy: StrategyRuleSet,
        floor_context: FloorContext,
        now: datetime,
    ) -> Tuple[TradeRecord, TradeProposal]:
        """Creates a NEW Trade and its INITIAL_ENTRY proposal, in that
        order -- preserves the Controller-approved Trade-first foreign
        key ordering (a Proposal can never be persisted before the
        Trade it references exists).

        `trade_id`/`proposal_id` are caller-supplied -- this method
        introduces no id-generation scheme. No idempotency mechanism is
        introduced: TradeRepository.save() and ProposalRepository.save()
        already refuse a duplicate trade_id/proposal_id outright, which
        is sufficient at this pre-execution stage (no order is
        submitted anywhere in this call).

        If Trade persistence fails, nothing further happens and the
        exception propagates unchanged. If Proposal persistence fails
        AFTER the Trade was already persisted, that exception also
        propagates unchanged -- the Trade row is left persisted with no
        proposal yet, a valid, recoverable, non-corrupt intermediate
        state. This method builds no recovery machinery for that case;
        none is added here."""

        trade_record = self._trade_repo.save(
            Trade(trade_id=trade_id, symbol=symbol, created_at=now), now=now
        )

        proposal = build_trade_proposal(
            proposal_id=proposal_id,
            trade_id=trade_id,
            action=TradeAction.INITIAL_ENTRY,
            symbol=symbol,
            current_price=current_price,
            as_of=now,
            strategy=strategy,
            floor_context=floor_context,
        )
        self._proposal_repo.save(proposal)

        return trade_record, proposal

    def propose_next_action(
        self,
        *,
        trade_id: str,
        action: TradeAction,
        proposal_id: str,
        current_price: float,
        strategy: StrategyRuleSet,
        floor_context: FloorContext,
        now: datetime,
    ) -> TradeProposal:
        """Creates a Ladder 1 or Ladder 2 proposal for an existing,
        ACTIVE Trade whose corresponding ladder is not already filled.

        Persists the Proposal only -- this method never calls
        TradeRepository.update(), never changes Trade's revision, and
        never creates a trade_snapshots row (Controller-approved scope:
        ladder1_proposal_id/ladder2_proposal_id remain reserved and
        unused).

        All Option E supersession behavior (block on a still-valid
        APPROVED sibling, silently supersede a PENDING sibling, leave a
        D-0007-stale APPROVED sibling untouched) happens entirely
        inside ProposalRepository.save() -> plan_save_supersession() --
        this method calls it unchanged and does not pre-inspect sibling
        proposals or branch on their state itself. D-0007 is likewise
        never called directly here; it is only exercised, unchanged,
        inside that same existing supersession logic."""

        if action not in _LADDER_ACTIONS:
            raise ValueError(
                f"propose_next_action only accepts LADDER_1/LADDER_2, got {action!r} -- "
                "the initial entry proposal is created by start_trade()"
            )

        record = self._trade_repo.get(trade_id)
        if record is None:
            raise TradeNotFoundError(f"no trade found for trade_id {trade_id!r}")

        status = describe_status(record.trade)
        if status != "ACTIVE":
            raise TradeNotActiveError(
                f"trade {trade_id!r} is {status!r}, not ACTIVE -- a ladder proposal "
                "requires a reconciled, non-terminal position"
            )

        if action is TradeAction.LADDER_1 and record.trade.ladder1_filled:
            raise LadderAlreadyFilledError(f"trade {trade_id!r} Ladder 1 is already filled")
        if action is TradeAction.LADDER_2 and record.trade.ladder2_filled:
            raise LadderAlreadyFilledError(f"trade {trade_id!r} Ladder 2 is already filled")

        proposal = build_trade_proposal(
            proposal_id=proposal_id,
            trade_id=trade_id,
            action=action,
            symbol=record.trade.symbol,
            current_price=current_price,
            as_of=now,
            strategy=strategy,
            floor_context=floor_context,
            weighted_avg_entry_at_proposal=record.trade.weighted_avg_entry_price,
        )
        self._proposal_repo.save(proposal)

        return proposal
