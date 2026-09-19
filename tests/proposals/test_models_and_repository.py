import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from proposals.models import ApprovalState, FloorContext, TradeAction, TradeProposal, approved_strategy_rule_set
from proposals.proposal import build_trade_proposal
from proposals.repository import (
    InMemoryProposalRepository,
    ProposalDecisionConflictError,
    plan_decision,
    plan_save_supersession,
)
from proposals.revalidation import validate_for_submission


def _now():
    return datetime(2026, 9, 17, 14, 0, 0, tzinfo=timezone.utc)


def _make(
    proposal_id="P-1",
    trade_id="T-1",
    action=TradeAction.INITIAL_ENTRY,
    symbol="TSLA",
    price=100.0,
    floor_context=None,
):
    return build_trade_proposal(
        proposal_id=proposal_id,
        trade_id=trade_id,
        action=action,
        symbol=symbol,
        current_price=price,
        as_of=_now(),
        strategy=approved_strategy_rule_set(),
        floor_context=floor_context if floor_context is not None else FloorContext.known(80.0),
    )


class TestApprovalTransitions(unittest.TestCase):
    def test_explicit_approval_changes_pending_to_approved(self):
        p = _make()
        decided_at = _now() + timedelta(minutes=1)
        approved = p.with_decision(
            approved=True, decided_by="controller", decided_at=decided_at, action=TradeAction.INITIAL_ENTRY
        )
        self.assertEqual(approved.approval_state, ApprovalState.APPROVED)
        self.assertEqual(approved.approval_received_at, decided_at)
        self.assertEqual(approved.approval_expires_at, decided_at + timedelta(minutes=5))
        self.assertEqual(approved.decided_by, "controller")
        self.assertEqual(approved.approved_action, TradeAction.INITIAL_ENTRY)

    def test_explicit_rejection_changes_pending_to_rejected(self):
        p = _make()
        decided_at = _now() + timedelta(minutes=1)
        rejected = p.with_decision(
            approved=False, decided_by="controller", decided_at=decided_at, action=TradeAction.INITIAL_ENTRY
        )
        self.assertEqual(rejected.approval_state, ApprovalState.REJECTED)
        self.assertIsNone(rejected.approval_expires_at)
        self.assertEqual(rejected.approved_action, TradeAction.INITIAL_ENTRY)

    def test_no_response_leaves_proposal_pending(self):
        p = _make()
        self.assertEqual(p.approval_state, ApprovalState.PENDING)
        self.assertEqual(p.approval_state, ApprovalState.PENDING)

    def test_conflicting_second_decision_on_same_object_raises(self):
        p = _make()
        decided_at = _now() + timedelta(minutes=1)
        approved = p.with_decision(
            approved=True, decided_by="controller", decided_at=decided_at, action=TradeAction.INITIAL_ENTRY
        )
        with self.assertRaises(ValueError):
            approved.with_decision(
                approved=False, decided_by="controller", decided_at=decided_at, action=TradeAction.INITIAL_ENTRY
            )

    def test_rejected_proposal_stays_rejected(self):
        p = _make()
        rejected = p.with_decision(
            approved=False, decided_by="controller", decided_at=_now(), action=TradeAction.INITIAL_ENTRY
        )
        with self.assertRaises(ValueError):
            rejected.with_decision(
                approved=True, decided_by="controller", decided_at=_now(), action=TradeAction.INITIAL_ENTRY
            )

    def test_decision_requires_valid_trade_action(self):
        p = _make()
        with self.assertRaises(TypeError):
            p.with_decision(approved=True, decided_by="controller", decided_at=_now(), action="not-an-action")


