"""Tests for: (1) the Strategy Engine dependency boundary
(docs/architecture/universe.md §1 — the Strategy Engine may consume only
ApprovedUniverseSnapshot and must have zero dependency on anything else
in this package), (2) pipeline wiring/construction guards, and
(3) run-to-run determinism through the full orchestrator.
"""

import ast
import unittest
from datetime import date
from pathlib import Path

import d0026
from d0026.identity import IdentityConfidence, SecurityIdentity, StaticAliasIdentityResolver, TickerAlias
from d0026.models import RegimeLabel, RegimeState
from d0026.observability import InMemoryAuditSink
from d0026.pipeline import (
    NotCalibratedStageEvaluator,
    PipelineStage,
    UniversePipeline,
    default_not_calibrated_evaluators,
)
from d0026.provider import InMemoryUniverseSourceProvider
from d0026.repository import InMemorySnapshotRepository
from d0026.snapshot import ApprovedUniverseSnapshot

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "d0026"


class TestStrategyEngineBoundary(unittest.TestCase):
    def test_package_public_surface_is_limited_to_the_snapshot_type(self) -> None:
        # The Strategy Engine (not yet built) should have exactly one
        # thing to import from this subsystem.
        self.assertEqual(set(d0026.__all__), {"ApprovedUniverseSnapshot", "SnapshotSymbolEntry"})

    def test_no_module_in_this_package_imports_a_strategy_engine(self) -> None:
        # Guards the one-way dependency direction (universe subsystem
        # must not depend on the Strategy Engine) for whenever a
        # strategy_engine package is eventually created — this test will
        # start failing the moment such an import is added, which is the
        # point.
        offending = []
        for path in _SRC_ROOT.glob("*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module] if node.module else []
                else:
                    continue
                for name in names:
                    if name and "strategy_engine" in name:
                        offending.append((path.name, name))
        self.assertEqual(offending, [])

    def test_a_consumer_typed_to_only_the_snapshot_can_be_satisfied(self) -> None:
        # Demonstrates the contract shape: a hypothetical Strategy Engine
        # entry point only ever needs an ApprovedUniverseSnapshot — no
        # provider, resolver, repository, or audit sink reference.
        def fake_strategy_engine_entry_point(snapshot: ApprovedUniverseSnapshot) -> bool:
            return snapshot.is_empty or len(snapshot.symbols) >= 0

        snapshot = ApprovedUniverseSnapshot.build(
            snapshot_at=__import__("datetime").datetime(2026, 1, 5, 8, 30, 0),
            effective_trading_date=date(2026, 1, 5),
            selection_version="v1",
            universe_source_version="p1",
            identity_mapping_version="i1",
            regime_label="unknown",
            symbols=(),
            rejection_summary=(),
            concentration_check_results=(),
            data_quality_summary=(),
            is_empty=True,
            empty_reason="no candidates",
        )
        self.assertTrue(fake_strategy_engine_entry_point(snapshot))


class TestPipelineConstructionGuards(unittest.TestCase):
    def test_missing_stage_evaluator_is_rejected_at_construction(self) -> None:
        incomplete = default_not_calibrated_evaluators()
        del incomplete[PipelineStage.TOP_N]
        with self.assertRaises(ValueError):
            UniversePipeline(
                provider=InMemoryUniverseSourceProvider({}),
                identity_resolver=StaticAliasIdentityResolver([], []),
                stage_evaluators=incomplete,
                snapshot_repository=InMemorySnapshotRepository(),
                audit_sink=InMemoryAuditSink(),
                selection_version="v1",
                universe_source_version="p1",
                identity_mapping_version="i1",
            )

    def test_default_evaluators_cover_every_candidate_stage(self) -> None:
        evaluators = default_not_calibrated_evaluators()
        from d0026.models import ORDERED_CANDIDATE_STAGES

        self.assertEqual(set(evaluators.keys()), set(ORDERED_CANDIDATE_STAGES))
        for stage, evaluator in evaluators.items():
            self.assertIsInstance(evaluator, NotCalibratedStageEvaluator)
            self.assertEqual(evaluator.stage, stage)


class TestRunToRunDeterminism(unittest.TestCase):
    def test_two_independent_runs_of_an_identical_empty_scenario_agree(self) -> None:
        def build_pipeline(repo, sink):
            return UniversePipeline(
                provider=InMemoryUniverseSourceProvider({}),
                identity_resolver=StaticAliasIdentityResolver([], []),
                stage_evaluators=default_not_calibrated_evaluators(),
                snapshot_repository=repo,
                audit_sink=sink,
                selection_version="v-test",
                universe_source_version="provider-test",
                identity_mapping_version="identity-test",
            )

        regime = RegimeState(
            as_of_date=date(2026, 1, 5),
            label=RegimeLabel.UNKNOWN,
            reference_series_values=(),
            classification_method_version="none-pending-calibration",
        )

        repo_a, repo_b = InMemorySnapshotRepository(), InMemorySnapshotRepository()
        outcome_a = build_pipeline(repo_a, InMemoryAuditSink()).run(date(2026, 1, 5), regime)
        outcome_b = build_pipeline(repo_b, InMemoryAuditSink()).run(date(2026, 1, 5), regime)

        self.assertEqual(outcome_a.snapshot.snapshot_id, outcome_b.snapshot.snapshot_id)


if __name__ == "__main__":
    unittest.main()
