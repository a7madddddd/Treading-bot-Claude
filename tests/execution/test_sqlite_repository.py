import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from execution.models import OrderExecution, OrderExecutionError
from execution.repository import (
    OrderExecutionAlreadyExistsError,
    OrderExecutionRevisionConflictError,
    ProposalDoesNotExistError,
    TradeDoesNotExistError,
)
from execution.sqlite_repository import SqliteOrderExecutionRepository
from persistence.db import bootstrap_schema, connect
from proposals.models import FloorContext, TradeAction, approved_strategy_rule_set
from proposals.proposal import build_trade_proposal
from proposals.sqlite_repository import SqliteProposalRepository
from trade.models import Trade
from trade.sqlite_repository import SqliteTradeRepository


def _now():
    return datetime(2026, 9, 17, 14, 0, 0, tzinfo=timezone.utc)


def _strategy():
    return approved_strategy_rule_set()


def _execution(**overrides):
    kwargs = dict(
        proposal_id="P-1",
        execution_id="E-1",
        trade_id="T-1",
        client_order_id="C-1",
        requested_qty=10,
        created_at=_now(),
    )
    kwargs.update(overrides)
    return OrderExecution(**kwargs)


def _save_trade_only(conn, *, trade_id="T-1", symbol="TSLA"):
    SqliteTradeRepository(conn).save(Trade(trade_id=trade_id, symbol=symbol, created_at=_now()), now=_now())


def _repo():
    conn = connect(":memory:")
    bootstrap_schema(conn)
    return SqliteOrderExecutionRepository(conn), conn


def _save_trade_and_proposal(conn, *, trade_id="T-1", proposal_id="P-1", symbol="TSLA"):
    SqliteTradeRepository(conn).save(Trade(trade_id=trade_id, symbol=symbol, created_at=_now()), now=_now())
    proposal = build_trade_proposal(
        proposal_id=proposal_id,
        trade_id=trade_id,
        action=TradeAction.INITIAL_ENTRY,
        symbol=symbol,
        current_price=100.0,
        as_of=_now(),
        strategy=_strategy(),
        floor_context=FloorContext.no_existing_position(),
    )
    SqliteProposalRepository(conn).save(proposal)


class TestSaveGetRoundTrip(unittest.TestCase):
    def test_round_trip(self):
        repo, conn = _repo()
        _save_trade_and_proposal(conn)
        execution = _execution()
        record = repo.save(execution, now=_now())
        self.assertEqual(record.revision, 0)
        fetched = repo.get("E-1")
        self.assertEqual(fetched.execution, execution)
        self.assertEqual(fetched.revision, 0)

    def test_get_missing_returns_none(self):
        repo, _ = _repo()
        self.assertIsNone(repo.get("NOPE"))

    def test_revision_starts_at_zero(self):
        repo, conn = _repo()
        _save_trade_and_proposal(conn)
        record = repo.save(_execution(), now=_now())
        self.assertEqual(record.revision, 0)


class TestProposalFirstForeignKey(unittest.TestCase):
    def test_save_for_nonexistent_proposal_fails_clearly(self):
        repo, conn = _repo()
        _save_trade_only(conn)
        with self.assertRaises(ProposalDoesNotExistError):
            repo.save(_execution(), now=_now())

    def test_failed_save_persists_nothing(self):
        repo, conn = _repo()
        _save_trade_only(conn)
        with self.assertRaises(ProposalDoesNotExistError):
            repo.save(_execution(), now=_now())
        self.assertIsNone(repo.get("E-1"))

    def test_repository_never_creates_a_proposal(self):
        repo, conn = _repo()
        _save_trade_only(conn)
        with self.assertRaises(ProposalDoesNotExistError):
            repo.save(_execution(), now=_now())
        proposal_row = conn.execute("SELECT 1 FROM proposals WHERE proposal_id = 'P-1'").fetchone()
        self.assertIsNone(proposal_row)

    def test_save_for_nonexistent_trade_fails_clearly(self):
        repo, conn = _repo()
        with self.assertRaises(TradeDoesNotExistError):
            repo.save(_execution(), now=_now())


