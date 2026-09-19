"""OrderExecution -- the pure, broker-transport-independent domain
aggregate representing ONE logical broker execution, for EITHER an
approved Proposal (BUY: Initial Entry/Ladder 1/Ladder 2) OR a
proposal-independent protective exit (SELL: Floor) -- Controller-
approved generalization, this session, extending the original
Controller-approved design review.

`side` ("buy"/"sell") and `trade_id` (always required, the real
anchor) were added specifically so Floor never needs a TradeProposal
-- Floor executes automatically, with no Controller approval gate, so
forcing it through the Proposal-anchored shape used by BUY orders
would misrepresent it as something it structurally isn't. `proposal_id`
is REQUIRED (non-empty) for side="buy" and MUST be None for side="sell"
-- enforced in __post_init__, never left to caller discipline.

One OrderExecution per approved Proposal (BUY) or per protective exit
attempt (SELL), not per broker submission attempt: at most one real
broker order can ever legitimately exist for one logical execution, so
there is no separate "physical attempt" concept here -- a retry after a
crash/timeout re-enters THIS SAME instance's transition flow, reusing
its already-assigned `client_order_id` unchanged. See docs/architecture
-- this design was reviewed and approved before implementation; that
review is the authority for the "why", this module is the "what".

Pure data + pure transition methods only, mirroring the exact pattern
already established by `trade.models.Trade` and
`proposals.models.TradeProposal`: a frozen dataclass, invariants
enforced in `__post_init__`, every transition method returns a NEW
instance via `dataclasses.replace`, never mutates `self`.

Governance boundary (unchanged from Trade/Proposal): this module has
NO knowledge of Alpaca, any broker SDK, `TradeRepository`,
`ProposalRepository`, or `Trade` itself. It never imports any of them.
Whether/when a terminal OrderExecution should update a Trade is an
orchestration/reconciliation-layer decision, entirely out of scope
here. Whether only one OrderExecution may exist per proposal_id is a
repository-level (database uniqueness) concern, also out of scope
here -- a single instance cannot know about any other instance.

Broker status representation -- read this before touching `status` or
`is_broker_terminal`:

This module stores the broker's own status value EXACTLY as reported,
as a plain string -- it never re-derives, translates, or re-enumerates
it into a second, parallel status vocabulary (that would be exactly
the duplicated state machine the approved design explicitly forbids).

`is_broker_terminal` is the one field beyond the literal minimum list
from the design review, and its exact boundary was reviewed and
approved explicitly (Controller-approved "Final Review" pass, this
session) -- it must be read and used accordingly:

- `OrderExecution` intentionally does NOT know the broker's status
  vocabulary. It has no table of which raw status strings are
  terminal, and never will -- hardcoding a specific broker's status
  spellings into this domain model would couple it to that broker,
  which is exactly what the broker-abstraction boundary exists to
  prevent (this module imports no broker SDK/API types and stays
  fully testable without network access).
- `is_broker_terminal` is CALLER-SUPPLIED on every call to
  `record_broker_response()`/`record_fill_update()`. The caller (the
  future reconciliation/Execution Service layer, which DOES know the
  real broker's vocabulary) is responsible for deriving it correctly
  from the actual broker response before passing it in.
- This module CANNOT independently verify that `status` (the raw
  string) and `is_broker_terminal` (the caller's classification of
  that string) actually agree with each other -- there is no way to
  check that without the forbidden vocabulary knowledge. This is a
  real, accepted, bounded trade-off, not an oversight: it mirrors the
  same caller-trust already placed in `Trade.freeze_initial_reference()`
  for its `fill_price`/`filled_shares` parameters ("must already be
  broker-reconciled" -- Trade doesn't re-verify that either).
- `is_broker_terminal` must NEVER be treated, by any higher layer, as
  a second, independent source of broker-status truth alongside
  `status` -- it carries exactly one bit of information ("no further
  update is expected for this execution"), nothing about WHICH status
  was reported. `status` (the raw broker string) remains the sole
  broker-reported fact on this object; `is_broker_terminal` is only
  ever a caller-supplied classification OF that fact, used solely to
  enforce this module's own "no further transition once terminal"
  invariant.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Optional

CREATED = "CREATED"
SUBMITTING = "SUBMITTING"
SUBMITTED_UNKNOWN = "SUBMITTED_UNKNOWN"

LOCAL_PRE_BROKER_STATUSES = frozenset({CREATED, SUBMITTING, SUBMITTED_UNKNOWN})
"""The only statuses this module itself assigns before any broker
response exists. Any other status value is, by definition, a broker's
own raw status string, passed through unmodified -- never validated
against a fixed vocabulary, since this module must stay broker-agnostic."""


class OrderExecutionError(RuntimeError):
    """Raised when an OrderExecution transition would violate one of
    its invariants. Per the Controller-approved design review: an
    invalid transition must BLOCK, never silently no-op or invent a
    value -- mirrors TradeStateError's role for Trade."""


