"""ApprovedUniverseSnapshot immutability and determinism tests."""

import dataclasses
import unittest
from datetime import date, datetime

from d0026.evidence import DecisionType, EvidenceQuality
from d0026.snapshot import ApprovedUniverseSnapshot, SnapshotSymbolEntry


def _build(*, snapshot_at: datetime, symbols=()) -> ApprovedUniverseSnapshot:
    is_empty = not symbols
    return ApprovedUniverseSnapshot.build(
        snapshot_at=snapshot_at,
        effective_trading_date=date(2026, 1, 5),
        selection_version="v-test-1",
        universe_source_version="provider-test-1",
        identity_mapping_version="identity-test-1",
        regime_label="unclassified_pending_calibration",
        symbols=symbols,
        rejection_summary=(),
        concentration_check_results=(),
        data_quality_summary=(),
        is_empty=is_empty,
        empty_reason=("no candidates" if is_empty else None),
    )


class TestSnapshotImmutability(unittest.TestCase):
    def test_fields_cannot_be_reassigned(self) -> None:
        snapshot = _build(snapshot_at=datetime(2026, 1, 5, 12, 0, 0))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            snapshot.is_empty = False  # type: ignore[misc]

    def test_symbols_is_a_tuple_not_a_list(self) -> None:
        snapshot = _build(snapshot_at=datetime(2026, 1, 5, 12, 0, 0))
        self.assertIsInstance(snapshot.symbols, tuple)

    def test_empty_snapshot_requires_reason(self) -> None:
        with self.assertRaises(ValueError):
            ApprovedUniverseSnapshot.build(
                snapshot_at=datetime(2026, 1, 5, 12, 0, 0),
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
                empty_reason=None,
            )

    def test_non_empty_snapshot_must_have_symbols(self) -> None:
        with self.assertRaises(ValueError):
            ApprovedUniverseSnapshot.build(
                snapshot_at=datetime(2026, 1, 5, 12, 0, 0),
                effective_trading_date=date(2026, 1, 5),
                selection_version="v1",
                universe_source_version="p1",
                identity_mapping_version="i1",
                regime_label="unknown",
                symbols=(),
                rejection_summary=(),
                concentration_check_results=(),
                data_quality_summary=(),
                is_empty=False,
                empty_reason=None,
            )

    def test_non_empty_snapshot_must_not_have_reason(self) -> None:
        entry = SnapshotSymbolEntry(
            security_id="sec-1",
            ticker_as_of_date="AAA",
            rank=1,
            score_summary=(("placeholder_metric", 0.0),),
            first_seen_by_universe_at=date(2026, 1, 5),
            sector=None,
            identity_confidence="resolved_cik",
            decision_type=DecisionType.STANDARD,
            evidence_quality=EvidenceQuality.HIGH,
            evidence_fired_conditions=(),
            evidence_policy_version="D0026-EV-001",
            feature_definition_version="D0026-FEATDEF-001",
        )
        with self.assertRaises(ValueError):
            ApprovedUniverseSnapshot.build(
                snapshot_at=datetime(2026, 1, 5, 12, 0, 0),
                effective_trading_date=date(2026, 1, 5),
                selection_version="v1",
                universe_source_version="p1",
                identity_mapping_version="i1",
                regime_label="unknown",
                symbols=(entry,),
                rejection_summary=(),
                concentration_check_results=(),
                data_quality_summary=(),
                is_empty=False,
                empty_reason="should not be set",
            )


class TestSnapshotDeterminism(unittest.TestCase):
    def test_identical_content_at_different_wall_clock_times_hashes_identically(
        self,
    ) -> None:
        a = _build(snapshot_at=datetime(2026, 1, 5, 8, 30, 0))
        b = _build(snapshot_at=datetime(2026, 1, 5, 14, 30, 0))
        self.assertEqual(a.snapshot_id, b.snapshot_id)
        self.assertNotEqual(a.snapshot_at, b.snapshot_at)

    def test_different_empty_reason_changes_the_hash(self) -> None:
        base = _build(snapshot_at=datetime(2026, 1, 5, 8, 30, 0))
        different = ApprovedUniverseSnapshot.build(
            snapshot_at=datetime(2026, 1, 5, 8, 30, 0),
            effective_trading_date=date(2026, 1, 5),
            selection_version="v-test-1",
            universe_source_version="provider-test-1",
            identity_mapping_version="identity-test-1",
            regime_label="unclassified_pending_calibration",
            symbols=(),
            rejection_summary=(),
            concentration_check_results=(),
            data_quality_summary=(),
            is_empty=True,
            empty_reason="a different reason than the base fixture",
        )
        self.assertNotEqual(base.snapshot_id, different.snapshot_id)

    def test_different_effective_date_changes_the_hash(self) -> None:
        a = _build(snapshot_at=datetime(2026, 1, 5, 8, 30, 0))
        b = ApprovedUniverseSnapshot.build(
            snapshot_at=datetime(2026, 1, 5, 8, 30, 0),
            effective_trading_date=date(2026, 1, 6),
            selection_version="v-test-1",
            universe_source_version="provider-test-1",
            identity_mapping_version="identity-test-1",
            regime_label="unclassified_pending_calibration",
            symbols=(),
            rejection_summary=(),
            concentration_check_results=(),
            data_quality_summary=(),
            is_empty=True,
            empty_reason="no candidates",
        )
        self.assertNotEqual(a.snapshot_id, b.snapshot_id)


if __name__ == "__main__":
    unittest.main()