class TestProposalRepository(unittest.TestCase):
    def test_save_then_get_round_trips(self):
        repo = InMemoryProposalRepository()
        p = _make()
        repo.save(p)
        self.assertEqual(repo.get("P-1"), p)

    def test_get_missing_returns_none(self):
        repo = InMemoryProposalRepository()
        self.assertIsNone(repo.get("does-not-exist"))

    def test_saving_duplicate_proposal_id_raises(self):
        repo = InMemoryProposalRepository()
        p = _make()
        repo.save(p)
        with self.assertRaises(ProposalDecisionConflictError):
            repo.save(p)

    def test_record_decision_approve_transitions_pending_to_approved(self):
        repo = InMemoryProposalRepository()
        repo.save(_make())
        decided_at = _now() + timedelta(minutes=1)
        result = repo.record_decision(
            "P-1", approved=True, decided_by="controller", decided_at=decided_at, action=TradeAction.INITIAL_ENTRY
        )
        self.assertEqual(result.approval_state, ApprovalState.APPROVED)
        self.assertEqual(repo.get("P-1").approval_state, ApprovalState.APPROVED)
        self.assertEqual(repo.get("P-1").approved_action, TradeAction.INITIAL_ENTRY)

    def test_record_decision_reject_transitions_pending_to_rejected(self):
        repo = InMemoryProposalRepository()
        repo.save(_make(action=TradeAction.LADDER_1))
        result = repo.record_decision(
            "P-1", approved=False, decided_by="controller", decided_at=_now(), action=TradeAction.LADDER_1
        )
        self.assertEqual(result.approval_state, ApprovalState.REJECTED)
        self.assertEqual(repo.get("P-1").approval_state, ApprovalState.REJECTED)

    def test_conflicting_second_decision_for_same_id_raises(self):
        repo = InMemoryProposalRepository()
        repo.save(_make())
        repo.record_decision(
            "P-1", approved=True, decided_by="controller", decided_at=_now(), action=TradeAction.INITIAL_ENTRY
        )
        with self.assertRaises(ProposalDecisionConflictError):
            repo.record_decision(
                "P-1", approved=False, decided_by="controller", decided_at=_now(), action=TradeAction.INITIAL_ENTRY
            )

    def test_record_decision_on_unknown_id_raises(self):
        repo = InMemoryProposalRepository()
        with self.assertRaises(ProposalDecisionConflictError):
            repo.record_decision(
                "does-not-exist",
                approved=True,
                decided_by="controller",
                decided_at=_now(),
                action=TradeAction.INITIAL_ENTRY,
            )

    def test_multiple_candidates_create_independent_ids_and_state(self):
        repo = InMemoryProposalRepository()
        repo.save(_make(proposal_id="P-1", trade_id="T-1", symbol="TSLA", price=100.0))
        repo.save(_make(proposal_id="P-2", trade_id="T-2", symbol="DELL", price=50.0))
        repo.save(_make(proposal_id="P-3", trade_id="T-3", symbol="NVDA", price=200.0))

        decided_at = _now() + timedelta(minutes=1)
        approved = repo.record_decision(
            "P-1", approved=True, decided_by="controller", decided_at=decided_at, action=TradeAction.INITIAL_ENTRY
        )
        rejected = repo.record_decision(
            "P-2", approved=False, decided_by="controller", decided_at=decided_at, action=TradeAction.INITIAL_ENTRY
        )

        self.assertEqual(approved.approval_state, ApprovalState.APPROVED)
        self.assertEqual(rejected.approval_state, ApprovalState.REJECTED)
        self.assertEqual(repo.get("P-3").approval_state, ApprovalState.PENDING)
        self.assertEqual(repo.get("P-1").symbol, "TSLA")
        self.assertEqual(repo.get("P-2").symbol, "DELL")
        self.assertEqual(repo.get("P-3").symbol, "NVDA")

    def test_list_for_symbol(self):
        repo = InMemoryProposalRepository()
        repo.save(_make(proposal_id="P-1", trade_id="T-1", symbol="TSLA"))
        repo.save(_make(proposal_id="P-2", trade_id="T-2", symbol="TSLA"))
        repo.save(_make(proposal_id="P-3", trade_id="T-3", symbol="DELL"))
        tsla_proposals = repo.list_for_symbol("tsla")
        self.assertEqual({p.proposal_id for p in tsla_proposals}, {"P-1", "P-2"})


