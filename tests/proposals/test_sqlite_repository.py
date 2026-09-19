import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from persistence.db import bootstrap_schema, connect
from proposals.models import ApprovalState, FloorContext, TradeAction, approved_strategy_rule_set
from proposals.proposal import build_trade_proposal
from proposals.repository import ProposalDecisionConflictError
from proposals.revalidation import validate_for_submission
from proposals.sqlite_repository import SqliteProposalRepository, TradeDoesNotExistError
from trade.models import Trade
from trade.sqlite_repository import SqliteTradeRepository


def _now():
    return datetime(2026, 9, 17, 14, 0, 0, tzinfo=timezone.utc)


def _strategy():
    return approved_strategy_rule_set()


def _make_proposal(
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
        strategy=_strategy(),
        floor_context=floor_context if floor_context is not None else FloorContext.known(80.0),
    )


def _repo():
    conn = connect(":memory:")
    bootstrap_schema(conn)
    proposal_repo = SqliteProposalRepository(conn)
    trade_repo = SqliteTradeRepository(conn)
    return proposal_repo, trade_repo, conn


def _save_trade(trade_repo, trade_id="T-1", symbol="TSLA"):
    trade_repo.save(Trade(trade_id=trade_id, symbol=symbol, created_at=_now()), now=_now())


class TestSaveGetRoundTrip(unittest.TestCase):
    def test_round_trip(self):
        proposal_repo, trade_repo, _ = _repo()
        _save_trade(trade_repo)
        p = _make_proposal()
        proposal_repo.save(p)
        self.assertEqual(proposal_repo.get("P-1"), p)

    def test_get_missing_returns_none(self):
        proposal_repo, _, _ = _repo()
        self.assertIsNone(proposal_repo.get("does-not-exist"))

    def test_initial_proposal_persistence_starts_pending(self):
        proposal_repo, trade_repo, _ = _repo()
        _save_trade(trade_repo)
        p = _make_proposal()
        proposal_repo.save(p)
        fetched = proposal_repo.get("P-1")
        self.assertEqual(fetched.approval_state, ApprovalState.PENDING)
        self.assertIsNone(fetched.approval_received_at)


class TestTradeFirstForeignKey(unittest.TestCase):
    def test_save_for_nonexistent_trade_fails_clearly(self):
        proposal_repo, _, _ = _repo()
        p = _make_proposal(trade_id="ghost-trade")
        with self.assertRaises(TradeDoesNotExistError):
            proposal_repo.save(p)

    def test_failed_save_persists_nothing(self):
        proposal_repo, _, conn = _repo()
        p = _make_proposal(trade_id="ghost-trade")
        with self.assertRaises(TradeDoesNotExistError):
            proposal_repo.save(p)
        count = conn.execute("SELECT COUNT(*) FROM proposals").fetchone()[0]
        self.assertEqual(count, 0)

    def test_repository_never_creates_a_trade(self):
        proposal_repo, _, conn = _repo()
        p = _make_proposal(trade_id="ghost-trade")
        with self.assertRaises(TradeDoesNotExistError):
            proposal_repo.save(p)
        count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        self.assertEqual(count, 0)


