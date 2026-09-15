"""CRASH vs. EMPTY tests against the full pipeline orchestrator, using
only synthetic/in-memory components (no real data source anywhere)."""

import unittest
from datetime import date

from d0026.failure import CrashCategory, CrashOutcome, SnapshotOutcome
from d0026.identity import IdentityConfidence, SecurityIdentity, StaticAliasIdentityResolver, TickerAlias
from d0026.models import RegimeLabel, RegimeState
from d0026.observability import InMemoryAuditSink, PipelineCrashEvent, SnapshotPublishedEvent
from d0026.pipeline import UniversePipeline, default_not_calibrated_evaluators
from d0026.provider import InMemoryUniverseSourceProvider
from d0026.repository import InMemorySnapshotRepository

_EVAL_DATE = date(2026, 1, 5)


def _blank_regime() -> RegimeState:
    return RegimeState(
        as_of_date=_EVAL_DATE,
        label=RegimeLabel.UNKNOWN,
        reference_series_values=(),
        classification_method_version="none-pending-calibration",
    )


def _known_ticker_resolver() -> StaticAliasIdentityResolver:
    identity = SecurityIdentity(
        security_id="aaa-corp",
        display_name="AAA Corp",
        cik="0000000001",
        confidence=IdentityConfidence.RESOLVED_CIK,
    )
    alias = TickerAlias(
        security_id="aaa-corp", ticker="AAA", effective_start=None, effective_end=None
    )
    return StaticAliasIdentityResolver([identity], [alias])


def _build_pipeline(*, candidates_by_date, identity_resolver, repository=None, audit_sink=None):
    return UniversePipeline(
        provider=InMemoryUniverseSourceProvider(candidates_by_date),
        identity_resolver=identity_resolver,
        stage_evaluators=default_not_calibrated_evaluators(),
        snapshot_repository=repository or InMemorySnapshotRepository(),
        audit_sink=audit_sink or InMemoryAuditSink(),
        selection_version="v-test",
        universe_source_version="provider-test",
        identity_mapping_version="identity-test",
    )


class TestEmptyOutcomes(unittest.TestCase):
    def test_no_raw_candidates_produces_a_published_empty_snapshot(self) -> None:
        repo = InMemorySnapshotRepository()
        sink = InMemoryAuditSink()
        pipeline = _build_pipeline(
            candidates_by_date={},
            identity_resolver=_known_ticker_resolver(),
            repository=repo,
            audit_sink=sink,
        )
        outcome = pipeline.run(_EVAL_DATE, _blank_regime())
        self.assertIsInstance(outcome, SnapshotOutcome)
        self.assertTrue(outcome.snapshot.is_empty)
        self.assertTrue(outcome.snapshot.empty_reason)
        self.assertEqual(repo.get_by_id(outcome.snapshot.snapshot_id), outcome.snapshot)
        published = [e for e in sink.events if isinstance(e, SnapshotPublishedEvent)]
        self.assertEqual(len(published), 1)
        self.assertTrue(published[0].is_empty)

    def test_all_candidates_failing_identity_resolution_produces_empty_snapshot(
        self,
    ) -> None:
        # "UNKNOWN" is not in the resolver's alias set at all -> UNRESOLVED
        # for every raw candidate -> zero candidates reach stage A ->
        # legitimate EMPTY, not a crash.
        repo = InMemorySnapshotRepository()
        pipeline = _build_pipeline(
            candidates_by_date={_EVAL_DATE: ["UNKNOWN"]},
            identity_resolver=_known_ticker_resolver(),
            repository=repo,
        )
        outcome = pipeline.run(_EVAL_DATE, _blank_regime())
        self.assertIsInstance(outcome, SnapshotOutcome)
        self.assertTrue(outcome.snapshot.is_empty)


class TestCrashOutcomes(unittest.TestCase):
    def test_a_real_candidate_reaching_a_stub_stage_crashes_not_faked_empty(
        self,
    ) -> None:
        # AAA resolves successfully, so it reaches stage A with a real
        # decision required — the stub evaluator must refuse (crash),
        # never silently produce an empty (or any) snapshot as if a real
        # decision had been made.
        repo = InMemorySnapshotRepository()
        sink = InMemoryAuditSink()
        pipeline = _build_pipeline(
            candidates_by_date={_EVAL_DATE: ["AAA"]},
            identity_resolver=_known_ticker_resolver(),
            repository=repo,
            audit_sink=sink,
        )
        outcome = pipeline.run(_EVAL_DATE, _blank_regime())
        self.assertIsInstance(outcome, CrashOutcome)
        self.assertEqual(outcome.category, CrashCategory.CALIBRATION_NOT_READY)
        self.assertIsNone(repo.get_latest_for_date(_EVAL_DATE))
        crash_events = [e for e in sink.events if isinstance(e, PipelineCrashEvent)]
        self.assertEqual(len(crash_events), 1)
        published = [e for e in sink.events if isinstance(e, SnapshotPublishedEvent)]
        self.assertEqual(published, [])

    def test_an_unexpected_exception_crashes_and_publishes_nothing(self) -> None:
        class ExplodingProvider:
            def get_raw_candidates(self, as_of_date):
                raise RuntimeError("simulated infrastructure failure")

        repo = InMemorySnapshotRepository()
        pipeline = UniversePipeline(
            provider=ExplodingProvider(),  # type: ignore[arg-type]
            identity_resolver=_known_ticker_resolver(),
            stage_evaluators=default_not_calibrated_evaluators(),
            snapshot_repository=repo,
            audit_sink=InMemoryAuditSink(),
            selection_version="v-test",
            universe_source_version="provider-test",
            identity_mapping_version="identity-test",
        )
        outcome = pipeline.run(_EVAL_DATE, _blank_regime())
        self.assertIsInstance(outcome, CrashOutcome)
        self.assertEqual(outcome.category, CrashCategory.UNHANDLED_EXCEPTION)
        self.assertIsNone(repo.get_latest_for_date(_EVAL_DATE))


class TestCrashAndEmptyAreStructurallyDistinct(unittest.TestCase):
    def test_outcome_is_exactly_one_of_the_two_shapes(self) -> None:
        crash = CrashOutcome(
            as_of_date=_EVAL_DATE, category=CrashCategory.UNHANDLED_EXCEPTION, detail="x"
        )
        self.assertFalse(hasattr(crash, "snapshot"))

    def test_neither_outcome_silently_creates_proposals(self) -> None:
        # Neither PipelineOutcome shape carries any concept of a
        # "proposal" at all — proposals belong to the Strategy Engine,
        # which this subsystem has zero dependency on (see
        # test_pipeline_boundary.py). This test documents that absence
        # rather than asserting a specific field, since no such field
        # exists to assert against — which is the point.
        crash = CrashOutcome(
            as_of_date=_EVAL_DATE, category=CrashCategory.UNHANDLED_EXCEPTION, detail="x"
        )
        self.assertNotIn("proposal", crash.__dataclass_fields__)


if __name__ == "__main__":
    unittest.main()
