import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from spx0dte.position import OpenPosition
from spx0dte.proposal import OptionTradeProposal, ProposalApprovalState
from spx0dte.sqlite_repository import SpxPositionRepository, SpxProposalRepository


def _make(proposal_id="SPX-1", **overrides) -> OptionTradeProposal:
    defaults = dict(
        proposal_id=proposal_id,
        date="2026-11-16",
        direction="call",
        strike=6900,
        occ_symbol="SPXW261116C06900000",
        entry_time=datetime(2026, 11, 16, 14, 45, tzinfo=timezone.utc),
        hold_cap_minutes=40.0,
        estimated_premium=1.50,
        vix=18.0,
        filters_passed="VIX OK",
        created_at=datetime(2026, 11, 16, 14, 45, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return OptionTradeProposal(**defaults)


class TestSpxProposalRepository(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self.repo = SpxProposalRepository(self.path)

    def tearDown(self):
        os.remove(self.path)

    def test_save_and_get_roundtrip(self):
        p = _make()
        self.repo.save(p)
        got = self.repo.get("SPX-1")
        self.assertIsNotNone(got)
        self.assertEqual(got.occ_symbol, p.occ_symbol)
        self.assertEqual(got.approval_state, ProposalApprovalState.PENDING)

    def test_get_missing_returns_none(self):
        self.assertIsNone(self.repo.get("does-not-exist"))

    def test_update_transition_persists(self):
        p = _make()
        self.repo.save(p)
        now = datetime(2026, 11, 16, 14, 50, tzinfo=timezone.utc)
        decided = p.with_decision(approved=True, decided_by="42", decided_at=now)
        self.repo.save(decided)

        got = self.repo.get("SPX-1")
        self.assertEqual(got.approval_state, ProposalApprovalState.APPROVED)
        self.assertEqual(got.decided_by, "42")
        self.assertEqual(got.approval_expires_at, now + timedelta(minutes=5))

    def test_list_pending_excludes_decided(self):
        self.repo.save(_make(proposal_id="SPX-1"))
        self.repo.save(_make(proposal_id="SPX-2").with_decision(
            approved=True, decided_by="42",
            decided_at=datetime(2026, 11, 16, 14, 50, tzinfo=timezone.utc),
        ))
        pending = self.repo.list_pending()
        self.assertEqual([p.proposal_id for p in pending], ["SPX-1"])

    def test_reopening_same_file_preserves_data(self):
        self.repo.save(_make())
        reopened = SpxProposalRepository(self.path)
        self.assertIsNotNone(reopened.get("SPX-1"))


class TestRecentDailyCounts(unittest.TestCase):
    """D-0095-A, 2026-11-18 -- self-updating unusual-day threshold
    source: how many proposals were created on each of the most
    recent trading days, strictly before a given date."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self.repo = SpxProposalRepository(self.path)

    def tearDown(self):
        os.remove(self.path)

    def _seed(self, date: str, count: int) -> None:
        for i in range(count):
            self.repo.save(_make(proposal_id=f"{date}-{i}", date=date))

    def test_empty_repo_returns_empty(self):
        self.assertEqual(self.repo.recent_daily_counts(before_date="2026-11-18", limit=20), [])

    def test_counts_per_distinct_date(self):
        self._seed("2026-11-10", 3)
        self._seed("2026-11-11", 1)
        counts = self.repo.recent_daily_counts(before_date="2026-11-18", limit=20)
        self.assertEqual(sorted(counts), [1, 3])

    def test_excludes_the_before_date_itself(self):
        self._seed("2026-11-17", 5)
        self._seed("2026-11-16", 2)
        counts = self.repo.recent_daily_counts(before_date="2026-11-17", limit=20)
        self.assertEqual(counts, [2])  # today's own 5 must not count

    def test_respects_limit_keeping_most_recent(self):
        for d in range(1, 8):
            self._seed(f"2026-11-{d:02d}", d)
        counts = self.repo.recent_daily_counts(before_date="2026-11-20", limit=3)
        self.assertEqual(sorted(counts), [5, 6, 7])  # the 3 most recent dates


def _make_position(proposal_id="SPX-1", **overrides) -> OpenPosition:
    defaults = dict(
        proposal_id=proposal_id, occ_symbol="SPXW261116C06900000", direction="call",
        quantity=1, strike=6900, date="2026-11-16",
        entry_time=datetime(2026, 11, 16, 14, 45, tzinfo=timezone.utc),
        entry_price=1.50, hold_cap_minutes=40.0,
    )
    defaults.update(overrides)
    return OpenPosition(**defaults)


class TestSpxPositionRepository(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self.repo = SpxPositionRepository(self.path)

    def tearDown(self):
        os.remove(self.path)

    def test_save_and_get_roundtrip(self):
        p = _make_position()
        self.repo.save(p)
        got = self.repo.get("SPX-1")
        self.assertIsNotNone(got)
        self.assertEqual(got.occ_symbol, p.occ_symbol)
        self.assertFalse(got.is_closed)

    def test_get_missing_returns_none(self):
        self.assertIsNone(self.repo.get("does-not-exist"))

    def test_close_transition_persists(self):
        p = _make_position()
        self.repo.save(p)
        closed = p.with_exit(
            exit_time=datetime(2026, 11, 16, 15, 25, tzinfo=timezone.utc),
            exit_price=2.70, exit_reason="target",
        )
        self.repo.save(closed)

        got = self.repo.get("SPX-1")
        self.assertTrue(got.is_closed)
        self.assertEqual(got.exit_price, 2.70)
        self.assertEqual(got.exit_reason, "target")

    def test_list_open_excludes_closed(self):
        self.repo.save(_make_position(proposal_id="SPX-1"))
        closed = _make_position(proposal_id="SPX-2").with_exit(
            exit_time=datetime(2026, 11, 16, 15, 25, tzinfo=timezone.utc),
            exit_price=1.0, exit_reason="stop_loss",
        )
        self.repo.save(closed)

        open_positions = self.repo.list_open()
        self.assertEqual([p.proposal_id for p in open_positions], ["SPX-1"])

    def test_shares_db_file_with_proposal_repository(self):
        # Both repositories point at the same file and must not clobber
        # each other's tables (separate CREATE TABLE statements in one schema).
        proposal_repo = SpxProposalRepository(self.path)
        proposal_repo.save(_make(proposal_id="SPX-1"))
        self.repo.save(_make_position(proposal_id="SPX-1"))

        self.assertIsNotNone(proposal_repo.get("SPX-1"))
        self.assertIsNotNone(self.repo.get("SPX-1"))


if __name__ == "__main__":
    unittest.main()
