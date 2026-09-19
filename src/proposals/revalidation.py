"""D-0007-compatible revalidation -- pure, deterministic, no network.

Implements exactly `docs/architecture/state-management.md` §2
invariant 5, unchanged:

    "Ladder N submission requires: proposal approved AND
     now - approval_received_at <= 5 min AND
     |current - trigger| / trigger <= 0.005 AND
     trigger > active_floor_price."

This module does NOT submit anything -- it only answers "would a
submission be allowed right now," for use immediately before an actual
submission call in a later phase. Callers outside this phase must not
treat a passing result as itself authorization to submit; it is only
one of the required checks (the other being a genuinely APPROVED
proposal in the repository).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .models import PRICE_BAND_FRACTION, ApprovalState, TradeAction, TradeProposal


@dataclass(frozen=True)
class RevalidationResult:
    allowed: bool
    reason: Optional[str] = None


def validate_for_submission(
    proposal: TradeProposal,
    *,
    trigger_price: float,
    current_price: float,
    active_floor_price: Optional[float],
    now: datetime,
    action: TradeAction,
) -> RevalidationResult:
    """`trigger_price` is whichever level (ladder_1_trigger,
    ladder_2_trigger, or floor_trigger) the caller is about to submit
    against -- this function does not select one on its own.

    `action` is the Controller-gated action (INITIAL_ENTRY, LADDER_1, or
    LADDER_2) the caller is about to submit. It is required, and is
    checked against `proposal.approved_action` -- an approval recorded
    for one action must NEVER authorize submission of a different
    action (docs/trading/execution.md §2: each of Initial Entry,
    Ladder 1, and Ladder 2 requires its own separate Controller
    approval). Floor is deliberately not representable here at all --
    Floor executes automatically and never goes through this gate.

    D2 governance (Controller-approved fix): `active_floor_price=None`
    BLOCKS unconditionally. Per strategy.md §4/execution.md §7, floor
    priority applies to Ladder/Floor submissions, and by definition a
    Ladder can only trigger once a position (and therefore an original
    Floor) already exists -- so within this function's real call
    context there is no legitimate case where the floor is absent. A
    caller passing None is therefore always treated as "floor state
    unknown/unavailable," never as "no floor applies," and unknown floor
    state must never be interpreted as an infinitely permissive floor."""

    if not isinstance(action, TradeAction):
        raise TypeError(f"action must be a TradeAction, got {type(action)!r}")

    if proposal.approval_state != ApprovalState.APPROVED:
        return RevalidationResult(
            allowed=False,
            reason=f"proposal is {proposal.approval_state.value}, not approved",
        )

    if proposal.approval_received_at is None:
        return RevalidationResult(allowed=False, reason="approved proposal missing approval_received_at")

    if proposal.approved_action != action:
        approved_label = proposal.approved_action.value if proposal.approved_action else "none"
        return RevalidationResult(
            allowed=False,
            reason=(
                f"approval was recorded for action {approved_label!r}, not "
                f"{action.value!r} -- an approval for one action must never "
                "authorize a different action"
            ),
        )

    age = now - proposal.approval_received_at
    if age.total_seconds() > 5 * 60:
        return RevalidationResult(
            allowed=False,
            reason=f"approval age {age} exceeds the 5-minute window",
        )

    if trigger_price <= 0:
        return RevalidationResult(allowed=False, reason="trigger_price must be positive")

    price_deviation = abs(current_price - trigger_price) / trigger_price
    if price_deviation > PRICE_BAND_FRACTION:
        return RevalidationResult(
            allowed=False,
            reason=(
                f"price deviation {price_deviation:.4f} exceeds the "
                f"{PRICE_BAND_FRACTION} band"
            ),
        )

    if active_floor_price is None:
        return RevalidationResult(
            allowed=False,
            reason="active_floor_price is unknown/unavailable -- fail closed, "
            "never treated as an infinitely permissive floor",
        )

    if trigger_price <= active_floor_price:
        return RevalidationResult(
            allowed=False,
            reason="trigger is at or below the active protective floor",
        )

    return RevalidationResult(allowed=True)
