"""ProposalRepository -- persistence abstraction for TradeProposal.

Mirrors `src/d0026/repository.py`'s `SnapshotRepository` pattern: an
ABC plus an in-memory-only concrete implementation. No SQLite (or any
other real backend) is wired up here -- per Controller-authorized Phase
A scope, that wiring is a later phase, exactly as
`InMemorySnapshotRepository`'s own docstring already establishes as
precedent in this codebase ("D-0024's SQLite-backed implementation is
a Phase B/E concern, not Phase A").

ARCHITECTURE (Controller-approved refactor, this session): the Option E
save/supersession orchestration and the record_decision precondition are
implemented as pure, storage-agnostic functions (`plan_save_supersession`,
`plan_decision`) below -- no I/O, no SQL, no repository/database
dependency. `InMemoryProposalRepository` calls these functions rather
than re-deriving the logic inline; a future SQLite-backed repository
must do the same, so the business rule can only ever exist in ONE place
regardless of how many storage backends exist. Each concrete repository
is responsible only for fetching the (trade_id, proposed_action)-scoped
sibling list and executing whatever decision the pure function returns
-- it must never re-decide the rule itself.

Immutability is enforced the same way `approval.py`'s
`InMemoryApprovalRepository` enforces it: re-saving byte-identical
content is a no-op; saving conflicting content for an existing
proposal_id raises.

Controller-approved lifecycle rule (supersedes an earlier, rejected
design that expired BOTH PENDING and APPROVED siblings unconditionally):
a new proposal attempt for the same (trade_id, proposed_action)
- silently supersedes a still-PENDING sibling (no Controller decision
  was ever at stake in a PENDING record, so this is safe), but
- is BLOCKED outright (nothing is persisted) if an APPROVED sibling is
  still D-0007-valid -- an explicit Controller approval is never
  silently revoked merely because a newer proposal appears, and
- is ALLOWED, leaving the stale APPROVED sibling's record completely
  untouched (never auto-EXPIRED), if that sibling has independently
  failed D-0007 (age or price). D-0007 validity is always DERIVED via
  validate_for_submission(), never cached or duplicated here.

KNOWN, DELIBERATE BOUNDARY -- fill/execution state is out of scope:
this repository can only ever know whether the CONTROLLER approved an
action and whether that approval is still D-0007-valid. It has no
access to real fills or position state (that lives in a separate,
not-yet-built schema -- see docs/architecture/state-management.md's
`ladder1_filled`/`ladder2_filled` fields) and so it CANNOT guarantee
"never re-propose an action that has already been successfully
executed." That guarantee must be enforced by a future orchestration
layer that consults real fill/position state before ever calling
build_trade_proposal() again -- this repository must never fabricate
or infer that knowledge.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

from .models import ApprovalState, TradeAction, TradeProposal
from .revalidation import validate_for_submission


def _trigger_price_for_action(proposal: TradeProposal, action: TradeAction) -> float:
    """Pure field lookup: which trigger price on `proposal` corresponds
    to `action`. Contains NO D-0007 business rules -- those live
    entirely in validate_for_submission(), the single source of truth
    this module reuses rather than duplicates."""

    if action == TradeAction.INITIAL_ENTRY:
        return proposal.proposed_entry
    if action == TradeAction.LADDER_1:
        return proposal.ladder_1_trigger
    if action == TradeAction.LADDER_2:
        return proposal.ladder_2_trigger
    raise ValueError(f"no trigger price mapping for action {action!r}")


class ProposalDecisionConflictError(RuntimeError):
    """Raised when attempting to create a proposal_id that already
    exists, or to record a decision against a proposal that is not
    currently PENDING. A recorded decision is immutable -- a changed
    mind requires a new proposal, never an overwrite of a decided
    record."""


def plan_save_supersession(siblings: Sequence[TradeProposal], new_proposal: TradeProposal) -> Tuple[str, ...]:
    """PURE, storage-agnostic Option E decision function -- no I/O, no
    SQL, no repository/database dependency. Every concrete
    ProposalRepository's `save()` must call this exact function rather
    than re-deriving the rule; this is the ONLY place the rule may be
    expressed.

    `siblings` must already be scoped by the CALLER (a storage-layer
    concern: "fetch every proposal with this trade_id and
    proposed_action") to exactly `new_proposal`'s (trade_id,
    proposed_action) -- this function performs no filtering of its own,
    only decides what happens given an already-scoped sibling list.

    Returns the proposal_ids of PENDING siblings that the caller must
    supersede (via `TradeProposal.expire()`) before persisting
    `new_proposal` -- no Controller decision is ever at stake in a
    PENDING record, so silently superseding it is safe.

    Raises ProposalDecisionConflictError, persisting NOTHING, if any
    sibling is APPROVED and still D-0007-valid (per
    validate_for_submission(), the single D-0007 authority, never
    duplicated here) -- an explicit Controller approval is never
    silently revoked merely because a newer proposal appears. An
    APPROVED sibling that has independently failed D-0007 is left
    completely out of the returned tuple -- it is NEVER auto-expired,
    even though it is stale; Phase A never fabricates an EXPIRED
    transition for an approval nobody has acted on. REJECTED and
    already-EXPIRED siblings never block and are never returned."""

    if not math.isfinite(new_proposal.current_price_at_proposal) or new_proposal.current_price_at_proposal <= 0:
        raise ValueError(
            f"new_proposal.current_price_at_proposal {new_proposal.current_price_at_proposal!r} is not a "
            "valid positive finite price -- refusing to evaluate sibling D-0007 validity against it"
        )

    for existing in siblings:
        if existing.approval_state == ApprovalState.APPROVED:
            result = validate_for_submission(
                existing,
                trigger_price=_trigger_price_for_action(existing, existing.approved_action),
                current_price=new_proposal.current_price_at_proposal,
                active_floor_price=new_proposal.active_floor_at_proposal,
                now=new_proposal.proposal_created_at,
                action=existing.approved_action,
            )
            if result.allowed:
                raise ProposalDecisionConflictError(
                    f"proposal {existing.proposal_id!r} is APPROVED and still D-0007-valid "
                    f"for (trade_id={new_proposal.trade_id!r}, action={new_proposal.proposed_action.value!r}) "
                    "-- a valid Controller approval is never superseded by a newer proposal; "
                    "no new proposal was persisted"
                )

    return tuple(existing.proposal_id for existing in siblings if existing.approval_state == ApprovalState.PENDING)


def plan_decision(existing: Optional[TradeProposal], proposal_id: str) -> TradeProposal:
    """PURE, storage-agnostic record_decision precondition -- no I/O.
    `existing` is whatever the CALLER already fetched for `proposal_id`
    (or None if not found) -- this function performs no lookup of its
    own. Returns `existing` unchanged if it may legitimately receive a
    decision (the caller then calls `.with_decision(...)` and persists
    the result); raises ProposalDecisionConflictError otherwise (not
    found, or already decided -- a decision may only be recorded once
    per proposal)."""

    if existing is None:
        raise ProposalDecisionConflictError(f"no proposal found for id {proposal_id!r}")
    if existing.approval_state != ApprovalState.PENDING:
        raise ProposalDecisionConflictError(
            f"proposal {proposal_id!r} is already "
            f"{existing.approval_state.value!r} -- a decision may only "
            "be recorded once per proposal"
        )
    return existing


class ProposalRepository(ABC):
    @abstractmethod
    def save(self, proposal: TradeProposal) -> None:
        """Persists a NEW proposal. Must raise
        ProposalDecisionConflictError if proposal_id already exists.

        Controller-approved lifecycle rule (see module docstring for
        full rationale): an existing PENDING sibling sharing this
        proposal's (trade_id, proposed_action) is automatically
        superseded/EXPIRED. An existing APPROVED sibling is left
        completely untouched -- if it is still D-0007-valid (per
        validate_for_submission()), this save is BLOCKED entirely
        (nothing persisted); if it has independently failed D-0007, this
        save proceeds and the stale APPROVED sibling is left recorded as
        APPROVED, never auto-transitioned to EXPIRED. REJECTED and
        already-EXPIRED siblings never block and are never touched."""
        raise NotImplementedError

    @abstractmethod
    def record_decision(
        self,
        proposal_id: str,
        *,
        approved: bool,
        decided_by: str,
        decided_at: datetime,
        action: TradeAction,
    ) -> TradeProposal:
        """The ONLY supported way to move a stored proposal out of
        PENDING. Raises ProposalDecisionConflictError if the proposal
        does not exist or is not currently PENDING. `action` is
        required -- it ties the recorded decision to exactly one of
        INITIAL_ENTRY, LADDER_1, or LADDER_2."""
        raise NotImplementedError

    @abstractmethod
    def get(self, proposal_id: str) -> Optional[TradeProposal]:
        raise NotImplementedError

    @abstractmethod
    def list_for_symbol(self, symbol: str) -> List[TradeProposal]:
        raise NotImplementedError

    @abstractmethod
    def list_for_trade(self, trade_id: str) -> List[TradeProposal]:
        """Every proposal attempt (any action, any outcome -- pending,
        approved, rejected) recorded for this trade_id, in creation
        order. Nothing is filtered out or collapsed -- a rejected
        attempt remains in this list even after a later attempt for the
        same action is approved."""
        raise NotImplementedError


class InMemoryProposalRepository(ProposalRepository):
    """Test/scaffolding-only implementation. Holds proposals in a plain
    dict for the lifetime of the process; touches no file, database, or
    network. Not intended, and not wired, for production use."""

    def __init__(self) -> None:
        self._by_id: Dict[str, TradeProposal] = {}

    def save(self, proposal: TradeProposal) -> None:
        if proposal.proposal_id in self._by_id:
            raise ProposalDecisionConflictError(
                f"proposal_id {proposal.proposal_id!r} already exists -- "
                "save() creates a new proposal only, use record_decision() "
                "to transition an existing one"
            )

        # Storage-layer concern only: fetch the (trade_id, proposed_action)
        # -scoped sibling list. The actual decision (block / supersede
        # which ids / proceed) is made entirely by the pure
        # plan_save_supersession() function -- never re-derived here.
        siblings = [
            existing
            for existing in self._by_id.values()
            if existing.trade_id == proposal.trade_id and existing.proposed_action == proposal.proposed_action
        ]
        expire_ids = plan_save_supersession(siblings, proposal)

        for proposal_id in expire_ids:
            existing = self._by_id[proposal_id]
            self._by_id[proposal_id] = existing.expire(expired_at=proposal.proposal_created_at)

        self._by_id[proposal.proposal_id] = proposal

    def record_decision(
        self,
        proposal_id: str,
        *,
        approved: bool,
        decided_by: str,
        decided_at: datetime,
        action: TradeAction,
    ) -> TradeProposal:
        # Storage-layer concern only: fetch the current stored proposal.
        # The precondition check is made entirely by the pure
        # plan_decision() function -- never re-derived here.
        existing = plan_decision(self._by_id.get(proposal_id), proposal_id)
        decided = existing.with_decision(
            approved=approved, decided_by=decided_by, decided_at=decided_at, action=action
        )
        self._by_id[proposal_id] = decided
        return decided

    def get(self, proposal_id: str) -> Optional[TradeProposal]:
        return self._by_id.get(proposal_id)

    def list_for_symbol(self, symbol: str) -> List[TradeProposal]:
        symbol_norm = symbol.strip().upper()
        return [p for p in self._by_id.values() if p.symbol == symbol_norm]

    def list_for_trade(self, trade_id: str) -> List[TradeProposal]:
        return [p for p in self._by_id.values() if p.trade_id == trade_id]
