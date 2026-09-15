"""SnapshotRepository immutability-at-the-persistence-layer tests."""

import unittest
from datetime import date, datetime

from d0026.repository import InMemorySnapshotRepository, SnapshotAlreadyExistsError
from d0026.snapshot import ApprovedUniverseSnapshot


def _snapshot(
    *, effective_trading_date: date = date(2026, 1, 5), empty_reason: str = "no candidates"
) -> ApprovedUniverseSnapshot:
    return ApprovedUniverseSnapshot.build(
        snapshot_at=datetime(2026, 1, 5, 8, 30, 0),
        effective_trading_date=effective_trading_date,
        selection_version="v1",
        universe_source_version="p1",
        identity_mapping_version="i1",
        regime_label="unknown",
        symbols=(),
        rejection_summary=(),
        concentration_check_results=(),
        data_quality_summary=(),
        is_empty=True,
        empty_reason=empty_reason,
    )


class TestInMemorySnapshotRepository(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = InMemorySnapshotRepository()

    def test_save_then_get_round_trips(self) -> None:
        snap = _snapshot()
        self.repo.save(snap)
        self.assertEqual(self.repo.get_by_id(snap.snapshot_id), snap)

    def test_get_missing_id_returns_none(self) -> None:
        self.assertIsNone(self.repo.get_by_id("does-not-exist"))

    def test_saving_identical_content_twice_is_a_no_op(self) -> None:
        snap = _snapshot()
        self.repo.save(snap)
        self.repo.save(snap)  # must not raise — same id, same content
        self.assertEqual(self.repo.get_by_id(snap.snapshot_id), snap)

    def test_saving_different_content_under_a_colliding_id_is_rejected(self) -> None:
        # In practice a hash collision with different content shouldn't
        # happen, but the repository must defend the invariant
        # explicitly rather than trusting the hash never collides.
        snap = _snapshot()
        self.repo.save(snap)
        forged = ApprovedUniverseSnapshot(
            snapshot_id=snap.snapshot_id,  # deliberately colliding id
            snapshot_at=snap.snapshot_at,
            effective_trading_date=snap.effective_trading_date,
            selection_version=snap.selection_version,
            universe_source_version=snap.universe_source_version,
            identity_mapping_version=snap.identity_mapping_version,
            regime_label=snap.regime_label,
            symbols=(),
            rejection_summary=(),
            concentration_check_results=(),
            data_quality_summary=(),
            is_empty=True,
            empty_reason="a DIFFERENT reason under the same id",
        )
        with self.assertRaises(SnapshotAlreadyExistsError):
            self.repo.save(forged)

    def test_get_latest_for_date_returns_most_recently_produced(self) -> None:
        earlier = _snapshot(empty_reason="earlier run")
        later = ApprovedUniverseSnapshot.build(
            snapshot_at=datetime(2026, 1, 5, 14, 30, 0),
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
            empty_reason="later run",
        )
        self.repo.save(earlier)
        self.repo.save(later)
        latest = self.repo.get_latest_for_date(date(2026, 1, 5))
        self.assertEqual(latest, later)

    def test_get_latest_for_date_with_no_snapshots_returns_none(self) -> None:
        self.assertIsNone(self.repo.get_latest_for_date(date(2099, 1, 1)))


if __name__ == "__main__":
    unittest.main()
