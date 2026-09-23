"""BrokerClient -- the broker-agnostic abstraction ExecutionService
depends on.

No Alpaca (or any other broker) types, request/response shapes, or
status vocabulary appear here or in ExecutionService -- only this
normalized, broker-neutral contract. A future concrete Alpaca
implementation MUST implement this interface; this interface is never
shaped around Alpaca's own API.

Terminal classification (`is_terminal`) is deliberately computed by the
CONCRETE implementation, never here and never by ExecutionService --
only a real broker implementation knows its own status vocabulary well
enough to classify it. Mirrors the identical, already Controller-approved
reasoning behind `OrderExecution.is_broker_terminal` (see
execution.models's module docstring).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


class BrokerClientError(RuntimeError):
    """Base class for BrokerClient errors."""


class BrokerSubmissionAmbiguousError(BrokerClientError):
    """Raised by submit_order() when the outcome of a submission
    attempt could not be determined (e.g. a network timeout or
    connection reset) -- the caller does not know whether the broker
    received the order. Must never be interpreted as either success or
    rejection by any caller."""


class BrokerCommunicationError(BrokerClientError):
    """Raised by non-submit BrokerClient calls (get_order_by_client_order_id,
    get_cash_balance) when the broker could not be reached with a
    well-formed response. The caller does not learn any state from this
    error and must retry or report; it is never a signal that the
    resource does not exist -- for that, get_order_by_client_order_id()
    returns None from a definite HTTP 404, not from a network failure."""


class BrokerAccountBlockedError(BrokerClientError):
    """Raised by get_cash_balance() when the broker reports the account
    is blocked (trading_blocked or account_blocked) -- callers must treat
    the account as unusable for new orders even if a cash balance is
    reported alongside the block."""


@dataclass(frozen=True)
class BrokerOrderState:
    """A normalized, broker-neutral snapshot of one order's current
    state -- never an Alpaca (or other broker) type. `status` is the
    broker's own raw status string, passed through unmodified (never
    interpreted here or by ExecutionService); `is_terminal` is computed
    by the concrete BrokerClient implementation, which alone knows its
    broker's real vocabulary."""

    broker_order_id: str
    status: str
    is_terminal: bool
    filled_qty: int
    filled_avg_price: Optional[float]


class BrokerClient(ABC):
    @abstractmethod
    def submit_order(
        self,
        *,
        client_order_id: str,
        symbol: str,
        side: str,
        quantity: int,
        limit_price: float,
    ) -> BrokerOrderState:
        """Submits a new order. Returns a BrokerOrderState on any
        DEFINITE response (accepted, immediately filled, or definitely
        rejected -- all are definite outcomes). Raises
        BrokerSubmissionAmbiguousError if the outcome could not be
        determined (e.g. a timeout or connection reset) -- never
        guesses, never returns a fabricated state."""
        raise NotImplementedError

    @abstractmethod
    def get_order_by_client_order_id(self, client_order_id: str) -> Optional[BrokerOrderState]:
        """Returns the current state of the order submitted under this
        client_order_id, or None if the broker has no record of it --
        meaning a prior submission attempt never actually reached them,
        which is the signal that makes a later retry under the same
        client_order_id safe."""
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, client_order_id: str) -> None:
        """Requests cancellation of the remaining unfilled quantity of
        the order submitted under this client_order_id.

        Cancellation is ASYNCHRONOUS and NOT guaranteed -- this method
        returning does not mean the order has actually been cancelled.
        The order may continue to fill (even fully) before the
        cancellation is processed, or may already have reached a
        terminal state by the time this is called, in which case this
        is a harmless no-op. Callers must never treat the fill quantity
        observed at the moment this is called as final -- they must
        re-query get_order_by_client_order_id() and wait for
        is_terminal=True to learn the actual, final outcome. Safe to
        call repeatedly/idempotently on an already-cancelled or
        already-terminal order."""
        raise NotImplementedError

    @abstractmethod
    def get_cash_balance(self) -> float:
        """Returns the account's current cash balance in USD as read
        directly from the broker at call time. The broker is the source
        of truth for account state (D-0018 / D-0039); this method must
        never return a cached value that could drift from the broker's
        actual figure. Raises BrokerAccountBlockedError if the broker
        reports the account is blocked for trading or fully blocked --
        the caller must not use any cash figure from a blocked account
        to authorize new orders. Raises BrokerCommunicationError if the
        broker could not be reached with a well-formed response."""
        raise NotImplementedError