class TestListForTradeAndSymbol(unittest.TestCase):
    def test_list_for_trade_returns_all_attempts_in_order(self):
        proposal_repo, trade_repo, _ = _repo()
        _save_trade(trade_repo)
        proposal_repo.save(_make_proposal(proposal_id="P-1", action=TradeAction.INITIAL_ENTRY))
        proposal_repo.save(_make_proposal(proposal_id="P-2", action=TradeAction.LADDER_1))
        proposal_repo.save(_make_proposal(proposal_id="P-3", action=TradeAction.LADDER_2))

        attempts = proposal_repo.list_for_trade("T-1")
        self.assertEqual([p.proposal_id for p in attempts], ["P-1", "P-2", "P-3"])

    def test_list_for_trade_excludes_other_trades(self):
        proposal_repo, trade_repo, _ = _repo()
        _save_trade(trade_repo, trade_id="T-A")
        _save_trade(trade_repo, trade_id="T-B")
        proposal_repo.save(_make_proposal(proposal_id="P-1", trade_id="T-A"))
        proposal_repo.save(_make_proposal(proposal_id="P-2", trade_id="T-B"))
        self.assertEqual([p.proposal_id for p in proposal_repo.list_for_trade("T-A")], ["P-1"])

    def test_list_for_symbol(self):
        proposal_repo, trade_repo, _ = _repo()
        _save_trade(trade_repo, trade_id="T-1", symbol="TSLA")
        _save_trade(trade_repo, trade_id="T-2", symbol="TSLA")
        _save_trade(trade_repo, trade_id="T-3", symbol="DELL")
        proposal_repo.save(_make_proposal(proposal_id="P-1", trade_id="T-1", symbol="TSLA"))
        proposal_repo.save(_make_proposal(proposal_id="P-2", trade_id="T-2", symbol="TSLA"))
        proposal_repo.save(_make_proposal(proposal_id="P-3", trade_id="T-3", symbol="DELL"))

        tsla = {p.proposal_id for p in proposal_repo.list_for_symbol("tsla")}
        self.assertEqual(tsla, {"P-1", "P-2"})


class TestOptionESupersession(unittest.TestCase):
    def test_pending_sibling_is_superseded(self):
        proposal_repo, trade_repo, _ = _repo()
        _save_trade(trade_repo)
        proposal_repo.save(_make_proposal(proposal_id="P-1", action=TradeAction.LADDER_1))
        proposal_repo.save(_make_proposal(proposal_id="P-2", action=TradeAction.LADDER_1))

        self.assertEqual(proposal_repo.get("P-1").approval_state, ApprovalState.EXPIRED)
        self.assertEqual(proposal_repo.get("P-2").approval_state, ApprovalState.PENDING)

    def test_approved_and_still_d0007_valid_sibling_blocks_new_save(self):
        proposal_repo, trade_repo, _ = _repo()
        _save_trade(trade_repo)
        proposal_repo.save(_make_proposal(proposal_id="P-1", action=TradeAction.LADDER_1, price=100.0))
        proposal_repo.record_decision(
            "P-1", approved=True, decided_by="controller", decided_at=_now(), action=TradeAction.LADDER_1
        )

        with self.assertRaises(ProposalDecisionConflictError):
            proposal_repo.save(_make_proposal(proposal_id="P-2", action=TradeAction.LADDER_1, price=95.0))

        # The approved sibling is completely untouched.
        self.assertEqual(proposal_repo.get("P-1").approval_state, ApprovalState.APPROVED)
        self.assertIsNone(proposal_repo.get("P-2"))

    def test_approved_but_d0007_invalid_sibling_is_never_touched_and_new_save_allowed(self):
        proposal_repo, trade_repo, _ = _repo()
        _save_trade(trade_repo)
        proposal_repo.save(_make_proposal(proposal_id="P-1", action=TradeAction.LADDER_1, price=100.0))
        proposal_repo.record_decision(
            "P-1", approved=True, decided_by="controller", decided_at=_now(), action=TradeAction.LADDER_1
        )

        # A new attempt created 6 minutes later -- P-1's approval has aged
        # out of its 5-minute D-0007 window.
        stale_now = _now() + timedelta(minutes=6)
        p2 = build_trade_proposal(
            proposal_id="P-2",
            trade_id="T-1",
            action=TradeAction.LADDER_1,
            symbol="TSLA",
            current_price=95.0,
            as_of=stale_now,
            strategy=_strategy(),
            floor_context=FloorContext.known(80.0),
        )
        proposal_repo.save(p2)  # must not raise

        self.assertEqual(proposal_repo.get("P-1").approval_state, ApprovalState.APPROVED)  # never auto-expired
        self.assertEqual(proposal_repo.get("P-2").approval_state, ApprovalState.PENDING)

    def test_rejected_sibling_never_blocks_and_is_never_touched(self):
        proposal_repo, trade_repo, _ = _repo()
        _save_trade(trade_repo)
        proposal_repo.save(_make_proposal(proposal_id="P-1", action=TradeAction.LADDER_1))
        proposal_repo.record_decision(
            "P-1", approved=False, decided_by="controller", decided_at=_now(), action=TradeAction.LADDER_1
        )
        proposal_repo.save(_make_proposal(proposal_id="P-2", action=TradeAction.LADDER_1))

        self.assertEqual(proposal_repo.get("P-1").approval_state, ApprovalState.REJECTED)
        self.assertEqual(proposal_repo.get("P-2").approval_state, ApprovalState.PENDING)


