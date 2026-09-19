"""Pure serialization mapping between OrderExecution and a flat SQLite
row shape.

No SQL, no I/O, no database connection, no repository dependency, no
Alpaca dependency -- these two functions only translate between an
OrderExecution dataclass instance and a plain dict matching the future
`order_executions` table's columns (see the approved persistence
design review; the migration itself is not yet created).
`row_to_order_execution()` ALWAYS reconstructs through OrderExecution's
real constructor, so every invariant in `OrderExecution.__post_init__`
is enforced on every deserialization -- a corrupted/invariant-violating
row raises OrderExecutionError, never silently produces an invalid
object or repairs missing values.

Deliberately excluded from the row shape entirely: `revision` (a
persistence-layer-only wrapper concept -- OrderExecution itself carries
no revision field, exactly mirroring Trade/TradeRecord) and any derived
label (e.g. "is_unresolved"/"is_partially_filled") -- OrderExecution
has no such fields, and none is invented here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Mapping, Optional

from .models import OrderExecution


def _dt(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value is not None else None


def _bool_to_int(value: bool) -> int:
    return 1 if value else 0


def _int_to_bool(value: Any) -> bool:
    return bool(value)


def order_execution_to_row(execution: OrderExecution) -> Dict[str, Any]:
    """`OrderExecution` -> a flat dict matching the future
    `order_executions` table's columns exactly. Contains no `revision`
    key and no derived-label keys -- neither is ever persisted."""

    return {
        "execution_id": execution.execution_id,
        "proposal_id": execution.proposal_id,
        "trade_id": execution.trade_id,
        "client_order_id": execution.client_order_id,
        "side": execution.side,
        "broker_order_id": execution.broker_order_id,
        "requested_qty": execution.requested_qty,
        "filled_qty": execution.filled_qty,
        "filled_avg_price": execution.filled_avg_price,
        "status": execution.status,
        "is_broker_terminal": _bool_to_int(execution.is_broker_terminal),
        "created_at": _dt(execution.created_at),
        "last_broker_poll_at": _dt(execution.last_broker_poll_at),
    }


def row_to_order_execution(row: Mapping[str, Any]) -> OrderExecution:
    """A flat row (as produced by `order_execution_to_row()`, or loaded
    from the `order_executions` table) -> a real `OrderExecution`
    instance. ALWAYS constructs through OrderExecution's real
    constructor -- `__post_init__`'s invariants fire here, so a
    corrupted/invariant-violating row raises OrderExecutionError rather
    than silently producing an invalid object or inferring a missing
    value."""

    return OrderExecution(
        execution_id=row["execution_id"],
        proposal_id=row["proposal_id"],
        trade_id=row["trade_id"],
        client_order_id=row["client_order_id"],
        side=row["side"],
        broker_order_id=row["broker_order_id"],
        requested_qty=row["requested_qty"],
        filled_qty=row["filled_qty"],
        filled_avg_price=row["filled_avg_price"],
        status=row["status"],
        is_broker_terminal=_int_to_bool(row["is_broker_terminal"]),
        created_at=_parse_dt(row["created_at"]),
        last_broker_poll_at=_parse_dt(row["last_broker_poll_at"]),
    )
