import unittest
from datetime import datetime, timedelta, timezone

from orchestration.trade_proposal_service import (
    LadderAlreadyFilledError,
    TradeNotActiveError,
    TradeNotFoundError,
    TradeProposalService,
)
from persistence.db import bootstrap_schema, connect
from proposals.models import ApprovalState, FloorContext, TradeAction, approved_strategy_rule_set
from proposals.proposal import build_trade_proposal
from proposals.repository import ProposalDecisionConflictError
from proposals.sqlite_repository import SqliteProposalRepository
from trade.models import InitialOrderStatus, Trade
from trade.sqlite_repository import SqliteTradeRepository


def _now():
    return datetime(2026, 9, 17, 14, 0, 0, tzinfo=timezone.utc)


def _strategy():
    return approved_strategy_rule_set()


def _service():
    conn = connect(":memory:")
    bootstrap_schema(conn)
    trade_repo = SqliteTradeRepository(conn)
    proposal_repo = SqliteProposalRepository(conn)
    return TradeProposalService(trade_repo, proposal_repo), trade_repo, proposal_repo


def _active_trade(trade_repo, *, trade_id="T-1", symbol="TSLA", fill_price=100.0, shares=10):
    record = trade_repo.save(Trade(trade_id=trade_id, symbol=symbol, created_at=_now()), now=_now())
    frozen = record.trade.freeze_initial_reference(
        order_status=InitialOrderStatus.FILLED,
        filled_shares=shares,
        fill_price=fill_price,
        strategy=_strategy(),
        now=_now(),
    )
    return trade_repo.update(frozen, expected_revision=record.revision, transition="frozen", now=_now())


class TestStartTradeSuccess(unittest.TestCase):
    def test_persists_trade_then_initial_proposal(self):
        service, trade_repo, proposal_repo = _service()

        trade_record, proposal = service.start_trade(
            trade_id="T-1",
            symbol="TSLA",
            proposal_id="P-1",
            current_price=100.0,
            strategy=_strategy(),
            floor_context=FloorContext.no_existing_position(),
            now=_now(),
        )

        self.assertEqual(trade_record.trade.trade_id, "T-1")
        self.assertEqual(trade_record.revision, 0)
        self.assertEqual(proposal.proposed_action, TradeAction.INITIAL_ENTRY)
        self.assertEqual(proposal.approval_state, ApprovalState.PENDING)

        stored_trade = trade_repo.get("T-1")
        self.assertIsNotNone(stored_trade)
        stored_proposal = proposal_repo.get("P-1")
        self.assertIsNotNone(stored_proposal)
        self.assertEqual(stored_proposal.trade_id, "T-1")


class TestTradeFirstOrdering(unittest.TestCase):
    def test_trade_persisted_before_proposal(self):
        # A duplicate proposal_id forces the proposal-save step to fail
        # AFTER the Trade has already been persisted -- proves ordering
        # by observing the Trade exists despite the overall call raising.
        service, trade_repo, proposal_repo = _service()

        # Pre-seed a Trade + proposal with the SAME proposal_id under a
        # different trade_id so start_trade's own proposal.save() hits a
        # real proposal_id collision.
        trade_repo.save(Trade(trade_id="OTHER", symbol="TSLA", created_at=_now()), now=_now())
        proposal_repo.save(
            build_trade_proposal(
                proposal_id="P-DUP",
                trade_id="OTHER",
                action=TradeAction.INITIAL_ENTRY,
                symbol="TSLA",
                current_price=50.0,
                as_of=_now(),
                strategy=_strategy(),
                floor_context=FloorContext.no_existing_position(),
            )
        )

        with self.assertRaises(ProposalDecisionConflictError):
            service.start_trade(
                trade_id="T-1",
                symbol="TSLA",
                proposal_id="P-DUP",
                current_price=100.0,
                strategy=_strategy(),
                floor_context=FloorContext.no_existing_position(),
                now=_now(),
            )

        self.assertIsNotNone(trade_repo.get("T-1"))
        self.assertEqual(proposal_repo.list_for_trade("T-1"), [])