class TestRecordDecision(unittest.TestCase):
    def test_approve(self):
        proposal_repo, trade_repo, _ = _repo()
        _save_trade(trade_repo)
        proposal_repo.save(_make_proposal())
        decided_at = _now() + timedelta(minutes=1)
        result = proposal_repo.record_decision(
            "P-1", approved=True, decided_by="controller", decided_at=decided_at, action=TradeAction.INITIAL_ENTRY
        )
        self.assertEqual(result.approval_state, ApprovalState.APPROVED)
        self.assertEqual(result.approval_expires_at, decided_at + timedelta(minutes=5))
        self.assertEqual(proposal_repo.get("P-1").approval_state, ApprovalState.APPROVED)

    def test_reject(self):
        proposal_repo, trade_repo, _ = _repo()
        _save_trade(trade_repo)
        proposal_repo.save(_make_proposal())
        result = proposal_repo.record_decision(
            "P-1", approved=False, decided_by="controller", decided_at=_now(), action=TradeAction.INITIAL_ENTRY
        )
        self.assertEqual(result.approval_state, ApprovalState.REJECTED)
        self.assertIsNone(result.approval_expires_at)

    def test_decision_on_unknown_proposal_raises(self):
        proposal_repo, _, _ = _repo()
        with self.assertRaises(ProposalDecisionConflictError):
            proposal_repo.record_decision(
                "does-not-exist",
                approved=True,
                decided_by="controller",
                decided_at=_now(),
                action=TradeAction.INITIAL_ENTRY,
            )

    def test_second_decision_on_same_proposal_raises(self):
        proposal_repo, trade_repo, _ = _repo()
        _save_trade(trade_repo)
        proposal_repo.save(_make_proposal())
        proposal_repo.record_decision(
            "P-1", approved=True, decided_by="controller", decided_at=_now(), action=TradeAction.INITIAL_ENTRY
        )
        with self.assertRaises(ProposalDecisionConflictError):
            proposal_repo.record_decision(
                "P-1", approved=False, decided_by="controller", decided_at=_now(), action=TradeAction.INITIAL_ENTRY
            )

    def test_mismatched_action_is_rejected(self):
        proposal_repo, trade_repo, _ = _repo()
        _save_trade(trade_repo)
        proposal_repo.save(_make_proposal(action=TradeAction.LADDER_1))
        with self.assertRaises(ValueError):
            proposal_repo.record_decision(
                "P-1", approved=True, decided_by="controller", decided_at=_now(), action=TradeAction.LADDER_2
            )
        # Nothing was persisted -- proposal remains PENDING.
        self.assertEqual(proposal_repo.get("P-1").approval_state, ApprovalState.PENDING)


