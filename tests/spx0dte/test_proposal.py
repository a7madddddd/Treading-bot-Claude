import unittest
from datetime import datetime, timedelta, timezone

from spx0dte.proposal import OptionTradeProposal, ProposalApprovalState


def _make(**overrides) -> OptionTradeProposal:
    defaults = dict(
        proposal_id="SPX-1",
        date="2026-11-16",
        direction="call",
        strike=6900,
        occ_symbol="SPXW261116C06900000",
        entry_time=datetime(2026, 11, 16, 14, 45, tzinfo=timezone.utc),
        hold_cap_minutes=40.0,
        estimated_premium=1.50,
        vix=18.0,
        filters_passed="VIX OK; calendar clear; outside lunch window",
        created_at=datetime(2026, 11, 16, 14, 45, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return OptionTradeProposal(**defaults)


class TestOptionTradeProposalValidation(unittest.TestCase):
    def test_valid_pending_proposal(self):
        p = _make()
        self.assertEqual(p.approval_state, ProposalApprovalState.PENDING)

    def test_rejects_bad_direction(self):
        with self.assertRaises(ValueError):
            _make(direction="up")

    def test_rejects_nonpositive_strike(self):
        with self.assertRaises(ValueError):
            _make(strike=0)

    def test_rejects_nonpositive_hold_cap(self):
        with self.assertRaises(ValueError):
            _make(hold_cap_minutes=0)

    def test_pending_cannot_carry_a_decision(self):
        with self.assertRaises(ValueError):
            _make(decided_by="123")


class TestWithDecision(unittest.TestCase):
    def setUp(self):
        self.p = _make()
        self.now = datetime(2026, 11, 16, 14, 50, tzinfo=timezone.utc)

    def test_approve_sets_expiry_5_minutes_out(self):
        decided = self.p.with_decision(approved=True, decided_by="42", decided_at=self.now)
        self.assertEqual(decided.approval_state, ProposalApprovalState.APPROVED)
        self.assertEqual(decided.approval_expires_at, self.now + timedelta(minutes=5))

    def test_reject_has_no_expiry(self):
        decided = self.p.with_decision(approved=False, decided_by="42", decided_at=self.now)
        self.assertEqual(decided.approval_state, ProposalApprovalState.REJECTED)
        self.assertIsNone(decided.approval_expires_at)

    def test_cannot_decide_twice(self):
        decided = self.p.with_decision(approved=True, decided_by="42", decided_at=self.now)
        with self.assertRaises(ValueError):
            decided.with_decision(approved=False, decided_by="42", decided_at=self.now)

    def test_decided_by_required(self):
        with self.assertRaises(ValueError):
            self.p.with_decision(approved=True, decided_by="", decided_at=self.now)

    def test_original_untouched(self):
        self.p.with_decision(approved=True, decided_by="42", decided_at=self.now)
        self.assertEqual(self.p.approval_state, ProposalApprovalState.PENDING)


class TestExpire(unittest.TestCase):
    def test_expires_pending(self):
        p = _make()
        now = datetime(2026, 11, 16, 15, 30, tzinfo=timezone.utc)
        expired = p.expire(expired_at=now)
        self.assertEqual(expired.approval_state, ProposalApprovalState.EXPIRED)
        self.assertEqual(expired.expired_at, now)

    def test_cannot_expire_a_decided_proposal(self):
        p = _make().with_decision(
            approved=True, decided_by="42",
            decided_at=datetime(2026, 11, 16, 14, 50, tzinfo=timezone.utc),
        )
        with self.assertRaises(ValueError):
            p.expire(expired_at=datetime(2026, 11, 16, 15, 30, tzinfo=timezone.utc))


class TestIsApprovalStillValid(unittest.TestCase):
    def test_pending_is_never_valid(self):
        p = _make()
        self.assertFalse(p.is_approval_still_valid(now=datetime.now(timezone.utc)))

    def test_rejected_is_never_valid(self):
        p = _make().with_decision(
            approved=False, decided_by="42",
            decided_at=datetime(2026, 11, 16, 14, 50, tzinfo=timezone.utc),
        )
        self.assertFalse(p.is_approval_still_valid(now=datetime.now(timezone.utc)))

    def test_approved_within_window_is_valid(self):
        decided_at = datetime(2026, 11, 16, 14, 50, tzinfo=timezone.utc)
        p = _make().with_decision(approved=True, decided_by="42", decided_at=decided_at)
        self.assertTrue(p.is_approval_still_valid(now=decided_at + timedelta(minutes=2)))

    def test_approved_past_window_is_invalid(self):
        decided_at = datetime(2026, 11, 16, 14, 50, tzinfo=timezone.utc)
        p = _make().with_decision(approved=True, decided_by="42", decided_at=decided_at)
        self.assertFalse(p.is_approval_still_valid(now=decided_at + timedelta(minutes=6)))


if __name__ == "__main__":
    unittest.main()
