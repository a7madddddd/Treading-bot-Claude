"""SQLite persistence for SPX 0DTE proposals (D-0095 execution wiring).

A SEPARATE database file (default `spx0dte.sqlite`) from the project's
main persistence store (`persistence.db`, its migrations directory,
and the `proposals`/`trades` tables it manages) -- Controller-approved
choice, kept deliberately simple for this first version rather than
extending the main schema/migrations system. Self-contained
bootstrap (`CREATE TABLE IF NOT EXISTS`), no dependency on
`persistence.db` or any existing migration.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator, Optional

from spx0dte.position import OpenPosition
from spx0dte.proposal import OptionTradeProposal, ProposalApprovalState

_SCHEMA = """
CREATE TABLE IF NOT EXISTS spx_proposals (
    proposal_id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    direction TEXT NOT NULL,
    strike INTEGER NOT NULL,
    occ_symbol TEXT NOT NULL,
    entry_time TEXT NOT NULL,
    hold_cap_minutes REAL NOT NULL,
    estimated_premium REAL NOT NULL,
    vix REAL,
    filters_passed TEXT NOT NULL,
    created_at TEXT NOT NULL,
    approval_state TEXT NOT NULL,
    decided_by TEXT,
    decided_at TEXT,
    approval_expires_at TEXT,
    expired_at TEXT
);

CREATE TABLE IF NOT EXISTS spx_positions (
    proposal_id TEXT PRIMARY KEY,
    occ_symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    strike INTEGER NOT NULL,
    date TEXT NOT NULL,
    entry_time TEXT NOT NULL,
    entry_price REAL NOT NULL,
    hold_cap_minutes REAL NOT NULL,
    is_closed INTEGER NOT NULL,
    exit_time TEXT,
    exit_price REAL,
    exit_reason TEXT
);
"""


class ProposalDoesNotExistError(RuntimeError):
    """Raised when get()/update() targets a proposal_id with no row."""


class SpxProposalRepository:
    def __init__(self, db_path: str = "spx0dte.sqlite"):
        self._db_path = db_path
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path, timeout=30.0)
        try:
            conn.execute("PRAGMA busy_timeout = 30000")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def save(self, proposal: OptionTradeProposal) -> None:
        """Insert-or-replace by proposal_id -- callers persist each new
        immutable state transition (with_decision/expire already
        return a NEW object; this just writes its current snapshot)."""
        row = _to_row(proposal)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO spx_proposals (
                    proposal_id, date, direction, strike, occ_symbol, entry_time,
                    hold_cap_minutes, estimated_premium, vix, filters_passed, created_at,
                    approval_state, decided_by, decided_at, approval_expires_at, expired_at
                ) VALUES (
                    :proposal_id, :date, :direction, :strike, :occ_symbol, :entry_time,
                    :hold_cap_minutes, :estimated_premium, :vix, :filters_passed, :created_at,
                    :approval_state, :decided_by, :decided_at, :approval_expires_at, :expired_at
                )
                ON CONFLICT(proposal_id) DO UPDATE SET
                    approval_state=excluded.approval_state,
                    decided_by=excluded.decided_by,
                    decided_at=excluded.decided_at,
                    approval_expires_at=excluded.approval_expires_at,
                    expired_at=excluded.expired_at
                """,
                row,
            )

    def get(self, proposal_id: str) -> Optional[OptionTradeProposal]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute("SELECT * FROM spx_proposals WHERE proposal_id = ?", (proposal_id,))
            row = cur.fetchone()
        return _from_row(row) if row is not None else None

    def list_pending(self) -> list:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT * FROM spx_proposals WHERE approval_state = ? ORDER BY created_at",
                (ProposalApprovalState.PENDING.value,),
            )
            rows = cur.fetchall()
        return [_from_row(r) for r in rows]

    def recent_daily_counts(self, *, before_date: str, limit: int) -> list:
        """How many proposals were created on each of the `limit`
        most recent distinct trading days strictly BEFORE
        `before_date` (never including it -- today's own count is
        still accumulating and must not bias its own trailing
        window). Self-updating history for `live_loop`'s "unusual
        day" notice (D-0095-A, 2026-11-18): no manual input, derived
        from this bot's own real trade history, the same way
        `exit_logic.DailyPaceTracker` self-updates from recent pace
        instead of a fixed constant."""
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT date, COUNT(*) as cnt FROM spx_proposals
                WHERE date < ?
                GROUP BY date
                ORDER BY date DESC
                LIMIT ?
                """,
                (before_date, limit),
            )
            rows = cur.fetchall()
        return [r[1] for r in rows]


class SpxPositionRepository:
    """Separate class, same db file as SpxProposalRepository (shares
    `_SCHEMA`'s bootstrap) -- a position always exists in reference to
    an already-persisted proposal, but is its own table/lifecycle
    (open -> closed once), not a column bolted onto `spx_proposals`."""

    def __init__(self, db_path: str = "spx0dte.sqlite"):
        self._db_path = db_path
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path, timeout=30.0)
        try:
            conn.execute("PRAGMA busy_timeout = 30000")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def save(self, position: OpenPosition) -> None:
        row = _position_to_row(position)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO spx_positions (
                    proposal_id, occ_symbol, direction, quantity, strike, date,
                    entry_time, entry_price, hold_cap_minutes,
                    is_closed, exit_time, exit_price, exit_reason
                ) VALUES (
                    :proposal_id, :occ_symbol, :direction, :quantity, :strike, :date,
                    :entry_time, :entry_price, :hold_cap_minutes,
                    :is_closed, :exit_time, :exit_price, :exit_reason
                )
                ON CONFLICT(proposal_id) DO UPDATE SET
                    is_closed=excluded.is_closed,
                    exit_time=excluded.exit_time,
                    exit_price=excluded.exit_price,
                    exit_reason=excluded.exit_reason
                """,
                row,
            )

    def get(self, proposal_id: str) -> Optional[OpenPosition]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute("SELECT * FROM spx_positions WHERE proposal_id = ?", (proposal_id,))
            row = cur.fetchone()
        return _position_from_row(row) if row is not None else None

    def list_open(self) -> list:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute("SELECT * FROM spx_positions WHERE is_closed = 0 ORDER BY entry_time")
            rows = cur.fetchall()
        return [_position_from_row(r) for r in rows]


