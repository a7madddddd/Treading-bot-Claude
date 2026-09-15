"""ApprovedUniverseSnapshot — the sole object the Strategy Engine may
ever consume from this subsystem (docs/architecture/universe.md §1-2).

Immutable and deterministically hashed from its own content: two
independent pipeline runs with identical inputs (selection version,
universe source version, identity mapping version, effective trading
date, and final symbol list) produce identical ``snapshot_id`` values.
This is a testable determinism property that requires no numeric D-0026
parameter to exist — it only depends on the *structure* being
deterministic, not on what the eventual calibrated values are.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional, Tuple

from .evidence import (
    ConfidenceStatus,
    DecisionType,
    EvidenceClassification,
    EvidenceConditionId,
    EvidenceQuality,
)
from .models import SelectedCandidateEntry


@dataclass(frozen=True)
class SnapshotSymbolEntry:
    """One security's entry in a published snapshot. Deliberately does
    not carry a bare ticker as its primary reference — ``security_id``
    is the anchor (identity.py); ``ticker_as_of_date`` is recorded for
    display/audit only.

    Evidence/Confidence Layer fields (v3-final frozen design,
    2026-09-15): ``decision_type`` is constrained to MANUAL_REVIEW or
    STANDARD only — a NOT_ELIGIBLE candidate never becomes a
    SnapshotSymbolEntry at all (enforced in __post_init__ below, not
    just by caller convention), which is the structural implementation
    of "NOT_ELIGIBLE candidates must not become snapshot entries and
    must not generate Telegram recommendations." ``confidence``/``risk``
    remain locked to None/NOT_CALIBRATED at this layer too (D0026-EV
    frozen policy) — see evidence.EvidenceClassification for the
    authoritative enforcement.
    """

    security_id: str
    ticker_as_of_date: str
    rank: int
    score_summary: Tuple[Tuple[str, float], ...]
    first_seen_by_universe_at: date
    sector: Optional[str]
    identity_confidence: str  # IdentityConfidence.value, kept as str to
    # avoid a circular import; see identity.IdentityConfidence for the
    # authoritative enum.
    decision_type: DecisionType
    evidence_quality: EvidenceQuality
    evidence_fired_conditions: Tuple[EvidenceConditionId, ...]
    evidence_policy_version: str
    feature_definition_version: str
    confidence: Optional[float] = None
    confidence_status: ConfidenceStatus = ConfidenceStatus.NOT_CALIBRATED
    risk: Optional[float] = None
    risk_status: ConfidenceStatus = ConfidenceStatus.NOT_CALIBRATED

    def __post_init__(self) -> None:
        if self.decision_type is DecisionType.NOT_ELIGIBLE:
            raise ValueError(
                "a NOT_ELIGIBLE candidate must never become a "
                "SnapshotSymbolEntry (D-0026 Evidence/Confidence Layer "
                "frozen routing rule) — exclude it before constructing "
                "this object, and record it via EvidenceClassifiedEvent "
                "instead"
            )
        if self.confidence is not None or self.confidence_status is not ConfidenceStatus.NOT_CALIBRATED:
            raise ValueError("confidence must remain None/NOT_CALIBRATED (frozen policy)")
        if self.risk is not None or self.risk_status is not ConfidenceStatus.NOT_CALIBRATED:
            raise ValueError("risk must remain None/NOT_CALIBRATED (frozen policy)")

    def as_canonical(self) -> tuple:
        return (
            self.security_id,
            self.ticker_as_of_date,
            self.rank,
            tuple(self.score_summary),
            self.first_seen_by_universe_at.isoformat(),
            self.sector,
            self.identity_confidence,
            self.decision_type.value,
            self.evidence_quality.value,
            tuple(c.value for c in self.evidence_fired_conditions),
            self.evidence_policy_version,
            self.feature_definition_version,
            self.confidence,
            self.confidence_status.value,
            self.risk,
            self.risk_status.value,
        )


def _compute_snapshot_id(
    *,
    effective_trading_date: date,
    selection_version: str,
    universe_source_version: str,
    identity_mapping_version: str,
    symbols: Tuple[SnapshotSymbolEntry, ...],
    rejection_summary: Tuple[Tuple[str, int], ...],
    concentration_check_results: Tuple[Tuple[str, str], ...],
    data_quality_summary: Tuple[Tuple[str, int], ...],
    is_empty: bool,
    empty_reason: Optional[str],
) -> str:
    """Pure function: same content in, same hash out. Deliberately
    excludes ``snapshot_at`` (wall-clock production time) — two runs with
    identical content produced at different times must still hash
    identically, since ``snapshot_at`` is provenance metadata, not
    content."""

    canonical = {
        "effective_trading_date": effective_trading_date.isoformat(),
        "selection_version": selection_version,
        "universe_source_version": universe_source_version,
        "identity_mapping_version": identity_mapping_version,
        "symbols": [entry.as_canonical() for entry in symbols],
        "rejection_summary": list(rejection_summary),
        "concentration_check_results": list(concentration_check_results),
        "data_quality_summary": list(data_quality_summary),
        "is_empty": is_empty,
        "empty_reason": empty_reason,
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ApprovedUniverseSnapshot:
    """The immutable, dated, published output of the universe pipeline.

    This is the only object the Strategy Engine may depend on
    (docs/architecture/universe.md §1). Nothing on this class implies
    how it was produced, and the Strategy Engine must never branch on
    ``regime`` — it is informational/audit-only, unchanged from the
    frozen architecture.

    Construct via ``ApprovedUniverseSnapshot.build(...)``, not the raw
    constructor, so ``snapshot_id`` is always the computed content hash
    rather than a caller-supplied value.
    """

    snapshot_id: str
    snapshot_at: datetime
    effective_trading_date: date
    selection_version: str
    universe_source_version: str
    identity_mapping_version: str
    regime_label: str  # RegimeLabel.value; informational only, see above
    symbols: Tuple[SnapshotSymbolEntry, ...]
    rejection_summary: Tuple[Tuple[str, int], ...]
    concentration_check_results: Tuple[Tuple[str, str], ...]
    data_quality_summary: Tuple[Tuple[str, int], ...]
    is_empty: bool
    empty_reason: Optional[str]

    def __post_init__(self) -> None:
        if self.is_empty and self.symbols:
            raise ValueError("an empty snapshot must not carry any symbols")
        if self.is_empty and not self.empty_reason:
            raise ValueError("an empty snapshot must carry an empty_reason")
        if not self.is_empty and self.empty_reason:
            raise ValueError("empty_reason must be unset on a non-empty snapshot")
        if not self.is_empty and not self.symbols:
            raise ValueError(
                "a non-empty snapshot must carry at least one symbol; use "
                "is_empty=True with a reason instead of an empty symbol list"
            )

    @classmethod
    def build(
        cls,
        *,
        snapshot_at: datetime,
        effective_trading_date: date,
        selection_version: str,
        universe_source_version: str,
        identity_mapping_version: str,
        regime_label: str,
        symbols: Tuple[SnapshotSymbolEntry, ...],
        rejection_summary: Tuple[Tuple[str, int], ...],
        concentration_check_results: Tuple[Tuple[str, str], ...],
        data_quality_summary: Tuple[Tuple[str, int], ...],
        is_empty: bool,
        empty_reason: Optional[str],
    ) -> "ApprovedUniverseSnapshot":
        snapshot_id = _compute_snapshot_id(
            effective_trading_date=effective_trading_date,
            selection_version=selection_version,
            universe_source_version=universe_source_version,
            identity_mapping_version=identity_mapping_version,
            symbols=symbols,
            rejection_summary=rejection_summary,
            concentration_check_results=concentration_check_results,
            data_quality_summary=data_quality_summary,
            is_empty=is_empty,
            empty_reason=empty_reason,
        )
        return cls(
            snapshot_id=snapshot_id,
            snapshot_at=snapshot_at,
            effective_trading_date=effective_trading_date,
            selection_version=selection_version,
            universe_source_version=universe_source_version,
            identity_mapping_version=identity_mapping_version,
            regime_label=regime_label,
            symbols=symbols,
            rejection_summary=rejection_summary,
            concentration_check_results=concentration_check_results,
            data_quality_summary=data_quality_summary,
            is_empty=is_empty,
            empty_reason=empty_reason,
        )


def build_snapshot_symbol_entries(
    classified_candidates: Tuple[Tuple[SelectedCandidateEntry, EvidenceClassification], ...],
    *,
    as_of_date: date,
) -> Tuple[SnapshotSymbolEntry, ...]:
    """Routes ranked, evidence-classified candidates into snapshot
    entries per the frozen D-0026 Evidence/Confidence Layer design
    (2026-09-15): NOT_ELIGIBLE candidates are excluded entirely — they
    never become a SnapshotSymbolEntry (also enforced structurally in
    SnapshotSymbolEntry.__post_init__ above, so this function cannot
    accidentally violate that rule even if a future caller mishandles
    it). MANUAL_REVIEW and STANDARD candidates both become entries,
    distinguished only by their ``decision_type``/``evidence_quality``
    fields — routing/visibility metadata, never a ranking input
    (D0026-EV-INV-1/2).

    ``as_of_date`` supplies ``first_seen_by_universe_at`` as a same-day
    value — Phase A has no persistent, multi-day "first seen" tracking
    yet, so this is the honest, non-invented default (true unless a
    future persistence layer proves an earlier date), not a guess at
    history.

    ``sector`` is not sourced anywhere in the current Phase A models
    and is left ``None`` — not guessed.

    This function does not decide, compute, or influence ``rank`` or
    ``score_summary`` — both are taken as-is from the already-ranked
    ``SelectedCandidateEntry`` (Stage F's output). No calibrated Stage F
    implementation exists yet in this codebase; this function is ready
    for whichever future implementation produces one.
    """

    entries = []
    for selected, classification in classified_candidates:
        if classification.decision_type is DecisionType.NOT_ELIGIBLE:
            continue
        identity = selected.candidate.identity_resolution.identity
        assert identity is not None, (
            "classify_evidence guarantees any non-NOT_ELIGIBLE decision "
            "implies a RESOLVED identity"
        )
        entries.append(
            SnapshotSymbolEntry(
                security_id=identity.security_id,
                ticker_as_of_date=selected.candidate.raw.ticker,
                rank=selected.rank,
                score_summary=selected.score_summary,
                first_seen_by_universe_at=as_of_date,
                sector=None,
                identity_confidence=identity.confidence.value,
                decision_type=classification.decision_type,
                evidence_quality=classification.evidence_quality,
                evidence_fired_conditions=classification.fired_conditions,
                evidence_policy_version=classification.evidence_policy_version,
                feature_definition_version=classification.feature_definition_version,
            )
        )
    return tuple(entries)