class TestProposeNextActionPreconditions(unittest.TestCase):
    def test_missing_trade_raises(self):
        service, _, _ = _service()
        with self.assertRaises(TradeNotFoundError):
            service.propose_next_action(
                trade_id="NOPE",
                action=TradeAction.LADDER_1,
                proposal_id="P-1",
                current_price=95.0,
                strategy=_strategy(),
                floor_context=FloorContext.known(90.0),
                now=_now(),
            )

    def test_awaiting_initial_fill_blocks(self):
        service, trade_repo, _ = _service()
        trade_repo.save(Trade(trade_id="T-1", symbol="TSLA", created_at=_now()), now=_now())

        with self.assertRaises(TradeNotActiveError):
            service.propose_next_action(
                trade_id="T-1",
                action=TradeAction.LADDER_1,
                proposal_id="P-1",
                current_price=95.0,
                strategy=_strategy(),
                floor_context=FloorContext.known(90.0),
                now=_now(),
            )

    def test_abandoned_trade_blocks(self):
        service, trade_repo, _ = _service()
        record = trade_repo.save(Trade(trade_id="T-1", symbol="TSLA", created_at=_now()), now=_now())
        abandoned = record.trade.freeze_initial_reference(
            order_status=InitialOrderStatus.CANCELLED,
            filled_shares=0,
            fill_price=None,
            strategy=_strategy(),
            now=_now(),
        )
        trade_repo.update(abandoned, expected_revision=record.revision, transition="abandoned", now=_now())

        with self.assertRaises(TradeNotActiveError):
            service.propose_next_action(
                trade_id="T-1",
                action=TradeAction.LADDER_1,
                proposal_id="P-1",
                current_price=95.0,
                strategy=_strategy(),
                floor_context=FloorContext.known(90.0),
                now=_now(),
            )

    def test_closed_trade_blocks(self):
        service, trade_repo, _ = _service()
        record = _active_trade(trade_repo)
        closed = record.trade.reconcile_position(
            total_shares=0, weighted_avg_entry_price=None, strategy=_strategy(), now=_now()
        )
        trade_repo.update(closed, expected_revision=record.revision, transition="closed", now=_now())

        with self.assertRaises(TradeNotActiveError):
            service.propose_next_action(
                trade_id="T-1",
                action=TradeAction.LADDER_1,
                proposal_id="P-1",
                current_price=95.0,
                strategy=_strategy(),
                floor_context=FloorContext.known(90.0),
                now=_now(),
            )

    def test_ladder1_already_filled_blocks_ladder1(self):
        service, trade_repo, _ = _service()
        record = _active_trade(trade_repo)
        filled = record.trade.record_ladder_fill(
            TradeAction.LADDER_1,
            order_id="O-1",
            fill_price=95.0,
            fill_qty=10,
            new_total_shares=20,
            new_weighted_avg_entry_price=97.5,
            strategy=_strategy(),
        )
        trade_repo.update(filled, expected_revision=record.revision, transition="ladder1_filled", now=_now())

        with self.assertRaises(LadderAlreadyFilledError):
            service.propose_next_action(
                trade_id="T-1",
                action=TradeAction.LADDER_1,
                proposal_id="P-1",
                current_price=95.0,
                strategy=_strategy(),
                floor_context=FloorContext.known(90.0),
                now=_now(),
            )

    def test_ladder2_already_filled_blocks_ladder2(self):
        service, trade_repo, _ = _service()
        record = _active_trade(trade_repo)
        filled = record.trade.record_ladder_fill(
            TradeAction.LADDER_2,
            order_id="O-2",
            fill_price=92.0,
            fill_qty=20,
            new_total_shares=30,
            new_weighted_avg_entry_price=96.0,
            strategy=_strategy(),
        )
        trade_repo.update(filled, expected_revision=record.revision, transition="ladder2_filled", now=_now())

        with self.assertRaises(LadderAlreadyFilledError):
            service.propose_next_action(
                trade_id="T-1",
                action=TradeAction.LADDER_2,
                proposal_id="P-1",
                current_price=92.0,
                strategy=_strategy(),
                floor_context=FloorContext.known(90.0),
                now=_now(),
            )

    def test_rejects_initial_entry_action(self):
        service, trade_repo, _ = _service()
        _active_trade(trade_repo)
        with self.assertRaises(ValueError):
            service.propose_next_action(
                trade_id="T-1",
                action=TradeAction.INITIAL_ENTRY,
                proposal_id="P-1",
                current_price=95.0,
                strategy=_strategy(),
                floor_context=FloorContext.known(90.0),
                now=_now(),
            )


