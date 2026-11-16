import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from spx0dte.proposal import OptionTradeProposal, ProposalApprovalState
from spx0dte.sqlite_repository import SpxProposalRepository


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


if __name__ == "__main__":
    unittest.main()
