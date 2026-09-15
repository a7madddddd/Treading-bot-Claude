"""Controller approval records for MANUAL_REVIEW (and STANDARD)
candidates.

An ``ApprovalRecord`` is a separate, append-only fact layered on top of
an evidence classification — it never mutates the classification it
refers to. ``decision_type_at_evaluation`` and
``evidence_quality_at_evaluation`` are COPIED at approval time, not
referenced, so that even if some future process changed how a symbol is
classified going forward, an existing approval record cannot silently
inherit a different classification retroactively.

Per the frozen design: approving a MANUAL_REVIEW candidate means "I have
reviewed the evidence and authorize this specific proposal despite the
stated uncertainty." It does not, and structurally cannot, upgrade
``decision_type`` or ``evidence_quality`` anywhere — there is no code
path in this module that writes back to a snapshot or a classification.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional

from .evidence import DecisionType, EvidenceQuality


@dataclass(frozen=True)
class ApprovalRecord:
    """Immutable. One record per (security, snapshot) approval decision.
    Frozen copies of the classification state at approval time — never
    a live reference that could drift if the source snapshot changed."""

    security_id: str
    snapshot_id: str
    decision_type_at_evaluation: DecisionType
    evidence_quality_at_evaluation: EvidenceQuality
    evidence_policy_version_at_evaluation: str
    feature_definition_version_at_evaluation: str
    approved: bool
    approved_at: datetime
    controller_identity: str

    def __post_init__(self) -> None:
        if self.decision_type_at_evaluation is DecisionType.NOT_ELIGIBLE:
            raise ValueError(
                "a NOT_ELIGIBLE candidate never reaches a snapshot and can "
                "never have an approval record — this would indicate a "
                "routing violation upstream"
            )
        if not self.security_id:
            raise ValueError("security_id must be non-empty")
        if not self.snapshot_id:
            raise ValueError("snapshot_id must be non-empty")
        if not self.controller_identity:
            raise ValueError("controller_identity must be non-empty")


class ApprovalAlreadyExistsError(RuntimeError):
    """Raised when attempting to record a second approval decision for
    the same (security_id, snapshot_id) pair. An approval decision is
    immutable once recorded — a changed mind is a new decision on a new
    snapshot, never an overwrite of the historical record."""


class ApprovalRepository(ABC):
    @abstractmethod
    def save(self, record: ApprovalRecord) -> None:
        raise NotImplementedError

    @abstractmethod
    def get(self, security_id: str, snapshot_id: str) -> Optional[ApprovalRecord]:
        raise NotImplementedError


class InMemoryApprovalRepository(ApprovalRepository):
    """Test/scaffolding-only implementation. Holds records in a plain
    dict for the lifetime of the process; touches no file, database, or
    network."""

    def __init__(self) -> None:
        self._by_key: Dict[tuple, ApprovalRecord] = {}

    def save(self, record: ApprovalRecord) -> None:
        key = (record.security_id, record.snapshot_id)
        if key in self._by_key:
            existing = self._by_key[key]
            if existing == record:
                return
            raise ApprovalAlreadyExistsError(
                f"an approval record already exists for security "
                f"{record.security_id!r} on snapshot {record.snapshot_id!r} "
                "with different content — approval decisions are immutable"
            )
        self._by_key[key] = record

    def get(self, security_id: str, snapshot_id: str) -> Optional[ApprovalRecord]:
        return self._by_key.get((security_id, snapshot_id))
