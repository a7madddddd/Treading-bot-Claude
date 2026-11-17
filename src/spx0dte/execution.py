"""Order submission for approved SPX 0DTE proposals (D-0095 execution
wiring).

Calls `execution.broker_client.BrokerClient.submit_order()` UNCHANGED --
no modification to `execution/broker_client.py` or
`execution/alpaca_broker_client.py`. Confirmed analytically before
building: that method's only symbol check is `symbol.isupper()`, and an
OCC option symbol (e.g. "SPXW261104C06900000") satisfies it -- digits
are not "cased" characters, so Python's `str.isupper()` ignores them.
The paper-only guardrail (`_APPROVED_PAPER_HOST`) already in
`AlpacaBrokerClient` applies to this call exactly as it does to every
equity order -- no new safety work needed, and none skipped.

`submit_exit_order()` (added 2026-11-17, D-0095-A item 4) closes an
open position -- a SELL, same unmodified `submit_order()` path. Per
`.claude/rules/01-roles-and-authority.md`: "Already-approved protective
exits ... may execute automatically in paper trading when their
trigger is reached" -- unlike an entry, a close needs no per-trade
Controller approval, because the exit RULE itself (target/stop/stall/
lagging_pace/time_cap) was already approved and extensively
backtested under D-0095. `live_exit.LiveExitTracker` decides WHEN to
call this; this function only submits.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from execution.broker_client import (
    BrokerClient, BrokerClientError, BrokerOrderState, BrokerSubmissionAmbiguousError,
)


@dataclass(frozen=True)
class SubmissionResult:
    success: bool
    order_state: Optional[BrokerOrderState]
    error: Optional[str]


def submit_entry_order(
    broker: BrokerClient, *, client_order_id: str, occ_symbol: str,
    quantity: int, limit_price: float,
) -> SubmissionResult:
    """Submits a single BUY order to open an SPX 0DTE option position.
    `client_order_id` should be the proposal_id -- gives the same
    broker-side idempotency the equity-ladder flow relies on (a retry
    under the same id never duplicates the order).

    Never raises BrokerSubmissionAmbiguousError to the caller -- an
    ambiguous outcome (network timeout, connection reset) is reported
    as a failed SubmissionResult with the error message, exactly like
    any other failure, so the caller has one result shape to handle
    and must re-check get_order_by_client_order_id() before assuming
    anything about broker-side state, per BrokerClient's own contract."""
    try:
        state = broker.submit_order(
            client_order_id=client_order_id,
            symbol=occ_symbol,
            side="buy",
            quantity=quantity,
            limit_price=limit_price,
        )
        return SubmissionResult(success=True, order_state=state, error=None)
    except BrokerSubmissionAmbiguousError as ex:
        return SubmissionResult(success=False, order_state=None, error=f"ambiguous: {ex}")
    except BrokerClientError as ex:
        return SubmissionResult(success=False, order_state=None, error=str(ex))


def submit_exit_order(
    broker: BrokerClient, *, client_order_id: str, occ_symbol: str,
    quantity: int, limit_price: float,
) -> SubmissionResult:
    """Submits a single SELL order to close an open SPX 0DTE option
    position. `client_order_id` should be a NEW id distinct from the
    entry's (e.g. f"{proposal_id}-exit") -- the broker's idempotency
    is per client_order_id, and a close is a different order from the
    entry that must not collide with or be deduplicated against it."""
    try:
        state = broker.submit_order(
            client_order_id=client_order_id,
            symbol=occ_symbol,
            side="sell",
            quantity=quantity,
            limit_price=limit_price,
        )
        return SubmissionResult(success=True, order_state=state, error=None)
    except BrokerSubmissionAmbiguousError as ex:
        return SubmissionResult(success=False, order_state=None, error=f"ambiguous: {ex}")
    except BrokerClientError as ex:
        return SubmissionResult(success=False, order_state=None, error=str(ex))
