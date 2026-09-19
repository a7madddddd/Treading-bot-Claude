"""SqliteTradeRepository -- the SQLite-backed TradeRepository
implementation (D-0024).

Responsible ONLY for mechanical I/O: moving Trade objects to/from SQL
rows (via trade_to_row/row_to_trade, never a second serialization
scheme), managing transactions (via persistence.db.transaction --
BEGIN IMMEDIATE -- never a second transaction mechanism), and
optimistic-revision bookkeeping. It never recomputes a strategy value,
never re-derives a Trade invariant, and never infers a transition
label -- Trade's own transition methods already own every one of those
concerns; this module only persists whatever they produced.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import List, Optional, Tuple

from persistence.db import transaction

from .models import Trade, describe_status
from .repository import (
    TradeAlreadyExistsError,
    TradeRecord,
    TradeRepository,
    TradeRepositoryError,
    TradeRevisionConflictError,
)
from .serialization import row_to_trade, trade_to_row

_ACTIVE_STATUSES = ("AWAITING_INITIAL_FILL", "ACTIVE")


class SqliteTradeRepository(TradeRepository):
    """Operates on an already-connected, already-bootstrapped
    sqlite3.Connection (see persistence.db.connect/bootstrap_schema --
    this class does not open or bootstrap a database itself, it only
    persists Trade objects against a connection the caller provides)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row

    def save(self, trade: Trade, *, now: datetime) -> TradeRecord:
        row = trade_to_row(trade)
        columns = list(row.keys()) + ["revision"]
        placeholders = ", ".join("?" for _ in columns)
        values = list(row.values()) + [0]

        with transaction(self._conn) as conn:
            try:
                conn.execute(
                    f"INSERT INTO trades ({', '.join(columns)}) VALUES ({placeholders})", values
                )
            except sqlite3.IntegrityError as exc:
                raise TradeAlreadyExistsError(
                    f"trade_id {trade.trade_id!r} already exists -- save() creates a new trade only, "
                    "use update() to persist a change to an existing one"
                ) from exc

            conn.execute(
                "INSERT INTO trade_snapshots (trade_id, revision, snapshot_at, transition, full_state_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (trade.trade_id, 0, now.isoformat(), "created", json.dumps(row)),
            )

            if trade.protective_order_id is not None:
                conn.execute(
                    "INSERT INTO protective_order_history "
                    "(trade_id, sequence, order_id, stop_price, status, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        trade.trade_id,
                        1,
                        trade.protective_order_id,
                        trade.protective_order_stop_price,
                        trade.protective_order_status,
                        now.isoformat(),
                    ),
                )

        return TradeRecord(trade=trade, revision=0)

    def update(
        self, trade: Trade, *, expected_revision: int, transition: str, now: datetime
    ) -> TradeRecord:
        row = trade_to_row(trade)

        with transaction(self._conn) as conn:
            # The previous protective_order_id is read INSIDE this same
            # transaction (after BEGIN IMMEDIATE has already acquired
            # the write lock) -- never a separate pre-read outside it,
            # so no decision here can race against a concurrent writer.
            existing = conn.execute(
                "SELECT protective_order_id FROM trades WHERE trade_id = ?", (trade.trade_id,)
            ).fetchone()
            if existing is None:
                raise TradeRepositoryError(f"no trade found for id {trade.trade_id!r}")
            previous_protective_order_id = existing["protective_order_id"]

            set_clause = ", ".join(f"{column} = ?" for column in row) + ", revision = revision + 1"
            cursor = conn.execute(
                f"UPDATE trades SET {set_clause} WHERE trade_id = ? AND revision = ?",
                list(row.values()) + [trade.trade_id, expected_revision],
            )

            if cursor.rowcount == 0:
                current = conn.execute(
                    "SELECT revision FROM trades WHERE trade_id = ?", (trade.trade_id,)
                ).fetchone()
                current_revision = current["revision"] if current is not None else None
                raise TradeRevisionConflictError(
                    f"trade {trade.trade_id!r}: expected revision {expected_revision}, but the "
                    f"stored revision is {current_revision!r} -- update refused, nothing written"
                )

            new_revision = expected_revision + 1
            conn.execute(
                "INSERT INTO trade_snapshots (trade_id, revision, snapshot_at, transition, full_state_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (trade.trade_id, new_revision, now.isoformat(), transition, json.dumps(row)),
            )

            if trade.protective_order_id != previous_protective_order_id:
                next_seq_row = conn.execute(
                    "SELECT COALESCE(MAX(sequence), 0) AS max_seq FROM protective_order_history WHERE trade_id = ?",
                    (trade.trade_id,),
                ).fetchone()
                next_seq = next_seq_row["max_seq"] + 1
                if trade.protective_order_id is not None:
                    conn.execute(
                        "INSERT INTO protective_order_history "
                        "(trade_id, sequence, order_id, stop_price, status, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            trade.trade_id,
                            next_seq,
                            trade.protective_order_id,
                            trade.protective_order_stop_price,
                            trade.protective_order_status,
                            now.isoformat(),
                        ),
                    )

        return TradeRecord(trade=trade, revision=new_revision)

    def get(self, trade_id: str) -> Optional[TradeRecord]:
        row = self._conn.execute("SELECT * FROM trades WHERE trade_id = ?", (trade_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def list_active(self) -> List[TradeRecord]:
        # Deliberately fetches every trade and filters in Python via
        # describe_status() -- NEVER a duplicated SQL predicate
        # re-expressing AWAITING_INITIAL_FILL/ACTIVE's derivation. The
        # trades table is expected to stay small (few concurrently open
        # trades in a paper system), so this costs nothing measurable.
        rows = self._conn.execute("SELECT * FROM trades").fetchall()
        records = [self._row_to_record(row) for row in rows]
        return [record for record in records if describe_status(record.trade) in _ACTIVE_STATUSES]

    def list_for_symbol(self, symbol: str) -> List[TradeRecord]:
        rows = self._conn.execute("SELECT * FROM trades WHERE symbol = ?", (symbol,)).fetchall()
        return [self._row_to_record(row) for row in rows]

    def _row_to_record(self, row: sqlite3.Row) -> TradeRecord:
        row_dict = dict(row)
        revision = row_dict.pop("revision")
        lineage = self._load_lineage(
            row_dict["trade_id"], current_protective_order_id=row_dict["protective_order_id"]
        )
        trade = row_to_trade(row_dict, protective_order_lineage=lineage)
        return TradeRecord(trade=trade, revision=revision)

    def _load_lineage(self, trade_id: str, *, current_protective_order_id: Optional[str]) -> Tuple[str, ...]:
        rows = self._conn.execute(
            "SELECT order_id FROM protective_order_history WHERE trade_id = ? ORDER BY sequence",
            (trade_id,),
        ).fetchall()
        order_ids = [row["order_id"] for row in rows]
        if (
            current_protective_order_id is not None
            and order_ids
            and order_ids[-1] == current_protective_order_id
        ):
            return tuple(order_ids[:-1])
        return tuple(order_ids)
