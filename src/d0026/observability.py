"""Audit event schema for full universe-refresh auditability
(2026-09-15 implementation-plan discussion §G).

Every pipeline run should be able to log, at minimum: candidates
considered, candidates rejected (with reason), data-quality failures,
identity-resolution failures, regime classification, ranking result,
concentration checks, final Top-N, EMPTY/CRASH outcome, snapshot id, and
every source-version reference the snapshot depended on. This module
defines the event *shapes* and a sink interface; it wires no real
logging backend (no file, no database, no external service).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional, Tuple

from .evidence import DecisionType, EvidenceConditionId, EvidenceQuality
from .failure import CrashCategory
from .identity import IdentityResolution
from .models import (
    PipelineStage,
    RejectionReasonCategory,
    RegimeState,
    UniverseCandidate,
)


@dataclass(frozen=True)
class AuditEvent:
    """Base shape every audit event shares."""

    as_of_date: date
    recorded_at: datetime


@dataclass(frozen=True)
class CandidateConsideredEvent(AuditEvent):
    candidate: UniverseCandidate
    stage: PipelineStage


@dataclass(frozen=True)
class CandidateRejectedEvent(AuditEvent):
    candidate: UniverseCandidate
    stage: PipelineStage
    category: RejectionReasonCategory
    detail: str


@dataclass(frozen=True)
class IdentityResolutionEvent(AuditEvent):
    resolution: IdentityResolution


@dataclass(frozen=True)
class RegimeClassifiedEvent(AuditEvent):
    regime_state: RegimeState


@dataclass(frozen=True)
class RankingComputedEvent(AuditEvent):
    ranked_security_ids: Tuple[str, ...]


@dataclass(frozen=True)
class ConcentrationCheckEvent(AuditEvent):
    check_name: str
    outcome: str


@dataclass(frozen=True)
class SnapshotPublishedEvent(AuditEvent):
    snapshot_id: str
    is_empty: bool
    empty_reason: Optional[str]


@dataclass(frozen=True)
class PipelineCrashEvent(AuditEvent):
    category: CrashCategory
    detail: str


@dataclass(frozen=True)
class EvidenceClassifiedEvent(AuditEvent):
    """Recorded for EVERY candidate reaching evidence classification —
    including NOT_ELIGIBLE ones. This is what makes NOT_ELIGIBLE
    candidates internally auditable even though they are never visible
    to the Controller (no snapshot entry, no Telegram message) — per
    the frozen D-0026 Evidence/Confidence Layer design."""

    security_id: str
    evidence_quality: EvidenceQuality
    decision_type: DecisionType
    fired_conditions: Tuple[EvidenceConditionId, ...]
    evidence_policy_version: str
    feature_definition_version: str


@dataclass(frozen=True)
class ApprovalRecordedEvent(AuditEvent):
    security_id: str
    snapshot_id: str
    approved: bool
    decision_type_at_evaluation: DecisionType
    evidence_quality_at_evaluation: EvidenceQuality


class AuditSink(ABC):
    @abstractmethod
    def record(self, event: AuditEvent) -> None:
        raise NotImplementedError


class InMemoryAuditSink(AuditSink):
    """Test/scaffolding-only sink. Holds events in a plain list for the
    lifetime of the process; touches no file, database, or network."""

    def __init__(self) -> None:
        self._events: List[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self._events.append(event)

    @property
    def events(self) -> Tuple[AuditEvent, ...]:
        return tuple(self._events)