class TestRepeatedAttemptsPerTrade(unittest.TestCase):
    """Controller decision: a rejected action must be re-proposable
    later via a NEW proposal_id sharing the SAME trade_id, without
    losing or overwriting the rejected attempt's history."""

    def test_rejected_action_can_be_reproposed_with_new_proposal_id_same_trade_id(self):
        repo = InMemoryProposalRepository()
        p1 = _make(proposal_id="P-1", trade_id="T-123", action=TradeAction.INITIAL_ENTRY)
        repo.save(p1)
        repo.record_decision(
            "P-1", approved=True, decided_by="controller", decided_at=_now(), action=TradeAction.INITIAL_ENTRY
        )

        p2 = _make(proposal_id="P-2", trade_id="T-123", action=TradeAction.LADDER_1)
        repo.save(p2)
        rejected = repo.record_decision(
            "P-2",
            approved=False,
            decided_by="controller",
            decided_at=_now() + timedelta(minutes=1),
            action=TradeAction.LADDER_1,
        )
        self.assertEqual(rejected.approval_state, ApprovalState.REJECTED)

        # A later attempt for the SAME action, SAME trade -- new proposal_id.
        p3 = _make(proposal_id="P-3", trade_id="T-123", action=TradeAction.LADDER_1)
        repo.save(p3)
        approved = repo.record_decision(
            "P-3",
            approved=True,
            decided_by="controller",
            decided_at=_now() + timedelta(minutes=2),
            action=TradeAction.LADDER_1,
        )
        self.assertEqual(approved.approval_state, ApprovalState.APPROVED)

        # The rejected attempt's history is untouched.
        self.assertEqual(repo.get("P-2").approval_state, ApprovalState.REJECTED)
        self.assertEqual(repo.get("P-2").approved_action, TradeAction.LADDER_1)

    def test_list_for_trade_returns_all_attempts_in_creation_order(self):
        repo = InMemoryProposalRepository()
        # Distinct actions so none of these three attempts auto-expires
        # another -- this test is about list_for_trade's ordering, not
        # the auto-expire lifecycle rule (covered separately below).
        repo.save(_make(proposal_id="P-1", trade_id="T-123", action=TradeAction.INITIAL_ENTRY))
        repo.save(_make(proposal_id="P-2", trade_id="T-123", action=TradeAction.LADDER_1))
        repo.save(_make(proposal_id="P-3", trade_id="T-123", action=TradeAction.LADDER_2))
        repo.save(_make(proposal_id="P-4", trade_id="T-999"))

        repo.record_decision(
            "P-1", approved=True, decided_by="controller", decided_at=_now(), action=TradeAction.INITIAL_ENTRY
        )
        repo.record_decision(
            "P-2", approved=False, decided_by="controller", decided_at=_now(), action=TradeAction.LADDER_1
        )

        attempts = repo.list_for_trade("T-123")
        self.assertEqual([p.proposal_id for p in attempts], ["P-1", "P-2", "P-3"])
        self.assertEqual(attempts[0].approval_state, ApprovalState.APPROVED)
        self.assertEqual(attempts[1].approval_state, ApprovalState.REJECTED)
        self.assertEqual(attempts[2].approval_state, ApprovalState.PENDING)

    def test_list_for_trade_excludes_other_trades(self):
        repo = InMemoryProposalRepository()
        repo.save(_make(proposal_id="P-1", trade_id="T-A"))
        repo.save(_make(proposal_id="P-2", trade_id="T-B"))
        self.assertEqual([p.proposal_id for p in repo.list_for_trade("T-A")], ["P-1"])
        self.assertEqual([p.proposal_id for p in repo.list_for_trade("T-B")], ["P-2"])

    def test_list_for_trade_unknown_trade_id_returns_empty(self):
        repo = InMemoryProposalRepository()
        repo.save(_make(proposal_id="P-1", trade_id="T-A"))
        self.assertEqual(repo.list_for_trade("no-such-trade"), [])

    def test_each_proposal_still_accepts_only_one_terminal_decision(self):
        repo = InMemoryProposalRepository()
        repo.save(_make(proposal_id="P-1", trade_id="T-123", action=TradeAction.LADDER_1))
        repo.record_decision(
            "P-1", approved=False, decided_by="controller", decided_at=_now(), action=TradeAction.LADDER_1
        )
        with self.assertRaises(ProposalDecisionConflictError):
            repo.record_decision(
                "P-1", approved=True, decided_by="controller", decided_at=_now(), action=TradeAction.LADDER_1
            )


