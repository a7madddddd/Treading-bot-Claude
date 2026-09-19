"""Pure serialization mapping between Trade and a flat SQLite row shape.

No SQL, no I/O, no database connection -- these two functions only
translate between a Trade dataclass instance and a plain dict matching
the `trades` table's columns (src/persistence/migrations/0001_initial.sql,
docs/architecture/state-management.md). `row_to_trade()` ALWAYS
reconstructs through Trade's real constructor, so every invariant in
`Trade.__post_init__` is enforced on every deserialization -- a
corrupted/invariant-violating row raises TradeStateError, never
silently produces an invalid object.

Deliberately excluded from the row shape entirely: `active_floor_price`/
`active_floor_source` (derived `@property` fields -- Controller-approved
design decision: never persisted, always recomputed) and any `status`
field (Trade carries none by design; see `describe_status()` in
models.py). `protective_order_lineage` is not a column either -- it
lives in the separate `protective_order_history` table and is threaded
through `row_to_trade()` as an explicit caller-supplied parameter; this
module performs no lookup of its own.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Mapping, Optional, Tuple

from .models import InitialOrderStatus, Trade


def _dt(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value is not None else None


def _bool_to_int(value: bool) -> int:
    return 1 if value else 0


def _int_to_bool(value: Any) -> bool:
    return bool(value)


def trade_to_row(trade: Trade) -> Dict[str, Any]:
    """`Trade` -> a flat dict matching the `trades` table's columns
    exactly. Contains no `active_floor_price`/`active_floor_source`/
    `status` keys -- those are never persisted."""

    return {
        "trade_id": trade.trade_id,
        "symbol": trade.symbol,
        "created_at": _dt(trade.created_at),
        "initial_order_id": trade.initial_order_id,
        "initial_order_status": trade.initial_order_status.value,
        "initial_order_reconciled": _bool_to_int(trade.initial_order_reconciled),
        "original_initial_entry_fill_price": trade.original_initial_entry_fill_price,
        "initial_filled_shares": trade.initial_filled_shares,
        "freeze_timestamp": _dt(trade.freeze_timestamp),
        "ladder1_price": trade.ladder1_price,
        "ladder2_price": trade.ladder2_price,
        "original_floor_price": trade.original_floor_price,
        "ladder1_proposal_id": trade.ladder1_proposal_id,
        "ladder1_filled": _bool_to_int(trade.ladder1_filled),
        "ladder1_fill_order_id": trade.ladder1_fill_order_id,
        "ladder1_fill_price": trade.ladder1_fill_price,
        "ladder1_fill_qty": trade.ladder1_fill_qty,
        "ladder2_proposal_id": trade.ladder2_proposal_id,
        "ladder2_filled": _bool_to_int(trade.ladder2_filled),
        "ladder2_fill_order_id": trade.ladder2_fill_order_id,
        "ladder2_fill_price": trade.ladder2_fill_price,
        "ladder2_fill_qty": trade.ladder2_fill_qty,
        "total_shares": trade.total_shares,
        "weighted_avg_entry_price": trade.weighted_avg_entry_price,
        "trailing_activated": _bool_to_int(trade.trailing_activated),
        "trailing_current_threshold": trade.trailing_current_threshold,
        "trailing_floor_price": trade.trailing_floor_price,
        "trailing_last_updated_at": _dt(trade.trailing_last_updated_at),
        "protective_order_id": trade.protective_order_id,
        "protective_order_stop_price": trade.protective_order_stop_price,
        "protective_order_status": trade.protective_order_status,
        "last_broker_poll_at": _dt(trade.last_broker_poll_at),
        "last_reconciled_at": _dt(trade.last_reconciled_at),
        "last_error": trade.last_error,
    }


def row_to_trade(row: Mapping[str, Any], *, protective_order_lineage: Tuple[str, ...] = ()) -> Trade:
    """A flat row (as produced by `trade_to_row()`, or loaded from the
    `trades` table) -> a real `Trade` instance. ALWAYS constructs
    through `Trade`'s real constructor -- `__post_init__`'s invariants
    fire here, so a corrupted/invariant-violating row raises
    TradeStateError rather than silently producing an invalid object.

    `protective_order_lineage` is supplied by the CALLER (it lives in
    the separate `protective_order_history` table, not in `row`) --
    this function performs no lookup of its own."""

    return Trade(
        trade_id=row["trade_id"],
        symbol=row["symbol"],
        created_at=_parse_dt(row["created_at"]),
        initial_order_id=row["initial_order_id"],
        initial_order_status=InitialOrderStatus(row["initial_order_status"]),
        initial_order_reconciled=_int_to_bool(row["initial_order_reconciled"]),
        original_initial_entry_fill_price=row["original_initial_entry_fill_price"],
        initial_filled_shares=row["initial_filled_shares"],
        freeze_timestamp=_parse_dt(row["freeze_timestamp"]),
        ladder1_price=row["ladder1_price"],
        ladder2_price=row["ladder2_price"],
        original_floor_price=row["original_floor_price"],
        ladder1_proposal_id=row["ladder1_proposal_id"],
        ladder1_filled=_int_to_bool(row["ladder1_filled"]),
        ladder1_fill_order_id=row["ladder1_fill_order_id"],
        ladder1_fill_price=row["ladder1_fill_price"],
        ladder1_fill_qty=row["ladder1_fill_qty"],
        ladder2_proposal_id=row["ladder2_proposal_id"],
        ladder2_filled=_int_to_bool(row["ladder2_filled"]),
        ladder2_fill_order_id=row["ladder2_fill_order_id"],
        ladder2_fill_price=row["ladder2_fill_price"],
        ladder2_fill_qty=row["ladder2_fill_qty"],
        total_shares=row["total_shares"],
        weighted_avg_entry_price=row["weighted_avg_entry_price"],
        trailing_activated=_int_to_bool(row["trailing_activated"]),
        trailing_current_threshold=row["trailing_current_threshold"],
        trailing_floor_price=row["trailing_floor_price"],
        trailing_last_updated_at=_parse_dt(row["trailing_last_updated_at"]),
        protective_order_id=row["protective_order_id"],
        protective_order_stop_price=row["protective_order_stop_price"],
        protective_order_status=row["protective_order_status"],
        protective_order_lineage=protective_order_lineage,
        last_broker_poll_at=_parse_dt(row["last_broker_poll_at"]),
        last_reconciled_at=_parse_dt(row["last_reconciled_at"]),
        last_error=row["last_error"],
    )