class TestUniquenessConstraints(unittest.TestCase):
    def test_duplicate_execution_id_rejected(self):
        repo, conn = _repo()
        _save_trade_and_proposal(conn, proposal_id="P-1")
        _save_trade_and_proposal(conn, trade_id="T-2", proposal_id="P-2")
        repo.save(_execution(execution_id="E-1", proposal_id="P-1", client_order_id="C-1"), now=_now())
        with self.assertRaises(OrderExecutionAlreadyExistsError):
            repo.save(_execution(execution_id="E-1", proposal_id="P-2", client_order_id="C-2"), now=_now())

    def test_duplicate_proposal_id_rejected(self):
        repo, conn = _repo()
        _save_trade_and_proposal(conn, proposal_id="P-1")
        repo.save(_execution(execution_id="E-1", proposal_id="P-1", client_order_id="C-1"), now=_now())
        with self.assertRaises(OrderExecutionAlreadyExistsError):
            repo.save(_execution(execution_id="E-2", proposal_id="P-1", client_order_id="C-2"), now=_now())

    def test_duplicate_client_order_id_rejected(self):
        repo, conn = _repo()
        _save_trade_and_proposal(conn, proposal_id="P-1")
        _save_trade_and_proposal(conn, trade_id="T-2", proposal_id="P-2")
        repo.save(_execution(execution_id="E-1", proposal_id="P-1", client_order_id="C-1"), now=_now())
        with self.assertRaises(OrderExecutionAlreadyExistsError):
            repo.save(_execution(execution_id="E-2", proposal_id="P-2", client_order_id="C-1"), now=_now())

    def test_duplicate_broker_order_id_rejected(self):
        repo, conn = _repo()
        _save_trade_and_proposal(conn, proposal_id="P-1")
        _save_trade_and_proposal(conn, trade_id="T-2", proposal_id="P-2")
        acked_1 = _execution(
            execution_id="E-1", proposal_id="P-1", client_order_id="C-1",
            broker_order_id="B-1", status="accepted", last_broker_poll_at=_now(),
        )
        acked_2 = _execution(
            execution_id="E-2", proposal_id="P-2", client_order_id="C-2",
            broker_order_id="B-1", status="accepted", last_broker_poll_at=_now(),
        )
        repo.save(acked_1, now=_now())
        with self.assertRaises(OrderExecutionAlreadyExistsError):
            repo.save(acked_2, now=_now())

    def test_multiple_null_broker_order_id_allowed(self):
        repo, conn = _repo()
        _save_trade_and_proposal(conn, proposal_id="P-1")
        _save_trade_and_proposal(conn, trade_id="T-2", proposal_id="P-2")
        repo.save(_execution(execution_id="E-1", proposal_id="P-1", client_order_id="C-1"), now=_now())
        # Both rows have broker_order_id=None -- SQLite's UNIQUE allows
        # unlimited NULLs; this must not raise.
        repo.save(_execution(execution_id="E-2", proposal_id="P-2", client_order_id="C-2"), now=_now())
        self.assertIsNone(repo.get("E-1").execution.broker_order_id)
        self.assertIsNone(repo.get("E-2").execution.broker_order_id)