class TestAutoExpireLifecycleRule(unittest.TestCase):
    """Controller-approved lifecycle rule (PENDING half, unchanged by
    the later correction): creating a new proposal attempt for the same
    (trade_id, action) automatically expires any previously PENDING
    attempt for that same pair -- no Controller authorization is ever
    at stake in a PENDING record, so silently superseding it is safe.
    An APPROVED sibling is handled separately -- see
    TestApprovedSiblingNeverSilentlyExpired below."""

    def test_new_proposal_expires_a_still_pending_prior_attempt(self):
        repo = InMemoryProposalRepository()
        p1 = _make(proposal_id="P-1", trade_id="T-1", action=TradeAction.LADDER_1)
        repo.save(p1)
        self.assertEqual(repo.get("P-1").approval_state, ApprovalState.PENDING)

        p2 = _make(proposal_id="P-2", trade_id="T-1", action=TradeAction.LADDER_1)
        repo.save(p2)

        self.assertEqual(repo.get("P-1").approval_state, ApprovalState.EXPIRED)
        self.assertEqual(repo.get("P-1").expired_at, p2.proposal_created_at)
        self.assertEqual(repo.get("P-2").approval_state, ApprovalState.PENDING)

    def test_new_proposal_is_blocked_while_approved_sibling_is_still_d0007_valid(self):
        """Controller-corrected rule (this now supersedes the earlier,
        REJECTED design where an APPROVED sibling was silently
        auto-expired): while P1 remains APPROVED and D-0007-valid,
        creating P2 for the same (trade_id, action) is BLOCKED entirely
        -- nothing is persisted, and P1 is not touched in any way."""
        repo = InMemoryProposalRepository()
        repo.save(_make(proposal_id="P-1", trade_id="T-1", action=TradeAction.LADDER_1, price=100.0))
        repo.record_decision(
            "P-1", approved=True, decided_by="controller", decided_at=_now(), action=TradeAction.LADDER_1
        )
        p1_before = repo.get("P-1")
        self.assertEqual(p1_before.approval_state, ApprovalState.APPROVED)

        with self.assertRaises(ProposalDecisionConflictError):
            repo.save(_make(proposal_id="P-2", trade_id="T-1", action=TradeAction.LADDER_1, price=95.0))

        # P1 is completely untouched -- same object, still APPROVED.
        self.assertEqual(repo.get("P-1"), p1_before)
        self.assertEqual(repo.get("P-1").approval_state, ApprovalState.APPROVED)
        self.assertIsNone(repo.get("P-2"))

    def test_rejected_prior_attempt_is_not_touched_by_a_new_proposal(self):
        repo = InMemoryProposalRepository()
        repo.save(_make(proposal_id="P-1", trade_id="T-1", action=TradeAction.LADDER_1))
        repo.record_decision(
            "P-1", approved=False, decided_by="controller", decided_at=_now(), action=TradeAction.LADDER_1
        )
        repo.save(_make(proposal_id="P-2", trade_id="T-1", action=TradeAction.LADDER_1))

        self.assertEqual(repo.get("P-1").approval_state, ApprovalState.REJECTED)
        self.assertIsNone(repo.get("P-1").expired_at)

    def test_only_the_newest_attempt_is_active_after_several_supersessions(self):
        repo = InMemoryProposalRepository()
        repo.save(_make(proposal_id="P-1", trade_id="T-1", action=TradeAction.LADDER_1))
        repo.save(_make(proposal_id="P-2", trade_id="T-1", action=TradeAction.LADDER_1))
        repo.save(_make(proposal_id="P-3", trade_id="T-1", action=TradeAction.LADDER_1))

        self.assertEqual(repo.get("P-1").approval_state, ApprovalState.EXPIRED)
        self.assertEqual(repo.get("P-2").approval_state, ApprovalState.EXPIRED)
        self.assertEqual(repo.get("P-3").approval_state, ApprovalState.PENDING)

    def test_different_actions_on_the_same_trade_never_expire_each_other(self):
        repo = InMemoryProposalRepository()
        repo.save(_make(proposal_id="P-1", trade_id="T-1", action=TradeAction.INITIAL_ENTRY))
        repo.save(_make(proposal_id="P-2", trade_id="T-1", action=TradeAction.LADDER_1))
        repo.save(_make(proposal_id="P-3", trade_id="T-1", action=TradeAction.LADDER_2))

        self.assertEqual(repo.get("P-1").approval_state, ApprovalState.PENDING)
        self.assertEqual(repo.get("P-2").approval_state, ApprovalState.PENDING)
        self.assertEqual(repo.get("P-3").approval_state, ApprovalState.PENDING)

    def test_different_trades_never_expire_each_other(self):
        repo = InMemoryProposalRepository()
        repo.save(_make(proposal_id="P-1", trade_id="T-1", action=TradeAction.LADDER_1))
        repo.save(_make(proposal_id="P-2", trade_id="T-2", action=TradeAction.LADDER_1))

        self.assertEqual(repo.get("P-1").approval_state, ApprovalState.PENDING)
        self.assertEqual(repo.get("P-2").approval_state, ApprovalState.PENDING)

    def test_expired_proposal_cannot_later_receive_a_decision(self):
        repo = InMemoryProposalRepository()
        repo.save(_make(proposal_id="P-1", trade_id="T-1", action=TradeAction.LADDER_1))
        repo.save(_make(proposal_id="P-2", trade_id="T-1", action=TradeAction.LADDER_1))
        with self.assertRaises(ProposalDecisionConflictError):
            repo.record_decision(
                "P-1", approved=True, decided_by="controller", decided_at=_now(), action=TradeAction.LADDER_1
            )


