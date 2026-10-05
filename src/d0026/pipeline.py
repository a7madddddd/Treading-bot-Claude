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
from typing import Callable, Dict, Mapping, Optional, Tuple

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
from .publish import build_snapshot_symbols
from .repository import SnapshotRepository
from .snapshot import ApprovedUniverseSnapshot, SnapshotSymbolEntry


class InsufficientCandidatePoolError(RuntimeError):
    """P-036 (2026-10-05). Raised when the provider returns far fewer
    raw candidates than a real whole-market fetch would.

    This is deliberately a CRASH, not an EMPTY. The distinction in
    failure.py is "the pipeline completed normally and nothing
    survived" (EMPTY) versus "this run is not evidence about the
    market" (CRASH). A 40-candidate run against an 11,683-symbol
    market is the second: its percentile stages computed percentiles
    of the wrong population, so even a plausible-looking survivor list
    means nothing.

    Crashing also has a property an empty snapshot does not: no
    snapshot is written at all, so a good snapshot already published
    for the same trading date survives untouched. An empty snapshot
    would become the latest row for that date and silently replace it.
    """


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
        feature_enricher: Optional[Callable] = None,
        min_raw_candidates: int = 0,
    ) -> None:
        """`feature_enricher` (optional): a callable that takes a
        resolved UniverseCandidate and returns a new UniverseCandidate
        with `bar` and/or `features` populated. Runs AFTER identity
        resolution and BEFORE the eight stage evaluators. When None,
        candidates flow into the stages with bar=None, features=None
        (Stage A/B will then reject them all as MISSING_MARKET_DATA
        -- a valid EMPTY outcome, no crash). Signature:
            (UniverseCandidate, date, RegimeState) -> UniverseCandidate.

        `min_raw_candidates` (P-036, default 0 = disabled): the smallest
        raw-candidate pool this run will accept before refusing to
        publish anything. 0 preserves the historical behavior exactly,
        which is what every existing caller and test relies on; the
        production runner sets a real value. See
        InsufficientCandidatePoolError for why this is a crash.
        """
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
        self._feature_enricher = feature_enricher
        if min_raw_candidates < 0:
            raise ValueError("min_raw_candidates must be >= 0")
        self._min_raw_candidates = min_raw_candidates

    def run(self, as_of_date: date, regime_state: RegimeState) -> PipelineOutcome:
        now = datetime.utcnow()
        try:
            return self._run_unsafe(as_of_date, regime_state, now)
        except InsufficientCandidatePoolError as exc:
            crash = CrashOutcome(
                as_of_date=as_of_date,
                category=CrashCategory.INSUFFICIENT_CANDIDATE_POOL,
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

        # P-036: refuse before spending any enrichment call. Checked
        # here rather than after the stages so a truncated run costs
        # nothing and cannot reach the repository at all.
        if (self._min_raw_candidates > 0
                and len(raw_candidates) < self._min_raw_candidates):
            raise InsufficientCandidatePoolError(
                f"provider returned {len(raw_candidates)} raw candidates, "
                f"below the configured minimum of "
                f"{self._min_raw_candidates}. Refusing to publish: the "
                f"percentage stages would rank a population that is not "
                f"the market. No snapshot was written, so any snapshot "
                f"already published for {as_of_date} is untouched."
            )

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
            if self._feature_enricher is not None:
                candidate = self._feature_enricher(
                    candidate, as_of_date, regime_state,
                )
            candidates += (candidate,)

        # Snapshot the post-identity, post-enrichment pool before the
        # stages consume `candidates`, so P-038's counters describe what
        # entered stage A rather than what survived stage H.
        candidates_after_identity = candidates

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

        # Under the percentage-only D-0048 evaluators, candidates
        # surviving to this point form the final ranked pool (Ranking
        # ordered them descending; TopN cut to at most `top_n`).
        # Convert survivors into SelectedCandidateEntry + evidence-
        # classified SnapshotSymbolEntry so the published snapshot
        # actually contains the selected symbols. Under
        # NotCalibratedStageEvaluator (pre-D-0048 fallback) this
        # section is unreachable because those stubs raise before
        # returning a non-empty survivor pool.
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
        else:
            symbols = build_snapshot_symbols(
                candidates, regime_state=regime_state,
                as_of_date=as_of_date,
            )
            if not symbols:
                is_empty = True
                empty_reason = (
                    "all Top-N survivors were routed away by the "
                    "post-pipeline classification layer"
                )
            selected = tuple(
                SelectedCandidateEntry(
                    candidate=c, rank=i + 1, score_summary=(),
                )
                for i, c in enumerate(candidates)
            )

        # P-038 (2026-10-05): populate the data-quality summary, which
        # was hardcoded to () since the pipeline was written. On
        # 2026-10-05 the worst universe run to date published a snapshot
        # whose data-quality section was completely empty, so there was
        # no record of how much of the market the run had actually seen.
        # Every counter below is derived from values already in hand --
        # no extra work, no extra API call.
        enriched = sum(1 for c in candidates_after_identity
                       if c.features is not None)
        data_quality: Tuple[Tuple[str, int], ...] = (
            ("raw_candidates_fetched", len(raw_candidates)),
            ("identity_resolved", len(candidates_after_identity)),
            ("identity_rejected",
             len(raw_candidates) - len(candidates_after_identity)),
            ("enriched_with_features", enriched),
            ("missing_features",
             len(candidates_after_identity) - enriched),
            ("survivors_to_snapshot", len(candidates)),
            ("min_raw_candidates_configured", self._min_raw_candidates),
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
            data_quality_summary=data_quality,
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
