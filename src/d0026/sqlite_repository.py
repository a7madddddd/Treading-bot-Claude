"""SQLite-backed SnapshotRepository (B22, migration 0006).

Snapshots are immutable. `save()` refuses to overwrite an existing
snapshot_id with different content (byte-identical resave is a
no-op, matching InMemorySnapshotRepository).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from typing import Optional, Tuple

from d0026.evidence import (
    ConfidenceStatus, DecisionType, EvidenceConditionId, EvidenceQuality,
)
from d0026.repository import SnapshotAlreadyExistsError, SnapshotRepository
from d0026.snapshot import ApprovedUniverseSnapshot, SnapshotSymbolEntry
from persistence.db import transaction


class SqliteSnapshotRepository(SnapshotRepository):
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def save(self, snapshot: ApprovedUniverseSnapshot) -> None:
        existing = self.get_by_id(snapshot.snapshot_id)
        if existing is not None:
            if existing == snapshot:
                return  # byte-identical resave, per contract
            raise SnapshotAlreadyExistsError(
                f"snapshot_id {snapshot.snapshot_id!r} already exists with "
                "different content -- snapshot is immutable once approved"
            )
        with transaction(self._conn) as tconn:
            tconn.execute(
                "INSERT INTO universe_snapshots ("
                " snapshot_id, effective_trading_date, snapshot_at, "
                " selection_version, universe_source_version, "
                " identity_mapping_version, regime_label, is_empty, "
                " empty_reason, symbols_json, rejection_summary_json, "
                " concentration_json, data_quality_json"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    snapshot.snapshot_id,
                    snapshot.effective_trading_date.isoformat(),
                    snapshot.snapshot_at.isoformat(),
                    snapshot.selection_version,
                    snapshot.universe_source_version,
                    snapshot.identity_mapping_version,
                    snapshot.regime_label,
                    1 if snapshot.is_empty else 0,
                    snapshot.empty_reason,
                    json.dumps([_symbol_to_dict(s)
                                for s in snapshot.symbols]),
                    json.dumps(list(snapshot.rejection_summary)),
                    json.dumps(list(snapshot.concentration_check_results)),
                    json.dumps(list(snapshot.data_quality_summary)),
                ),
            )

    def get_by_id(self, snapshot_id: str) -> Optional[ApprovedUniverseSnapshot]:
        cur = self._conn.execute(
            "SELECT * FROM universe_snapshots WHERE snapshot_id = ?",
            (snapshot_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return _snapshot_from_row(dict(zip([c[0] for c in cur.description], row)))

    def get_latest_for_date(
        self, effective_trading_date: date
    ) -> Optional[ApprovedUniverseSnapshot]:
        cur = self._conn.execute(
            "SELECT * FROM universe_snapshots "
            "WHERE effective_trading_date = ? "
            "ORDER BY snapshot_at DESC LIMIT 1",
            (effective_trading_date.isoformat(),),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return _snapshot_from_row(dict(zip([c[0] for c in cur.description], row)))


# ---- serialization helpers -----------------------------------------

def _symbol_to_dict(s: SnapshotSymbolEntry) -> dict:
    return {
        "security_id": s.security_id,
        "ticker_as_of_date": s.ticker_as_of_date,
        "rank": s.rank,
        "score_summary": [list(t) for t in s.score_summary],
        "first_seen_by_universe_at": s.first_seen_by_universe_at.isoformat(),
        "sector": s.sector,
        "identity_confidence": s.identity_confidence,
        "decision_type": s.decision_type.value,
        "evidence_quality": s.evidence_quality.value,
        "evidence_fired_conditions": [c.value
                                       for c in s.evidence_fired_conditions],
        "evidence_policy_version": s.evidence_policy_version,
        "feature_definition_version": s.feature_definition_version,
        "confidence": s.confidence,
        "confidence_status": s.confidence_status.value,
        "risk": s.risk,
        "risk_status": s.risk_status.value,
    }


def _symbol_from_dict(d: dict) -> SnapshotSymbolEntry:
    return SnapshotSymbolEntry(
        security_id=d["security_id"],
        ticker_as_of_date=d["ticker_as_of_date"],
        rank=d["rank"],
        score_summary=tuple(
            (t[0], t[1]) for t in d.get("score_summary", [])
        ),
        first_seen_by_universe_at=date.fromisoformat(
            d["first_seen_by_universe_at"]
        ),
        sector=d.get("sector"),
        identity_confidence=d["identity_confidence"],
        decision_type=DecisionType(d["decision_type"]),
        evidence_quality=EvidenceQuality(d["evidence_quality"]),
        evidence_fired_conditions=tuple(
            EvidenceConditionId(c)
            for c in d.get("evidence_fired_conditions", [])
        ),
        evidence_policy_version=d["evidence_policy_version"],
        feature_definition_version=d["feature_definition_version"],
        confidence=d.get("confidence"),
        confidence_status=ConfidenceStatus(
            d.get("confidence_status", ConfidenceStatus.NOT_CALIBRATED.value)
        ),
        risk=d.get("risk"),
        risk_status=ConfidenceStatus(
            d.get("risk_status", ConfidenceStatus.NOT_CALIBRATED.value)
        ),
    )


def _snapshot_from_row(row: dict) -> ApprovedUniverseSnapshot:
    return ApprovedUniverseSnapshot(
        snapshot_id=row["snapshot_id"],
        snapshot_at=datetime.fromisoformat(row["snapshot_at"]),
        effective_trading_date=date.fromisoformat(
            row["effective_trading_date"]
        ),
        selection_version=row["selection_version"],
        universe_source_version=row["universe_source_version"],
        identity_mapping_version=row["identity_mapping_version"],
        regime_label=row["regime_label"],
        symbols=tuple(_symbol_from_dict(d)
                      for d in json.loads(row["symbols_json"])),
        rejection_summary=tuple(
            (t[0], t[1]) for t in json.loads(row["rejection_summary_json"])
        ),
        concentration_check_results=tuple(
            (t[0], t[1]) for t in json.loads(row["concentration_json"])
        ),
        data_quality_summary=tuple(
            (t[0], t[1]) for t in json.loads(row["data_quality_json"])
        ),
        is_empty=bool(row["is_empty"]),
        empty_reason=row["empty_reason"],
    )