class TestProposeNextActionSuccessAndOptionEReuse(unittest.TestCase):
    def test_valid_proposal_persisted(self):
        service, trade_repo, proposal_repo = _service()
        _active_trade(trade_repo)

        proposal = service.propose_next_action(
            trade_id="T-1",
            action=TradeAction.LADDER_1,
            proposal_id="P-L1",
            current_price=95.0,
            strategy=_strategy(),
            floor_context=FloorContext.known(90.0),
            now=_now(),
        )

        self.assertEqual(proposal.proposed_action, TradeAction.LADDER_1)
        self.assertIsNotNone(proposal_repo.get("P-L1"))

    def test_rejected_proposal_can_be_reproposed(self):
        service, trade_repo, proposal_repo = _service()
        _active_trade(trade_repo)

        service.propose_next_action(
            trade_id="T-1",
            action=TradeAction.LADDER_1,
            proposal_id="P-L1-a",
            current_price=95.0,
            strategy=_strategy(),
            floor_context=FloorContext.known(90.0),
            now=_now(),
        )
        proposal_repo.record_decision(
            "P-L1-a",
            approved=False,
            decided_by="controller",
            decided_at=_now(),
            action=TradeAction.LADDER_1,
        )

        second = service.propose_next_action(
            trade_id="T-1",
            action=TradeAction.LADDER_1,
            proposal_id="P-L1-b",
            current_price=95.0,
            strategy=_strategy(),
            floor_context=FloorContext.known(90.0),
            now=_now() + timedelta(minutes=1),
        )

        self.assertEqual(second.proposal_id, "P-L1-b")
        history = proposal_repo.list_for_trade("T-1")
        self.assertEqual({p.proposal_id for p in history}, {"P-L1-a", "P-L1-b"})
        self.assertEqual(proposal_repo.get("P-L1-a").approval_state, ApprovalState.REJECTED)

    def test_valid_approved_sibling_blocks_new_proposal_via_existing_option_e(self):
        service, trade_repo, proposal_repo = _service()
        _active_trade(trade_repo)

        service.propose_next_action(
            trade_id="T-1",
            action=TradeAction.LADDER_1,
            proposal_id="P-L1-a",
            current_price=95.0,
            strategy=_strategy(),
            floor_context=FloorContext.known(90.0),
            now=_now(),
        )
        proposal_repo.record_decision(
            "P-L1-a",
            approved=True,
            decided_by="controller",
            decided_at=_now(),
            action=TradeAction.LADDER_1,
        )

        # P-L1-a's ladder_1_trigger = 95.0 * (1 - 0.05) = 90.25 (its own
        # current_price_at_proposal is the reference build_trade_proposal
        # uses) -- D-0007's price band is checked against THAT trigger,
        # not against 95.0, so the new proposal's current_price must sit
        # within 0.5% of 90.25 for the sibling to still read as valid.
        with self.assertRaises(ProposalDecisionConflictError):
            service.propose_next_action(
                trade_id="T-1",
                action=TradeAction.LADDER_1,
                proposal_id="P-L1-b",
                current_price=90.3,
                strategy=_strategy(),
                floor_context=FloorContext.known(90.0),
                now=_now() + timedelta(seconds=30),
            )

    def test_stale_approved_sibling_left_untouched_new_proposal_allowed(self):
        service, trade_repo, proposal_repo = _service()
        _active_trade(trade_repo)

        service.propose_next_action(
            trade_id="T-1",
            action=TradeAction.LADDER_1,
            proposal_id="P-L1-a",
            current_price=95.0,
            strategy=_strategy(),
            floor_context=FloorContext.known(90.0),
            now=_now(),
        )
        proposal_repo.record_decision(
            "P-L1-a",
            approved=True,
            decided_by="controller",
            decided_at=_now(),
            action=TradeAction.LADDER_1,
        )

        later = _now() + timedelta(minutes=6)
        second = service.propose_next_action(
            trade_id="T-1",
            action=TradeAction.LADDER_1,
            proposal_id="P-L1-b",
            current_price=95.0,
            strategy=_strategy(),
            floor_context=FloorContext.known(90.0),
            now=later,
        )

        self.assertEqual(second.proposal_id, "P-L1-b")
        self.assertEqual(proposal_repo.get("P-L1-a").approval_state, ApprovalState.APPROVED)

    def test_pending_sibling_is_superseded(self):
        service, trade_repo, proposal_repo = _service()
        _active_trade(trade_repo)

        service.propose_next_action(
            trade_id="T-1",
            action=TradeAction.LADDER_1,
            proposal_id="P-L1-a",
            current_price=95.0,
            strategy=_strategy(),
            floor_context=FloorContext.known(90.0),
            now=_now(),
        )

        service.propose_next_action(
            trade_id="T-1",
            action=TradeAction.LADDER_1,
            proposal_id="P-L1-b",
            current_price=95.0,
            strategy=_strategy(),
            floor_context=FloorContext.known(90.0),
            now=_now() + timedelta(seconds=1),
        )

        self.assertEqual(proposal_repo.get("P-L1-a").approval_state, ApprovalState.EXPIRED)
        self.assertEqual(proposal_repo.get("P-L1-b").approval_state, ApprovalState.PENDING)