class TestUpdate(unittest.TestCase):
    def test_update_round_trip_and_revision_increments_once(self):
        repo, conn = _repo()
        _save_trade_and_proposal(conn)
        record = repo.save(_execution(), now=_now())

        submitting = record.execution.start_submission(now=_now())
        updated = repo.update(submitting, expected_revision=record.revision, transition="submitting", now=_now())

        self.assertEqual(updated.revision, 1)
        fetched = repo.get("E-1")
        self.assertEqual(fetched.execution.status, "SUBMITTING")
        self.assertEqual(fetched.revision, 1)

    def test_stale_revision_conflict_performs_no_write(self):
        repo, conn = _repo()
        _save_trade_and_proposal(conn)
        record = repo.save(_execution(), now=_now())
        submitting = record.execution.start_submission(now=_now())

        with self.assertRaises(OrderExecutionRevisionConflictError):
            repo.update(submitting, expected_revision=99, transition="submitting", now=_now())

        # Nothing written -- status/revision unchanged.
        fetched = repo.get("E-1")
        self.assertEqual(fetched.execution.status, "CREATED")
        self.assertEqual(fetched.revision, 0)

    def test_multiple_updates_across_lifecycle(self):
        repo, conn = _repo()
        _save_trade_and_proposal(conn)
        record = repo.save(_execution(), now=_now())

        submitting = record.execution.start_submission(now=_now())
        record = repo.update(submitting, expected_revision=record.revision, transition="submitting", now=_now())

        acked = record.execution.record_broker_response(
            broker_order_id="B-1", status="accepted", is_terminal=False, now=_now()
        )
        record = repo.update(acked, expected_revision=record.revision, transition="acknowledged", now=_now())
        self.assertEqual(record.revision, 2)

        filled = record.execution.record_fill_update(
            filled_qty=10, filled_avg_price=100.0, status="filled", is_terminal=True, now=_now()
        )
        record = repo.update(filled, expected_revision=record.revision, transition="filled", now=_now())
        self.assertEqual(record.revision, 3)

        fetched = repo.get("E-1")
        self.assertEqual(fetched.execution.status, "filled")
        self.assertTrue(fetched.execution.is_broker_terminal)
        self.assertEqual(fetched.execution.broker_order_id, "B-1")
        self.assertEqual(fetched.execution.filled_qty, 10)


class TestListUnresolved(unittest.TestCase):
    def test_returns_exactly_rows_where_is_broker_terminal_is_zero(self):
        repo, conn = _repo()
        _save_trade_and_proposal(conn, proposal_id="P-1")
        _save_trade_and_proposal(conn, trade_id="T-2", proposal_id="P-2")
        _save_trade_and_proposal(conn, trade_id="T-3", proposal_id="P-3")

        # Unresolved: still CREATED.
        repo.save(_execution(execution_id="E-1", proposal_id="P-1", client_order_id="C-1"), now=_now())
        # Unresolved: an arbitrary, non-Alpaca-looking open broker status.
        repo.save(
            _execution(
                execution_id="E-2", proposal_id="P-2", client_order_id="C-2",
                broker_order_id="B-2", status="some_custom_open_status", last_broker_poll_at=_now(),
            ),
            now=_now(),
        )
        # Resolved: terminal, regardless of the status string's spelling.
        repo.save(
            _execution(
                execution_id="E-3", proposal_id="P-3", client_order_id="C-3",
                broker_order_id="B-3", status="whatever_terminal_label", is_broker_terminal=True,
                last_broker_poll_at=_now(),
            ),
            now=_now(),
        )

        unresolved_ids = {record.execution.execution_id for record in repo.list_unresolved()}
        self.assertEqual(unresolved_ids, {"E-1", "E-2"})

    def test_no_broker_vocabulary_interpreted_by_sql(self):
        # The query is a plain boolean filter -- an execution whose raw
        # status is a real Alpaca-shaped terminal word ("filled") but
        # whose is_broker_terminal is (incorrectly, hypothetically)
        # False is STILL returned as unresolved, proving SQL never
        # second-guesses the status string itself.
        repo, conn = _repo()
        _save_trade_and_proposal(conn, proposal_id="P-1")
        repo.save(
            _execution(
                execution_id="E-1", proposal_id="P-1", client_order_id="C-1",
                broker_order_id="B-1", status="filled", is_broker_terminal=False,
                last_broker_poll_at=_now(),
            ),
            now=_now(),
        )
        unresolved_ids = {record.execution.execution_id for record in repo.list_unresolved()}
        self.assertEqual(unresolved_ids, {"E-1"})


