"""D0026-EV-INV-1 / D0026-EV-INV-2 boundary tests.

INV-1: Evidence Quality, Decision Type, Confidence, and Risk are
read-only, downstream-of-ranking metadata. No stage A-H, no ranking
formula, and no strategy-mechanics-fit logic may take any of these as
an input — nor may the HISTORY of a security's past classifications.

INV-2: Evidence Quality may change with evidence availability, but must
never influence trading desirability — no effect on ranking, selection,
strategy-mechanics, triggers, ladder parameters, or any calibrated
trading parameter. Routing/visibility only.

These are enforced here the same way the Strategy Engine boundary is
enforced elsewhere in this test suite (test_pipeline_boundary.py): a
static AST walk over the modules that implement Stage A-H decision
logic, asserting none of them reference evidence-layer identifiers.
"""

import ast
import unittest
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "d0026"

_EVIDENCE_IDENTIFIERS = {
    "evidence_quality",
    "decision_type",
    "EvidenceQuality",
    "DecisionType",
    "EvidenceClassification",
    "classify_evidence",
    "confidence",
    "confidence_status",
    "risk",
    "risk_status",
    # EvidenceSummary is descriptive/presentation metadata only -- it is
    # downstream of ranking exactly like the rest of the evidence layer
    # and must be just as invisible to Stage A-H orchestration/decision
    # code (D0026-EV-INV-1/2).
    "EvidenceSummary",
    "build_evidence_summary",
}

# Modules that implement, or could plausibly come to implement, Stage
# A-H decision/ranking logic. pipeline.py's StageEvaluator/
# NotCalibratedStageEvaluator classes live here today; a future
# calibrated ranking module would too.
_STAGE_LOGIC_MODULES = ("pipeline.py",)


def _names_referenced(tree: ast.AST) -> set:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.name)
    return names


class TestINV1NoStageLogicReferencesEvidenceFields(unittest.TestCase):
    def test_stage_evaluator_module_does_not_reference_evidence_identifiers(self) -> None:
        offending = []
        for filename in _STAGE_LOGIC_MODULES:
            path = _SRC_ROOT / filename
            tree = ast.parse(path.read_text(), filename=str(path))
            referenced = _names_referenced(tree)
            hit = referenced & _EVIDENCE_IDENTIFIERS
            if hit:
                offending.append((filename, hit))
        self.assertEqual(
            offending,
            [],
            "Stage A-H orchestration/decision code must never reference "
            "evidence-layer fields as an input (D0026-EV-INV-1)",
        )

    def test_not_calibrated_stage_evaluator_does_not_import_evidence_module(self) -> None:
        import inspect

        from d0026.pipeline import NotCalibratedStageEvaluator

        source = inspect.getsource(NotCalibratedStageEvaluator)
        for identifier in _EVIDENCE_IDENTIFIERS:
            self.assertNotIn(identifier, source)


class TestINV1NoHistoricalReputationInput(unittest.TestCase):
    def test_classify_evidence_has_no_way_to_accept_a_security_wide_history(self) -> None:
        import inspect

        from d0026.evidence import classify_evidence

        signature = inspect.signature(classify_evidence)
        param_names = set(signature.parameters)
        forbidden_substrings = ("history", "prior", "past", "reputation", "trust", "track_record")
        for param_name in param_names:
            lowered = param_name.lower()
            for forbidden in forbidden_substrings:
                self.assertNotIn(
                    forbidden,
                    lowered,
                    f"classify_evidence must not accept a parameter suggesting "
                    f"historical/reputation input ({param_name!r})",
                )

    def test_evidence_condition_lookup_functions_are_pure_of_module_level_mutable_state(self) -> None:
        # _classify_quality and classify_evidence must not read or write
        # any module-level mutable container that could accumulate
        # state across calls (which would be a de facto reputation
        # signal). The only module-level containers are the frozen
        # frozensets of condition IDs and the frozen FEATURE_DEFINITIONS
        # tuple -- neither is ever written to at runtime.
        import d0026.evidence as evidence_module

        self.assertIsInstance(evidence_module.FEATURE_DEFINITIONS, tuple)
        self.assertIsInstance(evidence_module._HARD_FAIL_IDS, frozenset)
        self.assertIsInstance(evidence_module._CORE_SOFT_GAP_IDS, frozenset)
        self.assertIsInstance(evidence_module._AUXILIARY_SOFT_GAP_IDS, frozenset)


class TestINV2NoRankingOrSelectionEffect(unittest.TestCase):
    def test_stage_result_type_has_no_evidence_fields(self) -> None:
        from d0026.pipeline import StageResult

        field_names = set(StageResult.__dataclass_fields__)
        self.assertFalse(field_names & _EVIDENCE_IDENTIFIERS)

    def test_selected_candidate_entry_has_no_evidence_fields(self) -> None:
        # Stage F's output type (rank, score_summary) must remain
        # entirely free of evidence-layer fields -- ranking is computed
        # and frozen before evidence classification ever runs.
        from d0026.models import SelectedCandidateEntry

        field_names = set(SelectedCandidateEntry.__dataclass_fields__)
        self.assertFalse(field_names & _EVIDENCE_IDENTIFIERS)

    def test_build_snapshot_symbol_entries_does_not_reorder_by_evidence_quality(self) -> None:
        # The assembler must preserve input order / caller-supplied rank
        # -- it must not sort or filter based on evidence_quality beyond
        # the binary NOT_ELIGIBLE exclusion already tested elsewhere.
        import inspect

        from d0026.snapshot import build_snapshot_symbol_entries

        source = inspect.getsource(build_snapshot_symbol_entries)
        self.assertNotIn("sort", source.lower())
        self.assertNotIn(".rank =", source)


if __name__ == "__main__":
    unittest.main()