class TestNoTradeMutationInProposeNextAction(unittest.TestCase):
    def test_no_trade_repository_update_call(self):
        service, trade_repo, _ = _service()
        _active_trade(trade_repo)

        original_update = trade_repo.update

        def _fail_if_called(*args, **kwargs):
            raise AssertionError("propose_next_action must never call TradeRepository.update()")

        trade_repo.update = _fail_if_called
        try:
            service.propose_next_action(
                trade_id="T-1",
                action=TradeAction.LADDER_1,
                proposal_id="P-L1",
                current_price=95.0,
                strategy=_strategy(),
                floor_context=FloorContext.known(90.0),
                now=_now(),
            )
        finally:
            trade_repo.update = original_update

    def test_trade_revision_unchanged(self):
        service, trade_repo, _ = _service()
        before = _active_trade(trade_repo)

        service.propose_next_action(
            trade_id="T-1",
            action=TradeAction.LADDER_1,
            proposal_id="P-L1",
            current_price=95.0,
            strategy=_strategy(),
            floor_context=FloorContext.known(90.0),
            now=_now(),
        )

        after = trade_repo.get("T-1")
        self.assertEqual(after.revision, before.revision)

    def test_no_pointer_fields_written(self):
        service, trade_repo, _ = _service()
        _active_trade(trade_repo)

        service.propose_next_action(
            trade_id="T-1",
            action=TradeAction.LADDER_1,
            proposal_id="P-L1",
            current_price=95.0,
            strategy=_strategy(),
            floor_context=FloorContext.known(90.0),
            now=_now(),
        )
        service.propose_next_action(
            trade_id="T-1",
            action=TradeAction.LADDER_2,
            proposal_id="P-L2",
            current_price=90.0,
            strategy=_strategy(),
            floor_context=FloorContext.known(90.0),
            now=_now(),
        )

        stored = trade_repo.get("T-1").trade
        self.assertIsNone(stored.ladder1_proposal_id)
        self.assertIsNone(stored.ladder2_proposal_id)


if __name__ == "__main__":
    unittest.main()
