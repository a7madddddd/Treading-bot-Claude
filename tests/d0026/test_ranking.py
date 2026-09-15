"""D-0026 Stage F (Ranking) Option 3 boundary scaffolding tests.

Covers the inert plumbing added in ranking.py: RankingMetricDefinition,
the dormant RANKING_METRIC_DEFINITIONS registry, compute_ranking_score_
summary(), and build_selected_candidate_entries(). None of this is
wired into pipeline.py — these tests exercise ranking.py in isolation.
"""

import ast
import dataclasses
import inspect
import unittest
from datetime import date

from d0026.identity import IdentityResolution, ResolutionOutcome
from d0026.models import RawCandidateRef, RegimeLabel, RegimeState, UniverseCandidate
from d0026.ranking import (
    RANKING_METRIC_DEFINITION_VERSION,
    RANKING_METRIC_DEFINITIONS,
    RankingMetricDefinition,
    build_selected_candidate_entries,
    compute_ranking_score_summary,
)

_DATE = date(2026, 1, 5)


def _regime() -> RegimeState:
    return RegimeState(
        as_of_date=_DATE,
        label=RegimeLabel.UNCLASSIFIED_PENDING_CALIBRATION,
        reference_series_values=(("dummy_ref", 1.0),),
        classification_method_version="none-pending-calibration",
    )


def _candidate(ticker: str) -> UniverseCandidate:
    resolution = IdentityResolution(
        outcome=ResolutionOutcome.UNRESOLVED, ticker=ticker, as_of_date=_DATE, detail="synthetic-fixture, no alias"
    )
    return UniverseCandidate(
        raw=RawCandidateRef(ticker=ticker, as_of_date=_DATE),
        identity_resolution=resolution,
        bar=None,
        features=None,
    )


class TestRankingMetricDefinitionShape(unittest.TestCase):
    def test_ranking_metric_definition_has_no_calibration_fields(self) -> None:
        forbidden_substrings = (
            "weight",
            "threshold",
            "direction",
            "score",
            "regime_adjustment",
            "priority",
            "ranking_value",
            "calibrat",
        )
        for field in dataclasses.fields(RankingMetricDefinition):
            lowered = field.name.lower()
            for forbidden in forbidden_substrings:
                self.assertNotIn(
                    forbidden,
                    lowered,
                    f"RankingMetricDefinition field {field.name!r} suggests a calibration-related field",
                )

    def test_ranking_metric_definition_only_field_is_metric_id(self) -> None:
        field_names = {field.name for field in dataclasses.fields(RankingMetricDefinition)}
        self.assertEqual(field_names, {"metric_id"})


class TestDormantRegistry(unittest.TestCase):
    def test_registry_is_empty(self) -> None:
        self.assertEqual(RANKING_METRIC_DEFINITIONS, ())

    def test_registry_is_a_tuple_not_a_mutable_container(self) -> None:
        self.assertIsInstance(RANKING_METRIC_DEFINITIONS, tuple)

    def test_registry_version_matches_established_constant(self) -> None:
        self.assertEqual(RANKING_METRIC_DEFINITION_VERSION, "D0026-RANK-METRICDEF-001")


class TestComputeRankingScoreSummaryDormancy(unittest.TestCase):
    def test_returns_empty_tuple_against_default_registry(self) -> None:
        result = compute_ranking_score_summary(_candidate("AAA"), _DATE, _regime())
        self.assertEqual(result, ())

    def test_deterministic_for_identical_inputs(self) -> None:
        candidate = _candidate("AAA")
        regime = _regime()
        a = compute_ranking_score_summary(candidate, _DATE, regime)
        b = compute_ranking_score_summary(candidate, _DATE, regime)
        self.assertEqual(a, b)

    def test_non_empty_metric_definitions_raises_rather_than_fabricating(self) -> None:
        synthetic_definitions = (RankingMetricDefinition(metric_id="synthetic_metric"),)
        with self.assertRaises(NotImplementedError):
            compute_ranking_score_summary(
                _candidate("AAA"), _DATE, _regime(), metric_definitions=synthetic_definitions
            )