class TestLookups(unittest.TestCase):
    def test_get_by_proposal_id(self):
        repo, conn = _repo()
        _save_trade_and_proposal(conn, proposal_id="P-1")
        repo.save(_execution(), now=_now())
        record = repo.get_by_proposal_id("P-1")
        self.assertEqual(record.execution.execution_id, "E-1")
        self.assertIsNone(repo.get_by_proposal_id("NOPE"))

    def test_get_by_client_order_id(self):
        repo, conn = _repo()
        _save_trade_and_proposal(conn, proposal_id="P-1")
        repo.save(_execution(), now=_now())
        record = repo.get_by_client_order_id("C-1")
        self.assertEqual(record.execution.execution_id, "E-1")
        self.assertIsNone(repo.get_by_client_order_id("NOPE"))


class TestRollback(unittest.TestCase):
    def test_forced_failure_leaves_no_partial_write(self):
        repo, conn = _repo()
        _save_trade_and_proposal(conn, proposal_id="P-1")
        _save_trade_and_proposal(conn, trade_id="T-2", proposal_id="P-2")
        repo.save(_execution(execution_id="E-1", proposal_id="P-1", client_order_id="C-1"), now=_now())

        # Attempting a second execution that collides on client_order_id
        # forces a genuine sqlite3.IntegrityError mid-transaction.
        with self.assertRaises(OrderExecutionAlreadyExistsError):
            repo.save(
                _execution(execution_id="E-2", proposal_id="P-2", client_order_id="C-1"), now=_now()
            )

        self.assertIsNone(repo.get("E-2"))
        self.assertIsNone(repo.get_by_proposal_id("P-2"))


class TestCorruptedRowRejection(unittest.TestCase):
    def test_get_raises_through_real_constructor_on_invariant_violation(self):
        repo, conn = _repo()
        _save_trade_and_proposal(conn, proposal_id="P-1")
        repo.save(_execution(), now=_now())
        # Corrupt the row directly: a local pre-broker status ("CREATED")
        # must never carry a broker_order_id -- OrderExecution's own
        # invariant, not a SQL CHECK.
        conn.execute("UPDATE order_executions SET broker_order_id = 'B-1' WHERE execution_id = 'E-1'")
        with self.assertRaises(OrderExecutionError):
            repo.get("E-1")

    def test_invalid_check_constraint_rejected_at_sql_level(self):
        repo, conn = _repo()
        _save_trade_and_proposal(conn, proposal_id="P-1")
        repo.save(_execution(), now=_now())
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE order_executions SET requested_qty = -1 WHERE execution_id = 'E-1'")


class TestReopenAndReload(unittest.TestCase):
    def test_close_reopen_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.sqlite")
            conn1 = connect(db_path)
            bootstrap_schema(conn1)
            _save_trade_and_proposal(conn1, proposal_id="P-1")
            SqliteOrderExecutionRepository(conn1).save(_execution(), now=_now())
            conn1.close()

            conn2 = connect(db_path)
            bootstrap_schema(conn2)
            record = SqliteOrderExecutionRepository(conn2).get("E-1")
            self.assertIsNotNone(record)
            self.assertEqual(record.execution.proposal_id, "P-1")
            conn2.close()


class TestConcurrency(unittest.TestCase):
    def test_begin_immediate_prevents_concurrent_update_race(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.sqlite")
            conn_a = connect(db_path)
            bootstrap_schema(conn_a)
            _save_trade_and_proposal(conn_a, proposal_id="P-1")
            repo_a = SqliteOrderExecutionRepository(conn_a)
            record = repo_a.save(_execution(), now=_now())

            conn_b = connect(db_path)
            conn_b.execute("PRAGMA busy_timeout = 0")
            repo_b = SqliteOrderExecutionRepository(conn_b)

            conn_a.execute("BEGIN IMMEDIATE")
            try:
                submitting = record.execution.start_submission(now=_now())
                with self.assertRaises(sqlite3.OperationalError):
                    repo_b.update(submitting, expected_revision=record.revision, transition="submitting", now=_now())
            finally:
                conn_a.execute("ROLLBACK")
                conn_a.close()
                conn_b.close()


if __name__ == "__main__":
    unittest.main()
