"""SqliteProposalRepository -- the SQLite-backed ProposalRepository
implementation (D-0024).

Responsible ONLY for mechanical I/O: moving TradeProposal objects
to/from SQL rows, and managing transactions via
persistence.db.transaction (BEGIN IMMEDIATE -- never a second
transaction mechanism). ALL proposal lifecycle business logic --
Option E, PENDING supersession, action-scoped decisions, D-0007 --
stays exactly where it already lives: `plan_save_supersession()` and
`plan_decision()` in `repository.py`, and `validate_for_submission()`
in `revalidation.py`. This module calls those functions unchanged; it
never re-derives, re-checks, or duplicates any part of what they
decide.

Trade-first FK: the `proposals` table's `trade_id` column is a real,
enforced foreign key against `trades(trade_id)` (already migrated,
Controller-approved -- a Trade must exist before any Proposal
referencing it can be persisted). This module does NOT create Trade
records under any circumstance -- a save() for a nonexistent trade_id
fails clearly (TradeDoesNotExistError), exactly as designed.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional

from persistence.db import transaction

from .models import ApprovalState, TradeAction, TradeProposal
from .repository import (
    ProposalDecisionConflictError,
    ProposalRepository,
    plan_decision,
    plan_save_supersession,
)


class TradeDoesNotExistError(RuntimeError):
    """Raised by save() when the proposal's trade_id has no
    corresponding row in `trades`. This repository never creates a
    Trade record itself -- a Trade must already exist (Controller-
    approved Trade-first foreign key rule); this error makes that
    requirement's failure mode explicit and clear rather than
    surfacing as a raw sqlite3.IntegrityError."""


def _proposal_to_row(proposal: TradeProposal) -> Dict[str, Any]:
    """TradeProposal -> a flat dict matching the `proposals` table's
    columns exactly (src/persistence/migrations/0001_initial.sql)."""

    return {
        "proposal_id": proposal.proposal_id,
        "trade_id": proposal.trade_id,
        "proposed_action": proposal.proposed_action.value,
        "symbol": proposal.symbol,
        "candidate_source": proposal.candidate_source,
        "current_price_at_proposal": proposal.current_price_at_proposal,
        "proposed_entry": proposal.proposed_entry,
        "ladder_1_trigger": proposal.ladder_1_trigger,
        "ladder_1_quantity": proposal.ladder_1_quantity,
        "ladder_2_trigger": proposal.ladder_2_trigger,
        "ladder_2_quantity": proposal.ladder_2_quantity,
        "floor_trigger": proposal.floor_trigger,
        "maximum_position": proposal.maximum_position,
        "proposal_created_at": proposal.proposal_created_at.isoformat(),
        "weighted_avg_entry_at_proposal": proposal.weighted_avg_entry_at_proposal,
        "active_floor_at_proposal": proposal.active_floor_at_proposal,
        "assumptions_json": json.dumps(list(proposal.assumptions)),
        "risks_json": json.dumps(list(proposal.risks)),
        "approval_state": proposal.approval_state.value,
        "approval_received_at": _dt(proposal.approval_received_at),
        "approval_expires_at": _dt(proposal.approval_expires_at),
        "decided_by": proposal.decided_by,
        "approved_action": proposal.approved_action.value if proposal.approved_action is not None else None,
        "expired_at": _dt(proposal.expired_at),
    }


def _row_to_proposal(row: Mapping[str, Any]) -> TradeProposal:
    """A flat row -> a real TradeProposal instance. ALWAYS constructs
    through TradeProposal's real constructor -- __post_init__'s
    invariants fire here, so a corrupted/invariant-violating row raises
    (ValueError/TypeError) rather than silently producing an invalid
    object."""

    return TradeProposal(
        proposal_id=row["proposal_id"],
        trade_id=row["trade_id"],
        proposed_action=TradeAction(row["proposed_action"]),
        symbol=row["symbol"],
        candidate_source=row["candidate_source"],
        current_price_at_proposal=row["current_price_at_proposal"],
        proposed_entry=row["proposed_entry"],
        ladder_1_trigger=row["ladder_1_trigger"],
        ladder_1_quantity=row["ladder_1_quantity"],
        ladder_2_trigger=row["ladder_2_trigger"],
        ladder_2_quantity=row["ladder_2_quantity"],
        floor_trigger=row["floor_trigger"],
        maximum_position=row["maximum_position"],
        proposal_created_at=_parse_dt(row["proposal_created_at"]),
        weighted_avg_entry_at_proposal=row["weighted_avg_entry_at_proposal"],
        active_floor_at_proposal=row["active_floor_at_proposal"],
        assumptions=tuple(json.loads(row["assumptions_json"])),
        risks=tuple(json.loads(row["risks_json"])),
        approval_state=ApprovalState(row["approval_state"]),
        approval_received_at=_parse_dt(row["approval_received_at"]),
        approval_expires_at=_parse_dt(row["approval_expires_at"]),
        decided_by=row["decided_by"],
        approved_action=TradeAction(row["approved_action"]) if row["approved_action"] is not None else None,
        expired_at=_parse_dt(row["expired_at"]),
    )


def _dt(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value is not None else None


class SqliteProposalRepository(ProposalRepository):
    """Operates on an already-connected, already-bootstrapped
    sqlite3.Connection (see persistence.db.connect/bootstrap_schema --
    this class does not open or bootstrap a database itself)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row

    def save(self, proposal: TradeProposal) -> None:
        row = _proposal_to_row(proposal)

        # Duplicate proposal_id is detected by letting the real INSERT's
        # PRIMARY KEY constraint fail and translating that
        # sqlite3.IntegrityError (see below) -- never a separate
        # pre-check SELECT -- matching SqliteTradeRepository.save()'s
        # precedent for TradeAlreadyExistsError.
        with transaction(self._conn) as conn:
            if not conn.execute(
                "SELECT 1 FROM trades WHERE trade_id = ?", (proposal.trade_id,)
            ).fetchone():
                raise TradeDoesNotExistError(
                    f"cannot save proposal {proposal.proposal_id!r}: no Trade exists for "
                    f"trade_id {proposal.trade_id!r} -- a Trade must be created first "
                    "(Controller-approved Trade-first foreign key rule); this repository "
                    "never creates a Trade automatically"
                )

            # Storage-layer concern only: fetch the (trade_id, proposed_action)
            # -scoped sibling rows. The actual decision (block / supersede
            # which ids / proceed) is made entirely by the pure
            # plan_save_supersession() function -- never re-derived here.
            sibling_rows = conn.execute(
                "SELECT * FROM proposals WHERE trade_id = ? AND proposed_action = ?",
                (proposal.trade_id, proposal.proposed_action.value),
            ).fetchall()
            siblings = [_row_to_proposal(dict(r)) for r in sibling_rows]
            expire_ids = plan_save_supersession(siblings, proposal)

            siblings_by_id = {s.proposal_id: s for s in siblings}
            for proposal_id in expire_ids:
                expired = siblings_by_id[proposal_id].expire(expired_at=proposal.proposal_created_at)
                expired_row = _proposal_to_row(expired)
                set_clause = ", ".join(f"{column} = ?" for column in expired_row)
                conn.execute(
                    f"UPDATE proposals SET {set_clause} WHERE proposal_id = ?",
                    list(expired_row.values()) + [proposal_id],
                )

            columns = list(row.keys())
            placeholders = ", ".join("?" for _ in columns)
            try:
                conn.execute(
                    f"INSERT INTO proposals ({', '.join(columns)}) VALUES ({placeholders})",
                    list(row.values()),
                )
            except sqlite3.IntegrityError as exc:
                raise ProposalDecisionConflictError(
                    f"proposal_id {proposal.proposal_id!r} already exists -- "
                    "save() creates a new proposal only, use record_decision() "
                    "to transition an existing one"
                ) from exc

    def record_decision(
        self,
        proposal_id: str,
        *,
        approved: bool,
        decided_by: str,
        decided_at: datetime,
        action: TradeAction,
    ) -> TradeProposal:
        with transaction(self._conn) as conn:
            # Storage-layer concern only: fetch the current stored
            # proposal. The precondition check is made entirely by the
            # pure plan_decision() function -- never re-derived here.
            existing_row = conn.execute(
                "SELECT * FROM proposals WHERE proposal_id = ?", (proposal_id,)
            ).fetchone()
            existing = _row_to_proposal(dict(existing_row)) if existing_row is not None else None
            existing = plan_decision(existing, proposal_id)

            decided = existing.with_decision(
                approved=approved, decided_by=decided_by, decided_at=decided_at, action=action
            )
            decided_row = _proposal_to_row(decided)
            set_clause = ", ".join(f"{column} = ?" for column in decided_row)
            conn.execute(
                f"UPDATE proposals SET {set_clause} WHERE proposal_id = ?",
                list(decided_row.values()) + [proposal_id],
            )

        return decided

    def get(self, proposal_id: str) -> Optional[TradeProposal]:
        row = self._conn.execute("SELECT * FROM proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
        if row is None:
            return None
        return _row_to_proposal(dict(row))

    def list_for_symbol(self, symbol: str) -> List[TradeProposal]:
        symbol_norm = symbol.strip().upper()
        rows = self._conn.execute(
            "SELECT * FROM proposals WHERE symbol = ? ORDER BY rowid", (symbol_norm,)
        ).fetchall()
        return [_row_to_proposal(dict(r)) for r in rows]

    def list_for_trade(self, trade_id: str) -> List[TradeProposal]:
        rows = self._conn.execute(
            "SELECT * FROM proposals WHERE trade_id = ? ORDER BY rowid", (trade_id,)
        ).fetchall()
        return [_row_to_proposal(dict(r)) for r in rows]
