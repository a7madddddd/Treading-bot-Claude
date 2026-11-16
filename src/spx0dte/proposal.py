"""Option-trade proposal model for the SPX 0DTE module (D-0095 execution
wiring). Deliberately NOT `proposals.models.TradeProposal` -- that model's
invariants (ladder triggers, share quantities, floor context) are specific
to the equity-ladder strategy and do not describe an options trade (strike,
call/put, a single entry, a hold-cap-governed exit). Reusing it would mean
either fabricating ladder fields that don't apply or weakening its
validation -- both worse than a separate, honestly-scoped model.

Mirrors the SAME approval-window and immutable-transition shape as
`proposals.models.TradeProposal` (a decision only moves PENDING ->
APPROVED/REJECTED once, EXPIRED only by explicit supersession, every
transition returns a new object) because that shape is sound, not because
this module imports or depends on that one.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

APPROVAL_WINDOW = timedelta(minutes=5)
"""Same window as D-0007's equity-ladder approvals -- an approval is
valid for submission only within this many minutes of being received."""


class ProposalApprovalState(Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass(frozen=True)
class OptionTradeProposal:
    """Immutable proposal record for a single SPX 0DTE option entry.
    `approval_state` and the decision/expiry fields are the only ones
    that ever change across the proposal's lifecycle, and even then
    only via `with_decision`/`expire`, which return a NEW object."""

    proposal_id: str
    date: str  # YYYY-MM-DD, the trading day this candidate belongs to
    direction: str  # "call" or "put"
    strike: int
    occ_symbol: str
    entry_time: datetime
    hold_cap_minutes: float
    estimated_premium: float
    """Last observed option price at signal time -- informational only,
    NEVER used as the limit price at submission (execution.py re-reads
    a fresh quote; D-0007's own "re-check the price before submitting"
    principle, applied here for the same reason: a price can move
    between proposal and decision)."""
    vix: Optional[float]
    filters_passed: str
    created_at: datetime

    approval_state: ProposalApprovalState = ProposalApprovalState.PENDING
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None
    approval_expires_at: Optional[datetime] = None
    expired_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if not self.proposal_id:
            raise ValueError("proposal_id must be non-empty")
        if self.direction not in ("call", "put"):
            raise ValueError(f"direction must be 'call' or 'put', got {self.direction!r}")
        if self.strike <= 0:
            raise ValueError("strike must be positive")
        if not self.occ_symbol:
            raise ValueError("occ_symbol must be non-empty")
        if self.hold_cap_minutes <= 0:
            raise ValueError("hold_cap_minutes must be positive")

        if self.approval_state in (ProposalApprovalState.APPROVED, ProposalApprovalState.REJECTED):
            if self.decided_at is None or self.decided_by is None:
                raise ValueError(f"{self.approval_state.value} proposals must carry decided_at and decided_by")
        if self.approval_state == ProposalApprovalState.PENDING:
            if self.decided_at is not None or self.decided_by is not None:
                raise ValueError("a pending proposal must not carry a decision")
        if self.approval_state == ProposalApprovalState.EXPIRED:
            if self.expired_at is None:
                raise ValueError("an expired proposal must carry expired_at")
        elif self.expired_at is not None:
            raise ValueError(f"a {self.approval_state.value} proposal must not carry expired_at")

    def with_decision(self, *, approved: bool, decided_by: str, decided_at: datetime) -> "OptionTradeProposal":
        if self.approval_state != ProposalApprovalState.PENDING:
            raise ValueError(
                f"proposal {self.proposal_id!r} is already {self.approval_state.value!r} -- "
                "a decision may only be recorded once"
            )
        if not decided_by:
            raise ValueError("decided_by must be non-empty -- every decision must be attributable")
        new_state = ProposalApprovalState.APPROVED if approved else ProposalApprovalState.REJECTED
        expires_at = decided_at + APPROVAL_WINDOW if approved else None
        return dataclasses.replace(
            self, approval_state=new_state, decided_by=decided_by,
            decided_at=decided_at, approval_expires_at=expires_at,
        )

    def expire(self, *, expired_at: datetime) -> "OptionTradeProposal":
        if self.approval_state != ProposalApprovalState.PENDING:
            raise ValueError(
                f"proposal {self.proposal_id!r} is {self.approval_state.value!r} -- "
                "only a PENDING proposal may expire unanswered"
            )
        return dataclasses.replace(self, approval_state=ProposalApprovalState.EXPIRED, expired_at=expired_at)

    def is_approval_still_valid(self, *, now: datetime) -> bool:
        """True only for an APPROVED proposal within its 5-minute
        submission window -- the same re-check execution.py must do
        immediately before calling the broker."""
        if self.approval_state != ProposalApprovalState.APPROVED:
            return False
        if self.approval_expires_at is None:
            return False
        return now <= self.approval_expires_at