class TestApprovedSiblingNeverSilentlyExpired(unittest.TestCase):
    """Controller-corrected Option E: an APPROVED sibling is never
    auto-expired. Creating a new proposal for the same (trade_id,
    action) is BLOCKED outright while that sibling remains D-0007-valid
    (age <= 5 min AND price within +/-0.5% of its own trigger,
    evaluated via validate_for_submission() -- never duplicated here),
    and ALLOWED -- leaving the stale APPROVED sibling's record
    untouched -- once it is no longer valid."""

    def _approve_ladder_1(self, repo, proposal_id, trade_id, price, decided_at):
        repo.save(_make(proposal_id=proposal_id, trade_id=trade_id, action=TradeAction.LADDER_1, price=price))
        repo.record_decision(
            proposal_id, approved=True, decided_by="controller", decided_at=decided_at, action=TradeAction.LADDER_1
        )

    # -- Item 2: exactly 5 minutes old, within +/-0.5% -> blocked.
    def test_exactly_five_minutes_old_still_blocks(self):
        repo = InMemoryProposalRepository()
        t0 = _now()
        self._approve_ladder_1(repo, "P-1", "T-1", price=100.0, decided_at=t0)  # ladder_1_trigger = 95.0
        p1_before = repo.get("P-1")

        new_proposal = _make(proposal_id="P-2", trade_id="T-1", action=TradeAction.LADDER_1, price=95.0)
        new_proposal = replace(new_proposal, proposal_created_at=t0 + timedelta(minutes=5))
        with self.assertRaises(ProposalDecisionConflictError):
            repo.save(new_proposal)
        self.assertEqual(repo.get("P-1"), p1_before)
        self.assertIsNone(repo.get("P-2"))

    # -- Item 3: just over 5 minutes old -> allowed, old record unchanged.
    def test_just_over_five_minutes_old_allows_creation(self):
        repo = InMemoryProposalRepository()
        t0 = _now()
        self._approve_ladder_1(repo, "P-1", "T-1", price=100.0, decided_at=t0)
        p1_before = repo.get("P-1")

        new_proposal = _make(proposal_id="P-2", trade_id="T-1", action=TradeAction.LADDER_1, price=95.0)
        new_proposal = replace(new_proposal, proposal_created_at=t0 + timedelta(minutes=5, seconds=1))
        repo.save(new_proposal)  # must not raise

        self.assertEqual(repo.get("P-1"), p1_before)
        self.assertEqual(repo.get("P-1").approval_state, ApprovalState.APPROVED)  # never auto-EXPIRED
        self.assertEqual(repo.get("P-2").approval_state, ApprovalState.PENDING)

    # -- Item 4: exactly +/-0.5% price deviation -> blocked.
    def test_exactly_half_percent_deviation_still_blocks(self):
        repo = InMemoryProposalRepository()
        t0 = _now()
        self._approve_ladder_1(repo, "P-1", "T-1", price=100.0, decided_at=t0)  # trigger = 95.0
        p1_before = repo.get("P-1")

        with self.assertRaises(ProposalDecisionConflictError):
            repo.save(
                _make(proposal_id="P-2", trade_id="T-1", action=TradeAction.LADDER_1, price=95.475)
            )  # |95.475-95|/95 == 0.005 exactly
        self.assertEqual(repo.get("P-1"), p1_before)
        self.assertIsNone(repo.get("P-2"))

    # -- Item 5: just beyond +/-0.5% -> allowed.
    def test_just_beyond_half_percent_deviation_allows_creation(self):
        repo = InMemoryProposalRepository()
        t0 = _now()
        self._approve_ladder_1(repo, "P-1", "T-1", price=100.0, decided_at=t0)  # trigger = 95.0
        p1_before = repo.get("P-1")

        repo.save(
            _make(proposal_id="P-2", trade_id="T-1", action=TradeAction.LADDER_1, price=95.48)
        )  # deviation > 0.005

        self.assertEqual(repo.get("P-1"), p1_before)
        self.assertEqual(repo.get("P-1").approval_state, ApprovalState.APPROVED)
        self.assertEqual(repo.get("P-2").approval_state, ApprovalState.PENDING)

    # -- Item 6: invalid/non-finite new current price -> clear error, no partial persistence.
    def test_invalid_current_price_on_new_proposal_raises_and_persists_nothing(self):
        repo = InMemoryProposalRepository()
        t0 = _now()
        self._approve_ladder_1(repo, "P-1", "T-1", price=100.0, decided_at=t0)

        bad_proposal = TradeProposal(
            proposal_id="P-bad",
            trade_id="T-1",
            proposed_action=TradeAction.LADDER_1,
            symbol="TSLA",
            candidate_source="fixed_watchlist",
            current_price_at_proposal=float("nan"),
            proposed_entry=100.0,
            ladder_1_trigger=95.0,
            ladder_1_quantity=10,
            ladder_2_trigger=92.0,
            ladder_2_quantity=20,
            floor_trigger=90.0,
            maximum_position=40,
            proposal_created_at=t0 + timedelta(minutes=1),
            weighted_avg_entry_at_proposal=None,
            active_floor_at_proposal=80.0,
            assumptions=(),
            risks=(),
        )
        with self.assertRaises(ValueError):
            repo.save(bad_proposal)

        self.assertIsNone(repo.get("P-bad"))
        self.assertEqual({p.proposal_id for p in repo.list_for_trade("T-1")}, {"P-1"})

    # -- Item 7 (your literal example): Ladder 1 APPROVED and still valid
    # blocks another Ladder 1 proposal.
    def test_ladder_1_approved_and_valid_blocks_another_ladder_1_proposal(self):
        repo = InMemoryProposalRepository()
        self._approve_ladder_1(repo, "P-1", "T-123", price=100.0, decided_at=_now())
        with self.assertRaises(ProposalDecisionConflictError):
            repo.save(_make(proposal_id="P-2", trade_id="T-123", action=TradeAction.LADDER_1, price=95.0))
        self.assertEqual(repo.get("P-1").approval_state, ApprovalState.APPROVED)
        self.assertIsNone(repo.get("P-2"))

    # -- Item 8: Ladder 1 APPROVED does not block an independent Ladder 2 proposal.
    def test_ladder_1_approved_does_not_block_ladder_2_proposal(self):
        repo = InMemoryProposalRepository()
        self._approve_ladder_1(repo, "P-1", "T-123", price=100.0, decided_at=_now())
        repo.save(_make(proposal_id="P-2", trade_id="T-123", action=TradeAction.LADDER_2, price=92.0))

        self.assertEqual(repo.get("P-1").approval_state, ApprovalState.APPROVED)
        self.assertEqual(repo.get("P-1").approved_action, TradeAction.LADDER_1)
        self.assertEqual(repo.get("P-2").approval_state, ApprovalState.PENDING)
        self.assertEqual(repo.get("P-2").proposed_action, TradeAction.LADDER_2)

    # -- Item 9: Ladder 1 REJECTED allows a later Ladder 1 proposal (regression).
    def test_ladder_1_rejected_allows_a_later_ladder_1_proposal(self):
        repo = InMemoryProposalRepository()
        repo.save(_make(proposal_id="P-1", trade_id="T-123", action=TradeAction.LADDER_1, price=100.0))
        repo.record_decision(
            "P-1", approved=False, decided_by="controller", decided_at=_now(), action=TradeAction.LADDER_1
        )
        repo.save(_make(proposal_id="P-2", trade_id="T-123", action=TradeAction.LADDER_1, price=95.0))

        self.assertEqual(repo.get("P-1").approval_state, ApprovalState.REJECTED)
        self.assertEqual(repo.get("P-2").approval_state, ApprovalState.PENDING)

    # -- Item 10: Ladder 2 APPROVED and still valid blocks another Ladder 2 proposal.
    def test_ladder_2_approved_and_valid_blocks_another_ladder_2_proposal(self):
        repo = InMemoryProposalRepository()
        repo.save(_make(proposal_id="P-1", trade_id="T-123", action=TradeAction.LADDER_2, price=100.0))
        repo.record_decision(
            "P-1", approved=True, decided_by="controller", decided_at=_now(), action=TradeAction.LADDER_2
        )
        with self.assertRaises(ProposalDecisionConflictError):
            repo.save(_make(proposal_id="P-2", trade_id="T-123", action=TradeAction.LADDER_2, price=92.0))
        self.assertEqual(repo.get("P-1").approval_state, ApprovalState.APPROVED)
        self.assertIsNone(repo.get("P-2"))

    # -- Item 11: same action, different trade_id -> no blocking.
    def test_same_action_different_trade_id_never_blocks(self):
        repo = InMemoryProposalRepository()
        self._approve_ladder_1(repo, "P-1", "T-1", price=100.0, decided_at=_now())
        repo.save(_make(proposal_id="P-2", trade_id="T-2", action=TradeAction.LADDER_1, price=100.0))
        self.assertEqual(repo.get("P-1").approval_state, ApprovalState.APPROVED)
        self.assertEqual(repo.get("P-2").approval_state, ApprovalState.PENDING)

    # -- Item 12: different action, same trade_id -> no blocking (APPROVED case).
    def test_different_action_same_trade_id_never_blocks(self):
        repo = InMemoryProposalRepository()
        self._approve_ladder_1(repo, "P-1", "T-1", price=100.0, decided_at=_now())
        repo.save(_make(proposal_id="P-2", trade_id="T-1", action=TradeAction.INITIAL_ENTRY, price=100.0))
        self.assertEqual(repo.get("P-1").approval_state, ApprovalState.APPROVED)
        self.assertEqual(repo.get("P-2").approval_state, ApprovalState.PENDING)

    # -- Item 13: a blocked creation leaves the repository exactly as it was.
    def test_blocked_creation_leaves_no_partial_or_orphaned_proposal(self):
        repo = InMemoryProposalRepository()
        self._approve_ladder_1(repo, "P-1", "T-1", price=100.0, decided_at=_now())
        before_ids = {p.proposal_id for p in repo.list_for_trade("T-1")}

        with self.assertRaises(ProposalDecisionConflictError):
            repo.save(_make(proposal_id="P-2", trade_id="T-1", action=TradeAction.LADDER_1, price=95.0))

        after_ids = {p.proposal_id for p in repo.list_for_trade("T-1")}
        self.assertEqual(before_ids, after_ids)
        self.assertEqual(before_ids, {"P-1"})

    # -- Item 14: the gate must agree with validate_for_submission() across
    # a grid of ages and price deviations straddling both D-0007 boundaries.
    def test_gate_agrees_with_validate_for_submission_across_boundary_grid(self):
        cases = [
            (timedelta(minutes=0), 95.0),
            (timedelta(minutes=5), 95.0),
            (timedelta(minutes=5, seconds=1), 95.0),
            (timedelta(minutes=1), 95.475),  # exactly 0.5%
            (timedelta(minutes=1), 95.48),  # just beyond 0.5%
            (timedelta(minutes=10), 95.48),  # both boundaries violated
        ]
        for age_offset, price in cases:
            with self.subTest(age_offset=age_offset, price=price):
                repo = InMemoryProposalRepository()
                t0 = _now()
                self._approve_ladder_1(repo, "P-1", "T-1", price=100.0, decided_at=t0)
                p1 = repo.get("P-1")

                now = t0 + age_offset
                expected = validate_for_submission(
                    p1,
                    trigger_price=p1.ladder_1_trigger,
                    current_price=price,
                    active_floor_price=80.0,
                    now=now,
                    action=TradeAction.LADDER_1,
                ).allowed

                new_proposal = _make(proposal_id="P-2", trade_id="T-1", action=TradeAction.LADDER_1, price=price)
                new_proposal = replace(new_proposal, proposal_created_at=now)

                if expected:
                    with self.assertRaises(ProposalDecisionConflictError):
                        repo.save(new_proposal)
                else:
                    repo.save(new_proposal)  # must not raise

    # -- Item 15: rapid move Ladder 1 -> Ladder 2 -- separate decisions,
    # Ladder 1's approval is never reused for Ladder 2.
    def test_rapid_move_ladder_1_to_ladder_2_are_independent_decisions(self):
        repo = InMemoryProposalRepository()
        self._approve_ladder_1(repo, "P-1", "T-1", price=100.0, decided_at=_now())

        repo.save(_make(proposal_id="P-2", trade_id="T-1", action=TradeAction.LADDER_2, price=92.0))
        repo.record_decision(
            "P-2", approved=True, decided_by="controller", decided_at=_now(), action=TradeAction.LADDER_2
        )

        p1, p2 = repo.get("P-1"), repo.get("P-2")
        self.assertEqual(p1.approved_action, TradeAction.LADDER_1)
        self.assertEqual(p2.approved_action, TradeAction.LADDER_2)

        # P1's approval can never be used to submit LADDER_2, and vice versa.
        self.assertFalse(
            validate_for_submission(
                p1,
                trigger_price=p1.ladder_1_trigger,
                current_price=p1.ladder_1_trigger,
                active_floor_price=80.0,
                now=_now(),
                action=TradeAction.LADDER_2,
            ).allowed
        )
        self.assertTrue(
            validate_for_submission(
                p2,
                trigger_price=p2.ladder_2_trigger,
                current_price=p2.ladder_2_trigger,
                active_floor_price=80.0,
                now=_now(),
                action=TradeAction.LADDER_2,
            ).allowed
        )


