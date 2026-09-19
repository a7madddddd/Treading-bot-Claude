"""OrderExecutionRepository -- persistence abstraction for
OrderExecution.

Mirrors `src/trade/repository.py`'s `TradeRepository` pattern: an ABC
plus a concrete implementation (`SqliteOrderExecutionRepository`, in
`sqlite_repository.py`). No business logic lives here -- `OrderExecution`'s
own transition methods (start_submission/mark_submission_unknown/
record_broker_response/record_fill_update) already own every invariant;
this module only defines the persistence contract for storing and
retrieving the `OrderExecution` objects a caller produces via those
methods.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from .models import OrderExecution


class OrderExecutionRepositoryError(RuntimeError):
    """Base class for OrderExecutionRepository errors."""


class OrderExecutionAlreadyExistsError(OrderExecutionRepositoryError):
    """Raised by save() when execution_id, proposal_id, or
    client_order_id already exists. save() creates a NEW execution
    only -- an existing one must go through update()."""


class OrderExecutionRevisionConflictError(OrderExecutionRepositoryError):
    """Raised by update() when the stored revision no longer matches
    `expected_revision` -- someone else updated this execution since
    the caller loaded it. On this error: NOTHING is written. The
    caller must reload the current OrderExecutionRecord and decide how
    to proceed; that policy is not this repository's concern."""


class ProposalDoesNotExistError(OrderExecutionRepositoryError):
    """Raised by save() when a BUY execution's proposal_id has no
    corresponding row in `proposals`. This repository never creates a
    Proposal itself -- a Proposal must already exist (mirrors the
    Controller-approved Trade-first foreign key rule already enforced
    by SqliteProposalRepository for trade_id). Never raised for a SELL
    execution (proposal_id is always None there by design)."""


class TradeDoesNotExistError(OrderExecutionRepositoryError):
    """Raised by save() when execution.trade_id has no corresponding
    row in `trades`. Applies to BOTH BUY and SELL executions -- every
    OrderExecution is always anchored to a real Trade, proposal or not."""


@dataclass(frozen=True)
class OrderExecutionRecord:
    """An `OrderExecution` together with its persistence revision.
    `OrderExecution` itself carries no `revision` field (it is
    persistence-only bookkeeping, not a domain concept) -- this wrapper
    is how every read/write method hands the caller the revision needed
    for a subsequent `update()` call, without any hidden state inside
    the repository."""

    execution: OrderExecution
    revision: int


class OrderExecutionRepository(ABC):
    @abstractmethod
    def save(self, execution: OrderExecution, *, now: datetime) -> OrderExecutionRecord:
        """Persists a NEW execution. Raises OrderExecutionAlreadyExistsError
        if execution_id, proposal_id, or client_order_id already
        exists. Raises ProposalDoesNotExistError if a BUY execution's
        proposal_id has no corresponding row in `proposals`. Raises
        TradeDoesNotExistError if execution.trade_id has no
        corresponding row in `trades`. Always creates at revision 0."""
        raise NotImplementedError

    @abstractmethod
    def update(
        self, execution: OrderExecution, *, expected_revision: int, transition: str, now: datetime
    ) -> OrderExecutionRecord:
        """The ONLY supported way to persist a change to an existing
        execution -- `execution` must be the NEW OrderExecution instance
        returned by one of OrderExecution's own transition methods.
        Raises OrderExecutionRevisionConflictError if `expected_revision`
        no longer matches the stored revision. `transition` is a
        caller-supplied audit label -- this repository never infers or
        interprets it."""
        raise NotImplementedError

    @abstractmethod
    def get(self, execution_id: str) -> Optional[OrderExecutionRecord]:
        raise NotImplementedError

    @abstractmethod
    def get_by_proposal_id(self, proposal_id: str) -> Optional[OrderExecutionRecord]:
        raise NotImplementedError

    @abstractmethod
    def get_by_client_order_id(self, client_order_id: str) -> Optional[OrderExecutionRecord]:
        raise NotImplementedError

    @abstractmethod
    def get_by_trade_id_and_side(self, trade_id: str, side: str) -> Optional[OrderExecutionRecord]:
        """Used for SELL-side (Floor) duplicate-prevention and
        recovery: returns the MOST RECENT execution for this
        trade_id/side combination, if any exist (a trade may have
        multiple sequential sell executions over time -- e.g. a
        partial Floor fill followed by a later attempt at the
        remainder), regardless of resolved/unresolved state. A BUY
        execution is never looked up this way (proposal_id already
        uniquely identifies it); this exists specifically because a
        SELL execution has no proposal to anchor a lookup to."""
        raise NotImplementedError

    @abstractmethod
    def list_unresolved(self) -> List[OrderExecutionRecord]:
        """Every execution whose is_broker_terminal is False -- i.e.
        not yet resolved. Used for restart recovery. Never interprets
        the raw broker status string; filters exclusively on the
        caller-supplied is_broker_terminal flag."""
        raise NotImplementedError