class TestBuildSelectedCandidateEntries(unittest.TestCase):
    def test_empty_survivors_returns_empty_tuple(self) -> None:
        self.assertEqual(build_selected_candidate_entries((), _DATE, _regime()), ())

    def test_single_survivor_gets_rank_one(self) -> None:
        candidate = _candidate("AAA")
        entries = build_selected_candidate_entries((candidate,), _DATE, _regime())
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].rank, 1)
        self.assertIs(entries[0].candidate, candidate)

    def test_multiple_survivors_receive_ranks_one_through_n(self) -> None:
        candidates = tuple(_candidate(ticker) for ticker in ("AAA", "BBB", "CCC", "DDD"))
        entries = build_selected_candidate_entries(candidates, _DATE, _regime())
        self.assertEqual([entry.rank for entry in entries], [1, 2, 3, 4])

    def test_input_ordering_is_preserved(self) -> None:
        candidates = tuple(_candidate(ticker) for ticker in ("ZZZ", "AAA", "MMM"))
        entries = build_selected_candidate_entries(candidates, _DATE, _regime())
        self.assertEqual(
            [entry.candidate.raw.ticker for entry in entries],
            ["ZZZ", "AAA", "MMM"],
        )

    def test_score_summary_comes_from_compute_ranking_score_summary(self) -> None:
        candidate = _candidate("AAA")
        entries = build_selected_candidate_entries((candidate,), _DATE, _regime())
        self.assertEqual(
            entries[0].score_summary,
            compute_ranking_score_summary(candidate, _DATE, _regime()),
        )

    @staticmethod
    def _source_body_without_docstring() -> str:
        # inspect.getsource() includes the docstring, which legitimately
        # documents (in prose) that this function does not sort/dedupe —
        # strip it so these checks inspect only executable code.
        source = inspect.getsource(build_selected_candidate_entries)
        tree = ast.parse(source)
        func_node = tree.body[0]
        assert isinstance(func_node, ast.FunctionDef)
        if (
            func_node.body
            and isinstance(func_node.body[0], ast.Expr)
            and isinstance(func_node.body[0].value, ast.Constant)
            and isinstance(func_node.body[0].value.value, str)
        ):
            code_only_body = func_node.body[1:]
        else:
            code_only_body = func_node.body
        return "\n".join(ast.unparse(node) for node in code_only_body)

    def test_no_sorting_or_reordering_in_source(self) -> None:
        code = self._source_body_without_docstring()
        self.assertNotIn("sort", code.lower())
        self.assertNotIn("reversed(", code)

    def test_no_deduplication_in_source(self) -> None:
        code = self._source_body_without_docstring()
        self.assertNotIn("set(", code)
        self.assertNotIn("dict.fromkeys", code)


class TestNoForbiddenDependencies(unittest.TestCase):
    def test_neither_function_accepts_an_evidence_parameter(self) -> None:
        forbidden_substrings = ("evidence", "confidence", "decision_type")
        for func in (compute_ranking_score_summary, build_selected_candidate_entries):
            signature = inspect.signature(func)
            for param_name in signature.parameters:
                lowered = param_name.lower()
                for forbidden in forbidden_substrings:
                    self.assertNotIn(
                        forbidden,
                        lowered,
                        f"{func.__name__} must not accept an Evidence/Confidence-related parameter ({param_name!r})",
                    )

    def test_neither_function_accepts_a_history_or_reputation_parameter(self) -> None:
        forbidden_substrings = ("history", "prior", "past", "reputation", "trust", "track_record")
        for func in (compute_ranking_score_summary, build_selected_candidate_entries):
            signature = inspect.signature(func)
            for param_name in signature.parameters:
                lowered = param_name.lower()
                for forbidden in forbidden_substrings:
                    self.assertNotIn(forbidden, lowered)

    def test_module_has_no_mutable_registry_state(self) -> None:
        import d0026.ranking as ranking_module

        self.assertIsInstance(ranking_module.RANKING_METRIC_DEFINITIONS, tuple)
        # No module-level list/dict/set exists for the registry or any
        # other cross-call state.
        for name, value in vars(ranking_module).items():
            if name.startswith("__"):
                continue
            self.assertNotIsInstance(
                value,
                (list, dict, set),
                f"module-level mutable container found: {name!r}",
            )


class TestPipelineIsolation(unittest.TestCase):
    def test_pipeline_module_does_not_reference_ranking(self) -> None:
        import ast
        from pathlib import Path

        pipeline_path = Path(__file__).resolve().parents[2] / "src" / "d0026" / "pipeline.py"
        tree = ast.parse(pipeline_path.read_text(), filename=str(pipeline_path))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    names.add(alias.name)
                if node.module:
                    names.add(node.module.split(".")[-1])

        forbidden = {
            "ranking",
            "RankingMetricDefinition",
            "RankingMetricId",
            "RANKING_METRIC_DEFINITIONS",
            "compute_ranking_score_summary",
            "build_selected_candidate_entries",
        }
        self.assertEqual(names & forbidden, set())

    def test_pipeline_source_has_no_import_of_ranking_module(self) -> None:
        from pathlib import Path

        pipeline_path = Path(__file__).resolve().parents[2] / "src" / "d0026" / "pipeline.py"
        source = pipeline_path.read_text()
        self.assertNotIn("ranking", source)


if __name__ == "__main__":
    unittest.main()
