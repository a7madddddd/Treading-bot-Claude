"""ApprovalRecord tests: approval creates a separate, frozen record and
never mutates the original evidence classification."""

import unittest
from datetime import datetime

from d0026.approval import ApprovalAlreadyExistsError, ApprovalRecord, InMemoryApprovalRepository
from d0026.evidence import DecisionType, EvidenceQuality


def _record(
    *,
    approved: bool = True,
    decision_type: DecisionType = DecisionType.MANUAL_REVIEW,
    evidence_quality: EvidenceQuality = EvidenceQuality.LOW,
) -> ApprovalRecord:
    return ApprovalRecord(
        security_id="sec-aaa",
        snapshot_id="snap-1",
        decision_type_at_evaluation=decision_type,
        evidence_quality_at_evaluation=evidence_quality,
        evidence_policy_version_at_evaluation="D0026-EV-001",
        feature_definition_version_at_evaluation="D0026-FEATDEF-001",
        approved=approved,
        approved_at=datetime(2026, 1, 5, 14, 0, 0),
        controller_identity="controller-test-user",
    )


class TestApprovalRecordInvariants(unittest.TestCase):
    def test_not_eligible_decision_type_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _record(decision_type=DecisionType.NOT_ELIGIBLE, evidence_quality=EvidenceQuality.INSUFFICIENT)

    def test_manual_review_and_standard_are_both_valid(self) -> None:
        _record(decision_type=DecisionType.MANUAL_REVIEW, evidence_quality=EvidenceQuality.LOW)
        _record(decision_type=DecisionType.STANDARD, evidence_quality=EvidenceQuality.HIGH)

    def test_reject_records_are_valid_too(self) -> None:
        record = _record(approved=False)
        self.assertFalse(record.approved)


class TestApprovalDoesNotUpgradeEvidence(unittest.TestCase):
    def test_approval_record_retains_original_manual_review_classification(self) -> None:
        record = _record(approved=True, decision_type=DecisionType.MANUAL_REVIEW, evidence_quality=EvidenceQuality.LOW)
        # The one and only place decision_type/evidence_quality live on
        # an ApprovalRecord are these frozen "_at_evaluation" fields —
        # there is no "current" or "effective" classification field an
        # approval could have upgraded into.
        self.assertEqual(record.decision_type_at_evaluation, DecisionType.MANUAL_REVIEW)
        self.assertEqual(record.evidence_quality_at_evaluation, EvidenceQuality.LOW)
        self.assertTrue(record.approved)

    def test_approving_does_not_mutate_a_snapshot_symbol_entry(self) -> None:
        from datetime import date

        from d0026.evidence import EvidenceClassification
        from d0026.snapshot import SnapshotSymbolEntry

        entry = SnapshotSymbolEntry(
            security_id="sec-aaa",
            ticker_as_of_date="AAA",
            rank=1,
            score_summary=(),
            first_seen_by_universe_at=date(2026, 1, 5),
            sector=None,
            identity_confidence="provisional",
            decision_type=DecisionType.MANUAL_REVIEW,
            evidence_quality=EvidenceQuality.LOW,
            evidence_fired_conditions=(),
            evidence_policy_version="D0026-EV-001",
            feature_definition_version="D0026-FEATDEF-001",
        )
        _record(approved=True)  # simulate the approval happening
        # The entry object itself has no mechanism to be told about the
        # approval at all — frozen dataclass, no setter, no reference
        # back from ApprovalRecord to the entry it approves.
        self.assertEqual(entry.decision_type, DecisionType.MANUAL_REVIEW)
        self.assertEqual(entry.evidence_quality, EvidenceQuality.LOW)
        del EvidenceClassification  # imported only to document the
        # related type exists; entry itself is what we assert on.


class TestInMemoryApprovalRepository(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = InMemoryApprovalRepository()

    def test_save_then_get_round_trips(self) -> None:
        record = _record()
        self.repo.save(record)
        self.assertEqual(self.repo.get("sec-aaa", "snap-1"), record)

    def test_get_missing_returns_none(self) -> None:
        self.assertIsNone(self.repo.get("nope", "nope"))

    def test_saving_identical_content_twice_is_a_no_op(self) -> None:
        record = _record()
        self.repo.save(record)
        self.repo.save(record)
        self.assertEqual(self.repo.get("sec-aaa", "snap-1"), record)

    def test_saving_a_different_decision_for_the_same_key_is_rejected(self) -> None:
        self.repo.save(_record(approved=True))
        with self.assertRaises(ApprovalAlreadyExistsError):
            self.repo.save(_record(approved=False))


if __name__ == "__main__":
    unittest.main()