def _position_to_row(p: OpenPosition) -> dict:
    return {
        "proposal_id": p.proposal_id,
        "occ_symbol": p.occ_symbol,
        "direction": p.direction,
        "quantity": p.quantity,
        "strike": p.strike,
        "date": p.date,
        "entry_time": p.entry_time.isoformat(),
        "entry_price": p.entry_price,
        "hold_cap_minutes": p.hold_cap_minutes,
        "is_closed": 1 if p.is_closed else 0,
        "exit_time": _dt(p.exit_time),
        "exit_price": p.exit_price,
        "exit_reason": p.exit_reason,
    }


def _position_from_row(row: sqlite3.Row) -> OpenPosition:
    return OpenPosition(
        proposal_id=row["proposal_id"],
        occ_symbol=row["occ_symbol"],
        direction=row["direction"],
        quantity=row["quantity"],
        strike=row["strike"],
        date=row["date"],
        entry_time=datetime.fromisoformat(row["entry_time"]),
        entry_price=row["entry_price"],
        hold_cap_minutes=row["hold_cap_minutes"],
        is_closed=bool(row["is_closed"]),
        exit_time=_parse_dt(row["exit_time"]),
        exit_price=row["exit_price"],
        exit_reason=row["exit_reason"],
    )


def _dt(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def _to_row(p: OptionTradeProposal) -> dict:
    return {
        "proposal_id": p.proposal_id,
        "date": p.date,
        "direction": p.direction,
        "strike": p.strike,
        "occ_symbol": p.occ_symbol,
        "entry_time": p.entry_time.isoformat(),
        "hold_cap_minutes": p.hold_cap_minutes,
        "estimated_premium": p.estimated_premium,
        "vix": p.vix,
        "filters_passed": p.filters_passed,
        "created_at": p.created_at.isoformat(),
        "approval_state": p.approval_state.value,
        "decided_by": p.decided_by,
        "decided_at": _dt(p.decided_at),
        "approval_expires_at": _dt(p.approval_expires_at),
        "expired_at": _dt(p.expired_at),
    }


def _from_row(row: sqlite3.Row) -> OptionTradeProposal:
    return OptionTradeProposal(
        proposal_id=row["proposal_id"],
        date=row["date"],
        direction=row["direction"],
        strike=row["strike"],
        occ_symbol=row["occ_symbol"],
        entry_time=datetime.fromisoformat(row["entry_time"]),
        hold_cap_minutes=row["hold_cap_minutes"],
        estimated_premium=row["estimated_premium"],
        vix=row["vix"],
        filters_passed=row["filters_passed"],
        created_at=datetime.fromisoformat(row["created_at"]),
        approval_state=ProposalApprovalState(row["approval_state"]),
        decided_by=row["decided_by"],
        decided_at=_parse_dt(row["decided_at"]),
        approval_expires_at=_parse_dt(row["approval_expires_at"]),
        expired_at=_parse_dt(row["expired_at"]),
    )