@dataclass(frozen=True)
class OrderExecution:
    """Immutable per-execution record, one per approved Proposal.

    `execution_id` and `client_order_id` are REQUIRED, caller-supplied
    constructor parameters -- exactly like `proposal_id`/`trade_id`
    elsewhere in this codebase (Controller-approved correction, this
    session: an earlier version generated them internally via
    `uuid.uuid4()`, which broke consistency with the rest of this
    codebase's identifiers and pre-empted a `client_order_id` FORMAT
    decision -- e.g. Alpaca's real length/character-set constraints --
    that has been explicitly deferred to the future Execution
    Service/Alpaca-integration design, not decided here). This module
    performs NO id generation of any kind. Preserved regardless of
    where generation eventually lives: `execution_id` is immutable;
    `client_order_id` is immutable/write-once and must exist before any
    broker call is made; every retry of the same logical execution
    reuses the same `client_order_id`; no retry ever produces a new
    logical execution."""

    proposal_id: Optional[str]
    execution_id: str
    client_order_id: str
    trade_id: str
    requested_qty: int
    created_at: datetime

    side: str = "buy"

    broker_order_id: Optional[str] = None

    filled_qty: int = 0
    filled_avg_price: Optional[float] = None

    status: str = CREATED
    is_broker_terminal: bool = False

    last_broker_poll_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if self.side not in ("buy", "sell"):
            raise OrderExecutionError(f"side must be 'buy' or 'sell', got {self.side!r}")
        if self.side == "buy":
            if not self.proposal_id:
                raise OrderExecutionError("proposal_id must be non-empty when side='buy'")
        else:
            if self.proposal_id is not None:
                raise OrderExecutionError(
                    "proposal_id must be None when side='sell' -- a protective exit (Floor) "
                    "never has a TradeProposal; it never goes through Controller approval"
                )
        if not self.trade_id:
            raise OrderExecutionError("trade_id must be non-empty")
        if not self.execution_id:
            raise OrderExecutionError("execution_id must be non-empty")
        if not self.client_order_id:
            raise OrderExecutionError("client_order_id must be non-empty")
        if self.requested_qty <= 0:
            raise OrderExecutionError("requested_qty must be positive")
        if self.filled_qty < 0:
            raise OrderExecutionError("filled_qty must not be negative")
        if self.filled_qty > self.requested_qty:
            raise OrderExecutionError(
                f"filled_qty {self.filled_qty} exceeds requested_qty {self.requested_qty}"
            )

        if self.filled_qty > 0:
            if (
                self.filled_avg_price is None
                or not math.isfinite(self.filled_avg_price)
                or self.filled_avg_price <= 0
            ):
                raise OrderExecutionError(
                    f"filled_avg_price {self.filled_avg_price!r} is not a valid positive "
                    "finite price -- required when filled_qty > 0"
                )
        elif self.filled_avg_price is not None:
            raise OrderExecutionError("filled_avg_price must be None while filled_qty == 0")

        if not self.status:
            raise OrderExecutionError("status must be non-empty")

        if self.broker_order_id is not None and not self.broker_order_id:
            raise OrderExecutionError("broker_order_id must be non-empty when set")

        if self.status in LOCAL_PRE_BROKER_STATUSES:
            if self.broker_order_id is not None:
                raise OrderExecutionError(
                    f"status {self.status!r} is a local pre-broker status and must not "
                    "carry a broker_order_id"
                )
            if self.is_broker_terminal:
                raise OrderExecutionError(
                    "is_broker_terminal cannot be True before any broker response is recorded"
                )
            if self.last_broker_poll_at is not None:
                raise OrderExecutionError(
                    f"status {self.status!r} is a local pre-broker status and must not "
                    "carry last_broker_poll_at"
                )
        else:
            if self.broker_order_id is None:
                raise OrderExecutionError(
                    f"status {self.status!r} is a broker-reported value and requires "
                    "broker_order_id to be set"
                )
            if self.last_broker_poll_at is None:
                raise OrderExecutionError(
                    f"status {self.status!r} is a broker-reported value and requires "
                    "last_broker_poll_at to be set"
                )
            if self.last_broker_poll_at < self.created_at:
                raise OrderExecutionError("last_broker_poll_at cannot precede created_at")

    # ------------------------------------------------------------------
    # Transitions -- each returns a NEW OrderExecution, never mutates self
    # ------------------------------------------------------------------

    def start_submission(self, *, now: datetime) -> "OrderExecution":
        """Marks that a broker submission call is about to be made.
        Must be called, and durably persisted by the caller, BEFORE
        that call -- this is the local half of the idempotency
        mechanism the approved design relies on."""

        if self.status != CREATED:
            raise OrderExecutionError(
                f"cannot start submission from status {self.status!r} -- only from {CREATED!r}"
            )
        return replace(self, status=SUBMITTING)

    def mark_submission_unknown(self, *, now: datetime) -> "OrderExecution":
        """Records that the broker call's outcome is ambiguous (e.g. a
        timeout or connection reset with no clear response) -- the
        caller does not yet know whether the broker received the
        order. Never itself implies success or rejection; only
        `record_broker_response()`, fed an ACTUAL broker response, may
        resolve this."""

        if self.status != SUBMITTING:
            raise OrderExecutionError(
                f"cannot mark submission unknown from status {self.status!r} -- only from {SUBMITTING!r}"
            )
        return replace(self, status=SUBMITTED_UNKNOWN)

    def record_broker_response(
        self,
        *,
        broker_order_id: str,
        status: str,
        is_terminal: bool,
        now: datetime,
        filled_qty: int = 0,
        filled_avg_price: Optional[float] = None,
    ) -> "OrderExecution":
        """Records the first real broker response -- assigns
        `broker_order_id` (write-once: this method can only ever run
        once per instance, since afterwards `status` is no longer
        SUBMITTING/SUBMITTED_UNKNOWN, structurally preventing a second
        call) and the broker's own raw status string, unmodified.
        Accepts an initial fill (`filled_qty`/`filled_avg_price`) for
        the case where the broker's very first response already
        reports a fill (e.g. an immediate full fill) -- avoids ever
        representing "terminal, but filled_qty still 0" when that
        would be false.

        `status` must be an actual broker-reported value, never one of
        the local pre-broker constants -- this is what structurally
        prevents SUBMITTED_UNKNOWN from ever being silently reinterpreted
        as success or rejection without a genuine broker response."""

        if self.status not in (SUBMITTING, SUBMITTED_UNKNOWN):
            raise OrderExecutionError(
                f"cannot record a broker response from status {self.status!r} -- only from "
                f"{SUBMITTING!r} or {SUBMITTED_UNKNOWN!r}"
            )
        if not broker_order_id:
            raise OrderExecutionError("broker_order_id must be non-empty")
        if not status or status in LOCAL_PRE_BROKER_STATUSES:
            raise OrderExecutionError(
                f"status {status!r} must be an actual broker-reported value, not a local "
                "pre-broker status"
            )
        if filled_qty < self.filled_qty:
            raise OrderExecutionError("filled_qty cannot decrease")

        return replace(
            self,
            broker_order_id=broker_order_id,
            status=status,
            is_broker_terminal=is_terminal,
            filled_qty=filled_qty,
            filled_avg_price=filled_avg_price,
            last_broker_poll_at=now,
        )

    def record_fill_update(
        self,
        *,
        filled_qty: int,
        filled_avg_price: Optional[float],
        status: str,
        is_terminal: bool,
        now: datetime,
    ) -> "OrderExecution":
        """Records updated cumulative fill information from a
        subsequent reconciliation poll -- requires a broker response to
        already be known (`record_broker_response()` already called).
        Refuses once already terminal: a terminal OrderExecution is
        done, by design nothing about it may change again, which is
        exactly what "terminal states cannot move backward" requires --
        there is no backward direction to move from, because there is
        no forward direction either."""

        if self.broker_order_id is None:
            raise OrderExecutionError(
                "cannot record a fill update before a broker response is known -- call "
                "record_broker_response() first"
            )
        if self.is_broker_terminal:
            raise OrderExecutionError(
                f"execution {self.execution_id!r} already reached a terminal broker status "
                f"({self.status!r}) -- no further update is permitted"
            )
        if not status or status in LOCAL_PRE_BROKER_STATUSES:
            raise OrderExecutionError(
                f"status {status!r} must be an actual broker-reported value, not a local "
                "pre-broker status"
            )
        if filled_qty < self.filled_qty:
            raise OrderExecutionError("filled_qty cannot decrease")

        return replace(
            self,
            filled_qty=filled_qty,
            filled_avg_price=filled_avg_price,
            status=status,
            is_broker_terminal=is_terminal,
            last_broker_poll_at=now,
        )