class TestProposedActionConsistency(unittest.TestCase):
    """Correctness property added alongside the auto-expire rule: a
    proposal may only ever be decided for the action it was created
    for."""

    def test_deciding_for_a_different_action_than_proposed_raises(self):
        p = _make(action=TradeAction.LADDER_1)
        with self.assertRaises(ValueError):
            p.with_decision(
                approved=True, decided_by="controller", decided_at=_now(), action=TradeAction.LADDER_2
            )

    def test_expire_rejects_an_already_terminal_proposal(self):
        p = _make(action=TradeAction.LADDER_1)
        rejected = p.with_decision(
            approved=False, decided_by="controller", decided_at=_now(), action=TradeAction.LADDER_1
        )
        with self.assertRaises(ValueError):
            rejected.expire(expired_at=_now())

    def test_expire_returns_new_object_and_preserves_other_fields(self):
        p = _make(action=TradeAction.LADDER_1)
        expired = p.expire(expired_at=_now())
        self.assertEqual(expired.approval_state, ApprovalState.EXPIRED)
        self.assertEqual(expired.expired_at, _now())
        self.assertEqual(expired.proposal_id, p.proposal_id)
        self.assertEqual(p.approval_state, ApprovalState.PENDING)  # original untouched


class TestPlanSaveSupersessionIsPure(unittest.TestCase):
    """Direct unit tests of the extracted pure decision function --
    called with plain lists only, NO InMemoryProposalRepository or any
    other storage object involved, proving it is genuinely
    storage-agnostic and independently verifiable."""

    def test_no_siblings_returns_empty_tuple(self):
        new = _make(action=TradeAction.LADDER_1)
        self.assertEqual(plan_save_supersession([], new), ())

    def test_pending_sibling_is_returned_for_supersession(self):
        pending = _make(proposal_id="P-old", action=TradeAction.LADDER_1)
        new = _make(proposal_id="P-new", action=TradeAction.LADDER_1)
        self.assertEqual(plan_save_supersession([pending], new), ("P-old",))

    def test_rejected_and_expired_siblings_are_never_returned(self):
        rejected = _make(proposal_id="P-r", action=TradeAction.LADDER_1).with_decision(
            approved=False, decided_by="controller", decided_at=_now(), action=TradeAction.LADDER_1
        )
        expired = _make(proposal_id="P-e", action=TradeAction.LADDER_1).expire(expired_at=_now())
        new = _make(proposal_id="P-new", action=TradeAction.LADDER_1)
        self.assertEqual(plan_save_supersession([rejected, expired], new), ())

    def test_approved_and_still_valid_sibling_raises_and_returns_nothing(self):
        approved = _make(proposal_id="P-a", action=TradeAction.LADDER_1, price=100.0).with_decision(
            approved=True, decided_by="controller", decided_at=_now(), action=TradeAction.LADDER_1
        )
        new = _make(proposal_id="P-new", action=TradeAction.LADDER_1, price=95.0)
        with self.assertRaises(ProposalDecisionConflictError):
            plan_save_supersession([approved], new)

    def test_approved_but_d0007_invalid_sibling_is_never_returned_for_expiry(self):
        approved = _make(proposal_id="P-a", action=TradeAction.LADDER_1, price=100.0).with_decision(
            approved=True, decided_by="controller", decided_at=_now(), action=TradeAction.LADDER_1
        )
        # New proposal created 6 minutes later -- the sibling's approval
        # has aged out of its 5-minute D-0007 window.
        new = _make(proposal_id="P-new", action=TradeAction.LADDER_1, price=95.0)
        new = replace(new, proposal_created_at=_now() + timedelta(minutes=6))
        result = plan_save_supersession([approved], new)
        self.assertEqual(result, ())  # never auto-expired, never returned

    def test_rejects_invalid_current_price(self):
        new = _make(action=TradeAction.LADDER_1)
        new = replace(new, current_price_at_proposal=float("nan"))
        with self.assertRaises(ValueError):
            plan_save_supersession([], new)

    def test_is_a_pure_function_no_shared_mutable_state(self):
        pending = _make(proposal_id="P-old", action=TradeAction.LADDER_1)
        new = _make(proposal_id="P-new", action=TradeAction.LADDER_1)
        siblings = [pending]
        result1 = plan_save_supersession(siblings, new)
        result2 = plan_save_supersession(siblings, new)
        self.assertEqual(result1, result2)
        # The input sibling objects are never mutated by the call.
        self.assertEqual(siblings[0].approval_state, ApprovalState.PENDING)


class TestPlanDecisionIsPure(unittest.TestCase):
    """Direct unit tests of the extracted pure record_decision
    precondition -- no repository object involved."""

    def test_none_existing_raises(self):
        with self.assertRaises(ProposalDecisionConflictError):
            plan_decision(None, "P-missing")

    def test_pending_proposal_is_returned_unchanged(self):
        p = _make(action=TradeAction.LADDER_1)
        self.assertIs(plan_decision(p, p.proposal_id), p)

    def test_already_approved_proposal_raises(self):
        approved = _make(action=TradeAction.LADDER_1).with_decision(
            approved=True, decided_by="controller", decided_at=_now(), action=TradeAction.LADDER_1
        )
        with self.assertRaises(ProposalDecisionConflictError):
            plan_decision(approved, approved.proposal_id)

    def test_already_rejected_proposal_raises(self):
        rejected = _make(action=TradeAction.LADDER_1).with_decision(
            approved=False, decided_by="controller", decided_at=_now(), action=TradeAction.LADDER_1
        )
        with self.assertRaises(ProposalDecisionConflictError):
            plan_decision(rejected, rejected.proposal_id)


if __name__ == "__main__":
    unittest.main()
