"""Trade proposal and approval-state data contract.

Field set matches `docs/architecture/state-management.md` §1's
"Approval workflow state (per pending proposal)" section, plus the
frozen-strategy price levels from `docs/trading/strategy.md`. No field
here represents ranking, score, strategy-fit, regime, or liquidity
information -- Phase A's candidate source (a fixed watchlist) has no
legitimate basis to populate any of those, so they are deliberately
absent rather than fabricated.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Tuple

APPROVAL_WINDOW = timedelta(minutes=5)
"""D-0007: an approval is valid for submission only within this window
of its receipt, re-checked immediately before submission (§ revalidation.py)."""

PRICE_BAND_FRACTION = 0.005
"""D-0007: current price must stay within this fraction of the
proposal/trigger reference at re-validation time."""


class StrategyUnavailableError(RuntimeError):
    """Raised when a StrategyRuleSet cannot be constructed or does not
    match the exact approved values (docs/trading/strategy.md §1, §7).
    Per Controller governance: missing/invalid strategy must BLOCK, never
    fall back to a default or an inferred value."""


@dataclass(frozen=True)
class StrategyRuleSet:
    """Self-validating mirror of the approved rule table
    (docs/trading/strategy.md §1). This is NOT a second source of truth
    -- it is the same approved numbers this project already represents
    as code (the established pattern used throughout src/d0026/),
    wrapped so that "the strategy in use is exactly the approved one"
    becomes a real, constructible, testable assertion instead of an
    implicit fact. `__post_init__` raises StrategyUnavailableError for
    any deviation -- there is no way to construct a "close enough" or
    partially-valid instance."""

    ladder_1_pct: float
    ladder_1_qty: int
    ladder_2_pct: float
    ladder_2_qty: int
    floor_pct: float
    initial_qty: int
    maximum_position: int

    def __post_init__(self) -> None:
        expected = {
            "ladder_1_pct": -0.05,
            "ladder_1_qty": 10,
            "ladder_2_pct": -0.08,
            "ladder_2_qty": 20,
            "floor_pct": -0.10,
            "initial_qty": 10,
            "maximum_position": 40,
        }
        for field_name, expected_value in expected.items():
            actual_value = getattr(self, field_name)
            if actual_value != expected_value:
                raise StrategyUnavailableError(
                    f"StrategyRuleSet.{field_name} = {actual_value!r} does not "
                    f"match the approved value {expected_value!r} "
                    "(docs/trading/strategy.md §1, §7) -- refusing to "
                    "construct an invalid strategy rule set"
                )
        if self.initial_qty + self.ladder_1_qty + self.ladder_2_qty != self.maximum_position:
            raise StrategyUnavailableError(
                "StrategyRuleSet quantities are internally inconsistent -- "
                f"{self.initial_qty} + {self.ladder_1_qty} + {self.ladder_2_qty} "
                f"!= {self.maximum_position}"
            )


def approved_strategy_rule_set() -> StrategyRuleSet:
    """The ONLY approved way to obtain a StrategyRuleSet. Raises
    StrategyUnavailableError (never returns a default/partial object)
    if the approved values cannot be validated."""

    return StrategyRuleSet(
        ladder_1_pct=-0.05,
        ladder_1_qty=10,
        ladder_2_pct=-0.08,
        ladder_2_qty=20,
        floor_pct=-0.10,
        initial_qty=10,
        maximum_position=40,
    )


class InvalidFloorContextError(RuntimeError):
    """Raised when floor information for a candidate is missing,
    unknown, or invalid. Per Controller governance: a candidate must
    NEVER be proposed without valid, explicitly-declared floor
    information for its trade context -- there is no default, no
    inferred value, and no silent 'unknown means no floor' behavior."""


@dataclass(frozen=True)
class FloorContext:
    """Explicit, validated declaration of the active-floor situation for
    a candidate at proposal time. There is exactly one way to construct
    a valid instance for each real situation -- via
    `FloorContext.no_existing_position()` or `FloorContext.known(...)` --
    and no way to construct an ambiguous one (a bare `None` is never
    accepted as floor information)."""

    has_existing_position: bool
    floor_price: Optional[float]

    def __post_init__(self) -> None:
        if self.has_existing_position:
            if self.floor_price is None:
                raise InvalidFloorContextError(
                    "has_existing_position=True requires a known floor_price -- "
                    "an existing position's active floor must never be unknown"
                )
            if not math.isfinite(self.floor_price) or self.floor_price <= 0:
                raise InvalidFloorContextError(
                    f"floor_price {self.floor_price!r} is not a valid positive "
                    "finite price"
                )
        else:
            if self.floor_price is not None:
                raise InvalidFloorContextError(
                    "has_existing_position=False must not carry a floor_price -- "
                    "no existing position means no active floor to declare"
                )

    @classmethod
    def no_existing_position(cls) -> "FloorContext":
        """Explicit, deliberate declaration: no position currently open
        in this symbol, so no active floor exists yet. This is the only
        legitimate way to represent the absence of a floor -- it must
        never be inferred from a missing/None argument."""
        return cls(has_existing_position=False, floor_price=None)

    @classmethod
    def known(cls, floor_price: float) -> "FloorContext":
        """Explicit declaration of a known, valid active floor for an
        existing position in this symbol."""
        return cls(has_existing_position=True, floor_price=floor_price)


class ApprovalState(Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class TradeAction(Enum):
    """The three Controller-approval-gated actions per
    docs/trading/execution.md §2. Deliberately does NOT include Floor --
    Floor execution requires no Controller approval at all (execution.md
    §1) and never goes through this action-scoped approval gate."""

    INITIAL_ENTRY = "initial_entry"
    LADDER_1 = "ladder_1"
    LADDER_2 = "ladder_2"


@dataclass(frozen=True)
class TradeProposal:
    """Immutable proposal record. `approval_state` and the two
    `*_at`/`*_by` fields below are the only fields that ever change
    across the proposal's lifecycle -- and even then, only via a new
    record written through `ProposalRepository`, never in place (see
    repository.py's immutability enforcement).

    Controller-approved lifecycle rule: creating a new proposal attempt
    for the same (trade_id, proposed_action) automatically transitions
    any previously PENDING or APPROVED attempt for that same pair to
    EXPIRED (see ProposalRepository.save()) -- only the newest attempt
    for a given action may remain active at once. Superseding is not
    mutation: the superseded record is preserved as a new immutable
    EXPIRED record via `expire()`, never edited or deleted."""

    proposal_id: str
    trade_id: str
    """Groups every proposal attempt (across actions, across repeated
    attempts after a rejection) that belongs to the same real-world
    trade. Caller-supplied -- never derived from symbol/date/anything
    else -- so this module makes no claim about how a trade's identity
    is established; that is the caller's responsibility. `proposal_id`
    stays unique per individual attempt; `trade_id` is the stable
    grouping key across attempts (docs/trading/decisions.md: rejection
    of one attempt must not permanently block a later attempt for the
    same action on the same trade)."""
    proposed_action: TradeAction
    """Which single Controller-gated action this specific proposal
    attempt is FOR, declared at creation time (caller-supplied via
    build_trade_proposal, never inferred). Distinct from
    `approved_action` below, which records what a *decision* on this
    proposal was actually tied to and is only set once decided --
    `with_decision` enforces `action == proposed_action`, so a proposal
    can only ever be decided for the action it was created for."""
    symbol: str
    candidate_source: str
    """Must be exactly 'fixed_watchlist' for this phase -- never implied
    to be Universe Search output."""

    current_price_at_proposal: float
    proposed_entry: float
    ladder_1_trigger: float
    ladder_1_quantity: int
    ladder_2_trigger: float
    ladder_2_quantity: int
    floor_trigger: float
    maximum_position: int

    proposal_created_at: datetime
    weighted_avg_entry_at_proposal: Optional[float]
    active_floor_at_proposal: Optional[float]

    assumptions: Tuple[str, ...]
    risks: Tuple[str, ...]

    approval_state: ApprovalState = ApprovalState.PENDING
    approval_received_at: Optional[datetime] = None
    approval_expires_at: Optional[datetime] = None
    decided_by: Optional[str] = None
    approved_action: Optional[TradeAction] = None
    """Which single Controller-gated action (docs/trading/execution.md
    §2) this proposal's decision was tied to. Set only when a decision
    has been recorded (APPROVED or REJECTED); must remain None while
    PENDING. This exists so a decision recorded for one action (e.g.
    LADDER_1) can never be reused by validate_for_submission() to
    authorize a different action (e.g. LADDER_2) -- each of Initial
    Entry, Ladder 1, and Ladder 2 requires its own separate approval."""
    expired_at: Optional[datetime] = None
    """Set only when approval_state is EXPIRED -- the moment a newer
    proposal attempt for the same (trade_id, proposed_action) was
    created, superseding this one. Must remain None in every other
    state."""

    def __post_init__(self) -> None:
        if not self.proposal_id:
            raise ValueError("proposal_id must be non-empty")
        if not self.trade_id:
            raise ValueError("trade_id must be non-empty")
        if not self.symbol:
            raise ValueError("symbol must be non-empty")
        if not isinstance(self.proposed_action, TradeAction):
            raise TypeError(f"proposed_action must be a TradeAction, got {type(self.proposed_action)!r}")
        if self.candidate_source != "fixed_watchlist":
            raise ValueError(
                "Phase A TradeProposal must declare candidate_source="
                "'fixed_watchlist' -- this build has no other legitimate "
                "candidate source and must never imply Universe Search"
            )
        if self.approval_state in (ApprovalState.APPROVED, ApprovalState.REJECTED):
            if self.approval_received_at is None:
                raise ValueError(
                    f"{self.approval_state.value} proposals must carry "
                    "approval_received_at"
                )
            if self.approved_action is None:
                raise ValueError(
                    f"{self.approval_state.value} proposals must carry "
                    "approved_action -- a decision must be tied to exactly "
                    "one of INITIAL_ENTRY, LADDER_1, or LADDER_2"
                )
            if self.approved_action != self.proposed_action:
                raise ValueError(
                    f"approved_action {self.approved_action.value!r} does not match "
                    f"this proposal's proposed_action {self.proposed_action.value!r} "
                    "-- a proposal may only be decided for the action it was "
                    "created for"
                )
        if self.approval_state == ApprovalState.PENDING:
            if self.approval_received_at is not None:
                raise ValueError("a pending proposal must not carry approval_received_at")
            if self.approved_action is not None:
                raise ValueError("a pending proposal must not carry approved_action")
        if self.approval_state == ApprovalState.EXPIRED:
            if self.expired_at is None:
                raise ValueError("an expired proposal must carry expired_at")
        elif self.expired_at is not None:
            raise ValueError(f"a {self.approval_state.value} proposal must not carry expired_at")

    def with_decision(
        self, *, approved: bool, decided_by: str, decided_at: datetime, action: TradeAction
    ) -> "TradeProposal":
        """Returns a NEW TradeProposal representing the decision --
        never mutates self. This is the only supported way to move a
        proposal out of PENDING.

        `action` ties this decision to exactly one of the three
        Controller-approval-gated actions (docs/trading/execution.md
        §2). It is required -- there is no default -- so a decision can
        never be recorded without explicitly stating which action it
        authorizes."""

        if self.approval_state != ApprovalState.PENDING:
            raise ValueError(
                f"proposal {self.proposal_id!r} is already "
                f"{self.approval_state.value!r} -- a decision may only be "
                "recorded once per proposal"
            )
        if not isinstance(action, TradeAction):
            raise TypeError(f"action must be a TradeAction, got {type(action)!r}")
        if action != self.proposed_action:
            raise ValueError(
                f"action {action.value!r} does not match this proposal's "
                f"proposed_action {self.proposed_action.value!r} -- a proposal "
                "may only be decided for the action it was created for"
            )
        new_state = ApprovalState.APPROVED if approved else ApprovalState.REJECTED
        expires_at = decided_at + APPROVAL_WINDOW if approved else None
        return _replace(
            self,
            approval_state=new_state,
            approval_received_at=decided_at,
            approval_expires_at=expires_at,
            decided_by=decided_by,
            approved_action=action,
        )

    def expire(self, *, expired_at: datetime) -> "TradeProposal":
        """Returns a NEW TradeProposal transitioned to EXPIRED -- never
        mutates self. Used exclusively by ProposalRepository.save() when
        a newer proposal attempt for the same (trade_id, proposed_action)
        is created, per the Controller-approved lifecycle rule: only the
        newest attempt for a given action may remain active at once.

        Only a PENDING or APPROVED proposal may be superseded this way --
        REJECTED and EXPIRED are already terminal history and must never
        be overwritten again."""

        if self.approval_state not in (ApprovalState.PENDING, ApprovalState.APPROVED):
            raise ValueError(
                f"proposal {self.proposal_id!r} is {self.approval_state.value!r} -- "
                "only a PENDING or APPROVED proposal may be superseded/expired"
            )
        return _replace(self, approval_state=ApprovalState.EXPIRED, expired_at=expired_at)


def _replace(proposal: TradeProposal, **overrides: object) -> TradeProposal:
    import dataclasses

    return dataclasses.replace(proposal, **overrides)
