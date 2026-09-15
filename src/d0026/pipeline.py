"""The nine-stage D-0026 pipeline contract
(docs/architecture/universe.md §1) and its orchestrator.

Every stage's real decision logic is deliberately unimplemented here.
``NotCalibratedStageEvaluator`` is the only concrete ``StageEvaluator``
provided, and it always raises ``CalibrationRequiredError`` the moment it
is actually invoked. This is a deliberate design choice, not an
oversight: it makes it structurally impossible for this codebase to
produce a real-looking universe selection before D-0026's calibration
gate (docs/trading/historical-data-calibration-plan.md §22) is
legitimately open. A future, calibrated implementation supplies its own
``StageEvaluator`` per stage — nothing here needs to change to allow
that, and nothing here does that work itself.

No numeric D-0026 parameter is chosen, defaulted, or implied anywhere in
this module.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, Mapping, Tuple

from .failure import CrashCategory, CrashOutcome, PipelineOutcome, SnapshotOutcome
from .identity import IdentityResolver, ResolutionOutcome
from .models import (
    ORDERED_CANDIDATE_STAGES,
    PipelineStage,
    RegimeState,
    RejectionReasonCategory,
    SelectedCandidateEntry,
    UniverseCandidate,
    UniverseSelectionRejection,
    UniverseSelectionResult,
)
from .observability import (
    AuditSink,
    CandidateRejectedEvent,
    IdentityResolutionEvent,
    PipelineCrashEvent,
    RegimeClassifiedEvent,
    SnapshotPublishedEvent,
)
from .provider import UniverseSourceProvider
from .repository import SnapshotRepository
from .snapshot import ApprovedUniverseSnapshot, SnapshotSymbolEntry


class CalibrationRequiredError(RuntimeError):
    """Raised by NotCalibratedStageEvaluator (or any evaluator that
    chooses to raise it) when real decision logic is invoked before
    D-0026 calibration data has passed the CALIBRATION READY gate.
    See docs/trading/historical-data-calibration-plan.md §22.

    The pipeline orchestrator catches this specifically and converts it
    to a CrashOutcome with category=CALIBRATION_NOT_READY — distinct
    from a generic technical failure, so a test or an operator can tell
    "this pipeline isn't calibrated yet" apart from "something broke."
    """


@dataclass(frozen=True)
class StageResult:
    survivors: Tuple[UniverseCandidate, ...]
    rejections: Tuple[UniverseSelectionRejection, ...]


class StageEvaluator(ABC):
    """One pipeline stage's interface. A calibrated implementation
    supplies the real decision logic; nothing in this module does."""

    stage: PipelineStage

    @abstractmethod
    def evaluate(
        self,
        candidates: Tuple[UniverseCandidate, ...],
        as_of_date: date,
        regime_state: RegimeState,
    ) -> StageResult:
        raise NotImplementedError


_STAGE_TO_REJECTION_CATEGORY: Mapping[PipelineStage, RejectionReasonCategory] = {
    PipelineStage.TRADABILITY: RejectionReasonCategory.FAILED_TRADABILITY,
    PipelineStage.DATA_QUALITY: RejectionReasonCategory.FAILED_DATA_QUALITY,
    PipelineStage.EXECUTION_QUALITY: RejectionReasonCategory.FAILED_EXECUTION_QUALITY,
    PipelineStage.STRATEGY_MECHANICS_FIT: (
        RejectionReasonCategory.FAILED_STRATEGY_MECHANICS_FIT
    ),
    PipelineStage.REGIME_ADAPTATION: RejectionReasonCategory.FAILED_REGIME_ADAPTATION,
    PipelineStage.CONCENTRATION: RejectionReasonCategory.FAILED_CONCENTRATION,
    PipelineStage.TOP_N: RejectionReasonCategory.NOT_IN_TOP_N,
}


class NotCalibratedStageEvaluator(StageEvaluator):
    """The only concrete StageEvaluator in this codebase.

    Raises the moment it would need to exercise real, uncalibrated
    judgment — i.e. whenever it receives at least one candidate to
    decide on. With zero input candidates there is no decision to make
    (a stage cannot "fail to calibrate" a judgment it was never asked
    to render), so it trivially passes an empty pool through unchanged.
    This is what keeps the legitimate EMPTY outcome (docs/trading/
    historical-data-calibration-plan.md — CRASH vs EMPTY semantics)
    reachable — e.g. when the provider returns no raw candidates, or
    every raw candidate fails identity resolution before reaching this
    stage — while still making it structurally impossible to produce a
    real, non-empty, uncalibrated selection.
    """

    def __init__(self, stage: PipelineStage) -> None:
        self.stage = stage

    def evaluate(
        self,
        candidates: Tuple[UniverseCandidate, ...],
        as_of_date: date,
        regime_state: RegimeState,
    ) -> StageResult:
        if not candidates:
            return StageResult(survivors=(), rejections=())
        raise CalibrationRequiredError(
            f"stage {self.stage.value} has no calibrated decision logic to "
            f"evaluate {len(candidates)} candidate(s); "
            "D-0026 CALIBRATION = BLOCKED "
            "(docs/trading/historical-data-calibration-plan.md §22)"
        )


def default_not_calibrated_evaluators() -> Dict[PipelineStage, StageEvaluator]:
    """Convenience factory: every candidate stage wired to the stub
    evaluator. This is the only evaluator set this codebase ships —
    a calibrated implementation must construct and inject its own."""

    return {stage: NotCalibratedStageEvaluator(stage) for stage in ORDERED_CANDIDATE_STAGES}


class UniversePipeline:
    """Orchestrates the nine-stage contract. Owns none of the decision
    logic (that lives in the injected StageEvaluators), the raw
    candidate source (injected UniverseSourceProvider), the identity
    layer (injected IdentityResolver), or persistence (injected
    SnapshotRepository) — this class only wires them together in the
    frozen order and enforces the CRASH/EMPTY type boundary.
    """

    def __init__(
        self,
        *,
        provider: UniverseSourceProvider,
        identity_resolver: IdentityResolver,
        stage_evaluators: Mapping[PipelineStage, StageEvaluator],
        snapshot_repository: SnapshotRepository,
        audit_sink: AuditSink,
        selection_version: str,
        universe_source_version: str,
        identity_mapping_version: str,
    ) -> None:
        missing = set(ORDERED_CANDIDATE_STAGES) - set(stage_evaluators)
        if missing:
            raise ValueError(f"missing stage evaluators for: {sorted(s.value for s in missing)}")
        self._provider = provider
        self._identity_resolver = identity_resolver
        self._stage_evaluators = dict(stage_evaluators)
        self._snapshot_repository = snapshot_repository
        self._audit_sink = audit_sink
        self._selection_version = selection_version
        self._universe_source_version = universe_source_version
        self._identity_mapping_version = identity_mapping_version

    def run(self, as_of_date: date, regime_state: RegimeState) -> PipelineOutcome:
        now = datetime.utcnow()
        try:
            return self._run_unsafe(as_of_date, regime_state, now)
        except CalibrationRequiredError as exc:
            crash = CrashOutcome(
                as_of_date=as_of_date,
                category=CrashCategory.CALIBRATION_NOT_READY,
                detail=str(exc),
            )
            self._audit_sink.record(
                PipelineCrashEvent(
                    as_of_date=as_of_date,
                    recorded_at=now,
                    category=crash.category,
                    detail=crash.detail,
                )
            )
            return crash
        except Exception as exc:  # noqa: BLE001 - deliberate: any other
            # exception is, by definition, an unexpected technical
            # failure per the CRASH definition (2026-09-15
            # implementation-plan discussion §F) and must never
            # propagate as a silently-produced snapshot.
            crash = CrashOutcome(
                as_of_date=as_of_date,
                category=CrashCategory.UNHANDLED_EXCEPTION,
                detail=f"{type(exc).__name__}: {exc}",
            )
            self._audit_sink.record(
                PipelineCrashEvent(
                    as_of_date=as_of_date,
                    recorded_at=now,
                    category=crash.category,
                    detail=crash.detail,
                )
            )
            return crash

    def _run_unsafe(
        self, as_of_date: date, regime_state: RegimeState, now: datetime
    ) -> PipelineOutcome:
        self._audit_sink.record(
            RegimeClassifiedEvent(as_of_date=as_of_date, recorded_at=now, regime_state=regime_state)
        )

        raw_candidates = self._provider.get_raw_candidates(as_of_date)

        candidates: Tuple[UniverseCandidate, ...] = ()
        all_rejections: Tuple[UniverseSelectionRejection, ...] = ()
        for raw in raw_candidates:
            resolution = self._identity_resolver.resolve(raw.ticker, raw.as_of_date)
            self._audit_sink.record(
                IdentityResolutionEvent(as_of_date=as_of_date, recorded_at=now, resolution=resolution)
            )
            candidate = UniverseCandidate(
                raw=raw, identity_resolution=resolution, bar=None, features=None
            )
            if resolution.outcome is ResolutionOutcome.UNRESOLVED:
                rejection = UniverseSelectionRejection(
                    candidate=candidate,
                    stage=PipelineStage.TRADABILITY,
                    category=RejectionReasonCategory.IDENTITY_UNRESOLVED,
                    detail=resolution.detail,
                )
                all_rejections += (rejection,)
                self._audit_sink.record(
                    CandidateRejectedEvent(
                        as_of_date=as_of_date,
                        recorded_at=now,
                        candidate=candidate,
                        stage=rejection.stage,
                        category=rejection.category,
                        detail=rejection.detail,
                    )
                )
                continue
            if resolution.outcome is ResolutionOutcome.AMBIGUOUS:
                rejection = UniverseSelectionRejection(
                    candidate=candidate,
                    stage=PipelineStage.TRADABILITY,
                    category=RejectionReasonCategory.IDENTITY_AMBIGUOUS,
                    detail=resolution.detail,
                )
                all_rejections += (rejection,)
                self._audit_sink.record(
                    CandidateRejectedEvent(
                        as_of_date=as_of_date,
                        recorded_at=now,
                        candidate=candidate,
                        stage=rejection.stage,
                        category=rejection.category,
                        detail=rejection.detail,
                    )
                )
                continue
            # RESOLVED (any confidence tier, including PROVISIONAL) flows
            # through — a provisional identity is a normal, expected,
            # fully auditable tier, not a rejection reason by itself.
            candidates += (candidate,)

        for stage in ORDERED_CANDIDATE_STAGES:
            evaluator = self._stage_evaluators[stage]
            result = evaluator.evaluate(candidates, as_of_date, regime_state)
            for rejection in result.rejections:
                self._audit_sink.record(
                    CandidateRejectedEvent(
                        as_of_date=as_of_date,
                        recorded_at=now,
                        candidate=rejection.candidate,
                        stage=rejection.stage,
                        category=rejection.category,
                        detail=rejection.detail,
                    )
                )
            all_rejections += result.rejections
            candidates = result.survivors

        # With the stub evaluators this codebase ships
        # (NotCalibratedStageEvaluator), reaching this point with a
        # non-empty `candidates` tuple is not possible: the first stage
        # to receive at least one candidate raises CalibrationRequiredError
        # rather than deciding anything, which the caller converts to a
        # CrashOutcome. Only a genuinely empty candidate pool (no raw
        # candidates from the provider, or every raw candidate rejected
        # during identity resolution before any stage runs) can reach
        # here — which is exactly the legitimate EMPTY outcome, produced
        # without any stage having to exercise uncalibrated judgment.
        is_empty = len(candidates) == 0
        selected: Tuple[SelectedCandidateEntry, ...] = ()
        symbols: Tuple[SnapshotSymbolEntry, ...] = ()
        empty_reason = None
        if is_empty:
            empty_reason = (
                "no candidates survived to Top-N"
                if raw_candidates
                else "provider returned no raw candidates for this date"
            )

        rejection_counts: Dict[str, int] = {}
        for rejection in all_rejections:
            key = f"{rejection.stage.value}:{rejection.category.value}"
            rejection_counts[key] = rejection_counts.get(key, 0) + 1

        selection_result = UniverseSelectionResult(
            as_of_date=as_of_date,
            regime_state=regime_state,
            selected=selected,
            rejections=all_rejections,
            is_empty=is_empty,
            empty_reason=empty_reason,
            produced_at=now,
        )
        del selection_result  # internal result constructed for its own
        # invariant checks (__post_init__) and for a future observability
        # hook; the published artifact is the snapshot below.

        snapshot = ApprovedUniverseSnapshot.build(
            snapshot_at=now,
            effective_trading_date=as_of_date,
            selection_version=self._selection_version,
            universe_source_version=self._universe_source_version,
            identity_mapping_version=self._identity_mapping_version,
            regime_label=regime_state.label.value,
            symbols=symbols,
            rejection_summary=tuple(sorted(rejection_counts.items())),
            concentration_check_results=(),
            data_quality_summary=(),
            is_empty=is_empty,
            empty_reason=empty_reason,
        )
        self._snapshot_repository.save(snapshot)
        self._audit_sink.record(
            SnapshotPublishedEvent(
                as_of_date=as_of_date,
                recorded_at=now,
                snapshot_id=snapshot.snapshot_id,
                is_empty=snapshot.is_empty,
                empty_reason=snapshot.empty_reason,
            )
        )
        return SnapshotOutcome(snapshot=snapshot)
