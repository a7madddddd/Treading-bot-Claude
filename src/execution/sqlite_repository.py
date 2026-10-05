"""SqliteOrderExecutionRepository -- the SQLite-backed
OrderExecutionRepository implementation (D-0024).

Responsible ONLY for mechanical I/O: moving OrderExecution objects
to/from SQL rows (via serialization.py, never a second serialization
scheme), managing transactions (via persistence.db.transaction --
BEGIN IMMEDIATE -- never a second transaction mechanism), and
optimistic-revision bookkeeping. It never recomputes a domain
invariant, never interprets a raw broker status string, and never
generates an identifier -- OrderExecution's own transition methods
already own every one of those concerns; this module only persists
whatever they produced.

Proposal-first FK: the `order_executions` table's `proposal_id` column
is a real, enforced foreign key against `proposals(proposal_id)`. This
module does NOT create Proposal records under any circumstance -- a
save() for a nonexistent proposal_id fails clearly
(ProposalDoesNotExistError).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import List, Optional

from persistence.db import transaction

from .models import OrderExecution
from .repository import (
    OrderExecutionAlreadyExistsError,
    OrderExecutionRecord,
    OrderExecutionRepository,
    OrderExecutionRevisionConflictError,
    ProposalDoesNotExistError,
    TradeDoesNotExistError,
)
from .serialization import order_execution_to_row, row_to_order_execution


class SqliteOrderExecutionRepository(OrderExecutionRepository):
    """Operates on an already-connected, already-bootstrapped
    sqlite3.Connection (see persistence.db.connect/bootstrap_schema --
    this class does not open or bootstrap a database itself)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row

    def save(self, execution: OrderExecution, *, now: datetime) -> OrderExecutionRecord:
        row = order_execution_to_row(execution)
        columns = list(row.keys()) + ["revision"]
        placeholders = ", ".join("?" for _ in columns)
        values = list(row.values()) + [0]

        with transaction(self._conn) as conn:
            if not conn.execute(
                "SELECT 1 FROM trades WHERE trade_id = ?", (execution.trade_id,)
            ).fetchone():
                raise TradeDoesNotExistError(
                    f"cannot save execution {execution.execution_id!r}: no Trade exists for "
                    f"trade_id {execution.trade_id!r} -- a Trade must be created first; this "
                    "repository never creates a Trade automatically"
                )

            if execution.proposal_id is not None and not conn.execute(
                "SELECT 1 FROM proposals WHERE proposal_id = ?", (execution.proposal_id,)
            ).fetchone():
                raise ProposalDoesNotExistError(
                    f"cannot save execution {execution.execution_id!r}: no Proposal exists for "
                    f"proposal_id {execution.proposal_id!r} -- a Proposal must be created first; "
                    "this repository never creates a Proposal automatically"
                )

            try:
                conn.execute(
                    f"INSERT INTO order_executions ({', '.join(columns)}) VALUES ({placeholders})",
                    values,
                )
            except sqlite3.IntegrityError as exc:
                raise OrderExecutionAlreadyExistsError(
                    f"execution_id {execution.execution_id!r} (or its proposal_id/client_order_id) "
                    "already exists -- save() creates a new execution only, use update() to persist "
                    "a change to an existing one"
                ) from exc

        return OrderExecutionRecord(execution=execution, revision=0)

    def update(
        self,
        execution: OrderExecution,
        *,
        expected_revision: int,
        transition: str,
        now: datetime,
    ) -> OrderExecutionRecord:
        row = order_execution_to_row(execution)

        with transaction(self._conn) as conn:
            set_clause = ", ".join(f"{column} = ?" for column in row) + ", revision = revision + 1"
            cursor = conn.execute(
                f"UPDATE order_executions SET {set_clause} WHERE execution_id = ? AND revision = ?",
                list(row.values()) + [execution.execution_id, expected_revision],
            )

            if cursor.rowcount == 0:
                current = conn.execute(
                    "SELECT revision FROM order_executions WHERE execution_id = ?",
                    (execution.execution_id,),
                ).fetchone()
                current_revision = current["revision"] if current is not None else None
                raise OrderExecutionRevisionConflictError(
                    f"execution {execution.execution_id!r}: expected revision {expected_revision}, "
                    f"but the stored revision is {current_revision!r} -- update refused, nothing written"
                )

        return OrderExecutionRecord(execution=execution, revision=expected_revision + 1)

    def get(self, execution_id: str) -> Optional[OrderExecutionRecord]:
        row = self._conn.execute(
            "SELECT * FROM order_executions WHERE execution_id = ?", (execution_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def get_by_proposal_id(self, proposal_id: str) -> Optional[OrderExecutionRecord]:
        row = self._conn.execute(
            "SELECT * FROM order_executions WHERE proposal_id = ?", (proposal_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def get_by_client_order_id(self, client_order_id: str) -> Optional[OrderExecutionRecord]:
        row = self._conn.execute(
            "SELECT * FROM order_executions WHERE client_order_id = ?", (client_order_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def get_by_trade_id_and_side(self, trade_id: str, side: str) -> Optional[OrderExecutionRecord]:
        # ORDER BY rowid DESC: a trade can have multiple SEQUENTIAL
        # sell executions over time (e.g. a partial Floor fill followed
        # by a later attempt at the remainder) -- always the MOST
        # RECENT one, deterministically, never an arbitrary row when
        # more than one exists.
        row = self._conn.execute(
            "SELECT * FROM order_executions WHERE trade_id = ? AND side = ? ORDER BY rowid DESC LIMIT 1",
            (trade_id, side),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def list_unresolved(self) -> List[OrderExecutionRecord]:
        rows = self._conn.execute(
            "SELECT * FROM order_executions WHERE is_broker_terminal = 0"
        ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def _row_to_record(self, row: sqlite3.Row) -> OrderExecutionRecord:
        row_dict = dict(row)
        revision = row_dict.pop("revision")
        execution = row_to_order_execution(row_dict)
        return OrderExecutionRecord(execution=execution, revision=revision)