class TestActionScopedApprovalAndD0007(unittest.TestCase):
    def test_ladder_1_approval_does_not_authorize_ladder_2(self):
        proposal_repo, trade_repo, _ = _repo()
        _save_trade(trade_repo)
        proposal_repo.save(_make_proposal(action=TradeAction.LADDER_1, price=100.0))
        proposal_repo.record_decision(
            "P-1", approved=True, decided_by="controller", decided_at=_now(), action=TradeAction.LADDER_1
        )
        p = proposal_repo.get("P-1")
        result = validate_for_submission(
            p,
            trigger_price=p.ladder_1_trigger,
            current_price=p.ladder_1_trigger,
            active_floor_price=80.0,
            now=_now() + timedelta(minutes=1),
            action=TradeAction.LADDER_2,
        )
        self.assertFalse(result.allowed)

    def test_d0007_boundary_via_persisted_proposal(self):
        proposal_repo, trade_repo, _ = _repo()
        _save_trade(trade_repo)
        proposal_repo.save(_make_proposal(action=TradeAction.LADDER_1, price=100.0))
        proposal_repo.record_decision(
            "P-1", approved=True, decided_by="controller", decided_at=_now(), action=TradeAction.LADDER_1
        )
        p = proposal_repo.get("P-1")
        # Just over 5 minutes -- must fail.
        result = validate_for_submission(
            p,
            trigger_price=p.ladder_1_trigger,
            current_price=p.ladder_1_trigger,
            active_floor_price=80.0,
            now=_now() + timedelta(minutes=5, seconds=1),
            action=TradeAction.LADDER_1,
        )
        self.assertFalse(result.allowed)


class TestProposalHistoryPreserved(unittest.TestCase):
    def test_superseded_and_rejected_records_remain_queryable(self):
        proposal_repo, trade_repo, _ = _repo()
        _save_trade(trade_repo)
        proposal_repo.save(_make_proposal(proposal_id="P-1", action=TradeAction.LADDER_1))
        proposal_repo.record_decision(
            "P-1", approved=False, decided_by="controller", decided_at=_now(), action=TradeAction.LADDER_1
        )
        proposal_repo.save(_make_proposal(proposal_id="P-2", action=TradeAction.LADDER_1))
        proposal_repo.save(_make_proposal(proposal_id="P-3", action=TradeAction.LADDER_1))

        attempts = proposal_repo.list_for_trade("T-1")
        self.assertEqual([p.proposal_id for p in attempts], ["P-1", "P-2", "P-3"])
        self.assertEqual(attempts[0].approval_state, ApprovalState.REJECTED)
        self.assertEqual(attempts[1].approval_state, ApprovalState.EXPIRED)
        self.assertEqual(attempts[2].approval_state, ApprovalState.PENDING)


class TestReopenAndReload(unittest.TestCase):
    def test_close_reopen_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.sqlite")
            conn1 = connect(db_path)
            bootstrap_schema(conn1)
            SqliteTradeRepository(conn1).save(
                Trade(trade_id="T-1", symbol="TSLA", created_at=_now()), now=_now()
            )
            proposal_repo1 = SqliteProposalRepository(conn1)
            p = _make_proposal()
            proposal_repo1.save(p)
            proposal_repo1.record_decision(
                "P-1", approved=True, decided_by="controller", decided_at=_now(), action=TradeAction.INITIAL_ENTRY
            )
            conn1.close()

            conn2 = connect(db_path)
            bootstrap_schema(conn2)  # no-op, already bootstrapped
            proposal_repo2 = SqliteProposalRepository(conn2)
            fetched = proposal_repo2.get("P-1")
            self.assertEqual(fetched.approval_state, ApprovalState.APPROVED)
            self.assertEqual(fetched.trade_id, "T-1")
            conn2.close()


