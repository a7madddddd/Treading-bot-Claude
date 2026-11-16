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
