"""PendingDecisionSource -- the abstraction that funnels a Controller
decision into the Engine, without coupling the Engine or any domain
model to any particular transport (Controller-approved Engine design
review, this session: "keep approval intake behind an
abstraction/interface... real Telegram inbound approval (D-0025)
remains a separate future adapter... do not couple Engine or domain
logic directly to Telegram").

Mirrors the same abstract-interface-plus-concrete-adapter pattern
already used for `BrokerClient` (real Alpaca implementation deferred)
and `INotificationService` (real Telegram implementation already
built, kept separate from the abstraction). `InMemoryDecisionSource`
is the trivial in-process/dev/test adapter approved for Engine
development now; a real Telegram-webhook-backed adapter is later work,
plugging into this SAME interface with no Engine redesign.

Every `ControllerDecision` requires an explicit `decided_by` -- nothing
in this module can auto-approve anything. The Engine only ever forwards
a `ControllerDecision` unchanged into the existing, unmodified
`ProposalRepository.record_decision()` / `ExecutionService.confirm_
ladder2_partial_fill()` calls, both of which already require an
explicit decider identity and already refuse to proceed without one --
so whatever adapter produces a `ControllerDecision`, the Controller
remains the only source of authority for what it contains.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import List


class DecisionKind(Enum):
    APPROVE = "approve"
    REJECT = "reject"
    CONFIRM_LADDER2_PARTIAL_FILL = "confirm_ladder2_partial_fill"


@dataclass(frozen=True)
class ControllerDecision:
    """One decision produced by whatever adapter is in use. Carries no
    UI/transport concept (no Telegram message id, no chat id, no
    button payload) -- purely `proposal_id` + `kind` + `decided_by`.
    The Engine derives the affected proposal's `proposed_action` itself
    (via ProposalRepository.get()) rather than requiring the adapter to
    know or supply it -- keeps the adapter surface minimal."""

    proposal_id: str
    kind: DecisionKind
    decided_by: str

    def __post_init__(self) -> None:
        if not self.proposal_id:
            raise ValueError("proposal_id must be non-empty")
        if not isinstance(self.kind, DecisionKind):
            raise TypeError(f"kind must be a DecisionKind, got {type(self.kind)!r}")
        if not self.decided_by:
            raise ValueError("decided_by must be non-empty -- every decision must be attributable")


class PendingDecisionSource(ABC):
    @abstractmethod
    def poll(self) -> List[ControllerDecision]:
        """Returns every ControllerDecision available since the last
        poll() call and drains them -- a decision is delivered to the
        Engine exactly once. Returns an empty list when nothing is
        pending; never blocks."""
        raise NotImplementedError


class InMemoryDecisionSource(PendingDecisionSource):
    """Trivial in-process adapter for Engine development and tests.
    `submit()` is the dev/test-only way to enqueue a decision (e.g. a
    local CLI or a test calling this directly); poll() drains the
    queue exactly once. Not a persisted queue -- a decision submitted
    here and never poll()'d before process exit is lost, which is
    acceptable for a dev/test adapter and explicitly NOT how a real
    Telegram-backed adapter (D-0025, future work) would behave."""

    def __init__(self) -> None:
        self._queue: List[ControllerDecision] = []

    def submit(self, decision: ControllerDecision) -> None:
        self._queue.append(decision)

    def poll(self) -> List[ControllerDecision]:
        drained, self._queue = self._queue, []
        return drained