class TestConcurrency(unittest.TestCase):
    def test_begin_immediate_prevents_double_pending_race(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.sqlite")
            conn_a = connect(db_path)
            bootstrap_schema(conn_a)
            SqliteTradeRepository(conn_a).save(
                Trade(trade_id="T-1", symbol="TSLA", created_at=_now()), now=_now()
            )
            repo_a = SqliteProposalRepository(conn_a)

            conn_b = connect(db_path)
            conn_b.execute("PRAGMA busy_timeout = 0")
            repo_b = SqliteProposalRepository(conn_b)

            # A holds an open write transaction (simulated via a direct
            # BEGIN IMMEDIATE on its connection) while B tries to save --
            # B's own transaction() call must be refused/blocked rather
            # than racing A's read-then-write sequence.
            conn_a.execute("BEGIN IMMEDIATE")
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    repo_b.save(_make_proposal(proposal_id="P-1"))
            finally:
                conn_a.execute("ROLLBACK")
                conn_a.close()
                conn_b.close()


class TestRollback(unittest.TestCase):
    def test_forced_failure_mid_save_leaves_no_partial_write(self):
        proposal_repo, trade_repo, conn = _repo()
        _save_trade(trade_repo)
        proposal_repo.save(_make_proposal(proposal_id="P-1", action=TradeAction.LADDER_1))

        # Pre-insert a colliding row so the real INSERT inside a second
        # save() genuinely fails partway through the transaction (after
        # the supersession UPDATE for P-1 would already have run).
        # proposed_action='ladder_2' (NOT 'ladder_1') so this pre-inserted
        # row is never fetched as a sibling of the new ladder_1 proposal
        # below (it would otherwise be reconstructed via
        # _row_to_proposal() and rejected by TradeProposal's own
        # invariant that a REJECTED row must carry approval_received_at,
        # which this minimal row deliberately omits) -- the only
        # collision this row is meant to cause is the proposal_id
        # primary-key collision, later, when save() itself tries to
        # INSERT proposal_id 'P-2'.
        conn.execute(
            "INSERT INTO proposals (proposal_id, trade_id, proposed_action, symbol, candidate_source, "
            "current_price_at_proposal, proposed_entry, ladder_1_trigger, ladder_1_quantity, "
            "ladder_2_trigger, ladder_2_quantity, floor_trigger, maximum_position, proposal_created_at, "
            "assumptions_json, risks_json, approval_state) VALUES "
            "('P-2', 'T-1', 'ladder_2', 'TSLA', 'fixed_watchlist', 95.0, 95.0, 90.25, 10, 87.4, 20, "
            "85.5, 40, '2026-09-17T14:00:00+00:00', '[]', '[]', 'rejected')"
        )

        # The real INSERT's PRIMARY KEY constraint fails on the
        # pre-inserted 'P-2' row; save() translates that genuine
        # sqlite3.IntegrityError into ProposalDecisionConflictError.
        with self.assertRaises(ProposalDecisionConflictError):
            proposal_repo.save(_make_proposal(proposal_id="P-2", action=TradeAction.LADDER_1))

        # P-1's supersession attempt (already executed earlier in this
        # same transaction) must have been rolled back -- it must still
        # be PENDING, not EXPIRED.
        self.assertEqual(proposal_repo.get("P-1").approval_state, ApprovalState.PENDING)


class TestCorruptedRowRejection(unittest.TestCase):
    def test_get_raises_on_invalid_enum_value(self):
        # The DB-level CHECK constraint already rejects a genuinely
        # unknown approval_state value, so that corruption can never
        # reach _row_to_proposal() -- confirm that defense-in-depth
        # directly instead.
        proposal_repo, trade_repo, conn = _repo()
        _save_trade(trade_repo)
        proposal_repo.save(_make_proposal())
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE proposals SET approval_state = 'not-a-real-state' WHERE proposal_id = 'P-1'")

    def test_get_raises_on_invariant_violation(self):
        proposal_repo, trade_repo, conn = _repo()
        _save_trade(trade_repo)
        proposal_repo.save(_make_proposal(action=TradeAction.LADDER_1))
        proposal_repo.record_decision(
            "P-1", approved=True, decided_by="controller", decided_at=_now(), action=TradeAction.LADDER_1
        )
        # Corrupt: APPROVED but strip approval_received_at.
        conn.execute("UPDATE proposals SET approval_received_at = NULL WHERE proposal_id = 'P-1'")
        with self.assertRaises(ValueError):
            proposal_repo.get("P-1")


if __name__ == "__main__":
    unittest.main()
