import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from persistence.db import bootstrap_schema, connect
from proposals.models import TradeAction, approved_strategy_rule_set
from trade.models import InitialOrderStatus, Trade, describe_status
from trade.repository import TradeAlreadyExistsError, TradeRevisionConflictError
from trade.sqlite_repository import SqliteTradeRepository


def _now():
    return datetime(2026, 9, 17, 14, 0, 0, tzinfo=timezone.utc)


def _strategy():
    return approved_strategy_rule_set()


def _new_trade(trade_id="T-1", symbol="TSLA"):
    return Trade(trade_id=trade_id, symbol=symbol, created_at=_now())


def _frozen_trade(price=100.0, filled_shares=10, trade_id="T-1", symbol="TSLA"):
    return _new_trade(trade_id, symbol).freeze_initial_reference(
        order_status=InitialOrderStatus.FILLED,
        filled_shares=filled_shares,
        fill_price=price,
        strategy=_strategy(),
        now=_now(),
    )


def _repo():
    conn = connect(":memory:")
    bootstrap_schema(conn)
    return SqliteTradeRepository(conn), conn


class TestSaveGetRoundTrip(unittest.TestCase):
    def test_round_trip(self):
        repo, _ = _repo()
        trade = _new_trade()
        record = repo.save(trade, now=_now())
        self.assertEqual(record.revision, 0)
        fetched = repo.get("T-1")
        self.assertEqual(fetched.trade, trade)
        self.assertEqual(fetched.revision, 0)

    def test_save_revision_starts_at_zero(self):
        repo, _ = _repo()
        record = repo.save(_frozen_trade(), now=_now())
        self.assertEqual(record.revision, 0)

    def test_duplicate_trade_id_raises(self):
        repo, _ = _repo()
        trade = _new_trade()
        repo.save(trade, now=_now())
        with self.assertRaises(TradeAlreadyExistsError):
            repo.save(trade, now=_now())

    def test_get_missing_returns_none(self):
        repo, _ = _repo()
        self.assertIsNone(repo.get("does-not-exist"))


class TestUpdateGetRoundTrip(unittest.TestCase):
    def test_round_trip_after_freeze(self):
        repo, _ = _repo()
        repo.save(_new_trade(), now=_now())
        frozen = _frozen_trade()
        record = repo.update(frozen, expected_revision=0, transition="freeze_initial_reference", now=_now())
        self.assertEqual(record.revision, 1)
        fetched = repo.get("T-1")
        self.assertEqual(fetched.trade, frozen)
        self.assertEqual(fetched.revision, 1)

    def test_revision_increments_by_exactly_one_each_successful_update(self):
        repo, _ = _repo()
        repo.save(_new_trade(), now=_now())
        t = _frozen_trade()
        r0 = repo.update(t, expected_revision=0, transition="freeze", now=_now())
        self.assertEqual(r0.revision, 1)

        t = t.record_ladder_fill(
            TradeAction.LADDER_1,
            order_id="o1",
            fill_price=95.0,
            fill_qty=10,
            new_total_shares=20,
            new_weighted_avg_entry_price=97.5,
            strategy=_strategy(),
        )
        r1 = repo.update(t, expected_revision=1, transition="record_ladder_fill:LADDER_1", now=_now())
        self.assertEqual(r1.revision, 2)

        t = t.activate_trailing(current_price=110.0, now=_now())
        r2 = repo.update(t, expected_revision=2, transition="activate_trailing", now=_now())
        self.assertEqual(r2.revision, 3)

    def test_all_transition_methods_round_trip(self):
        repo, _ = _repo()
        repo.save(_new_trade(), now=_now())

        t = _frozen_trade()
        repo.update(t, expected_revision=0, transition="freeze", now=_now())

        t = t.record_ladder_fill(
            TradeAction.LADDER_1,
            order_id="o1",
            fill_price=95.0,
            fill_qty=10,
            new_total_shares=20,
            new_weighted_avg_entry_price=97.5,
            strategy=_strategy(),
        )
        repo.update(t, expected_revision=1, transition="ladder1", now=_now())

        t = t.record_ladder_fill(
            TradeAction.LADDER_2,
            order_id="o2",
            fill_price=92.0,
            fill_qty=20,
            new_total_shares=40,
            new_weighted_avg_entry_price=94.75,
            strategy=_strategy(),
        )
        repo.update(t, expected_revision=2, transition="ladder2", now=_now())

        t = t.activate_trailing(current_price=110.0, now=_now())
        repo.update(t, expected_revision=3, transition="activate", now=_now())

        t = t.ratchet_trailing(current_price=115.5, now=_now())
        repo.update(t, expected_revision=4, transition="ratchet", now=_now())

        t = t.update_protective_order(order_id="stop-1", stop_price=104.5, status="open", now=_now())
        repo.update(t, expected_revision=5, transition="protective_order", now=_now())

        t = t.record_reconciliation(now=_now(), error=None)
        record = repo.update(t, expected_revision=6, transition="reconciliation", now=_now())

        self.assertEqual(record.revision, 7)
        fetched = repo.get("T-1")
        self.assertEqual(fetched.trade, t)


class TestRevisionConflict(unittest.TestCase):
    def test_stale_revision_raises_and_writes_nothing(self):
        repo, _ = _repo()
        repo.save(_new_trade(), now=_now())
        t = _frozen_trade()
        repo.update(t, expected_revision=0, transition="freeze", now=_now())  # -> revision 1

        before = repo.get("T-1")

        # Caller B still thinks it's at revision 0.
        stale = t.record_ladder_fill(
            TradeAction.LADDER_1,
            order_id="o1",
            fill_price=95.0,
            fill_qty=10,
            new_total_shares=20,
            new_weighted_avg_entry_price=97.5,
            strategy=_strategy(),
        )
        with self.assertRaises(TradeRevisionConflictError):
            repo.update(stale, expected_revision=0, transition="stale_attempt", now=_now())

        after = repo.get("T-1")
        self.assertEqual(before, after)  # nothing written by the failed attempt
        self.assertEqual(after.revision, 1)

    def test_conflict_creates_no_snapshot(self):
        repo, conn = _repo()
        repo.save(_new_trade(), now=_now())
        t = _frozen_trade()
        repo.update(t, expected_revision=0, transition="freeze", now=_now())

        with self.assertRaises(TradeRevisionConflictError):
            repo.update(t, expected_revision=0, transition="stale_attempt", now=_now())

        rows = conn.execute(
            "SELECT revision FROM trade_snapshots WHERE trade_id = ? ORDER BY revision", ("T-1",)
        ).fetchall()
        self.assertEqual([r[0] for r in rows], [0, 1])  # only 'created' (0) and the one real update (1)

    def test_conflict_creates_no_protective_order_history_row(self):
        repo, conn = _repo()
        repo.save(_new_trade(), now=_now())
        t = _frozen_trade()
        repo.update(t, expected_revision=0, transition="freeze", now=_now())
        stale_with_stop = t.update_protective_order(order_id="stop-x", stop_price=90.0, status="open", now=_now())

        with self.assertRaises(TradeRevisionConflictError):
            repo.update(stale_with_stop, expected_revision=0, transition="stale_stop", now=_now())

        count = conn.execute(
            "SELECT COUNT(*) FROM protective_order_history WHERE trade_id = ?", ("T-1",)
        ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_update_on_unknown_trade_raises(self):
        repo, _ = _repo()
        with self.assertRaises(Exception):
            repo.update(_new_trade(trade_id="ghost"), expected_revision=0, transition="x", now=_now())

    def test_concurrency_race_second_writer_blocked_then_fails_cleanly(self):
        # Two separate connections to the SAME file -- the real
        # BEGIN IMMEDIATE-backed concurrency scenario from the design.
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.sqlite")
            conn_a = connect(db_path)
            bootstrap_schema(conn_a)
            repo_a = SqliteTradeRepository(conn_a)
            repo_a.save(_new_trade(), now=_now())
            t = _frozen_trade()
            repo_a.update(t, expected_revision=0, transition="freeze", now=_now())  # -> revision 1

            conn_b = connect(db_path)
            conn_b.execute("PRAGMA busy_timeout = 0")
            repo_b = SqliteTradeRepository(conn_b)

            # Caller B loaded the trade before A's update committed (or
            # otherwise still believes revision 0 is current).
            stale = t.activate_trailing(current_price=110.0, now=_now())
            with self.assertRaises(TradeRevisionConflictError):
                repo_b.update(stale, expected_revision=0, transition="stale", now=_now())

            self.assertEqual(repo_a.get("T-1").revision, 1)
            conn_a.close()
            conn_b.close()


class TestRollback(unittest.TestCase):
    def test_forced_failure_mid_transaction_leaves_no_partial_write(self):
        # Force a REAL failure partway through update()'s transaction:
        # pre-insert a trade_snapshots row that collides with the one
        # update() will try to insert for revision=1 (UNIQUE(trade_id,
        # revision)). The trades row UPDATE that already happened
        # earlier in the same transaction must be rolled back too.
        repo, conn = _repo()
        repo.save(_new_trade(), now=_now())
        conn.execute(
            "INSERT INTO trade_snapshots (trade_id, revision, snapshot_at, transition, full_state_json) "
            "VALUES ('T-1', 1, '2026-01-01T00:00:00+00:00', 'collision', '{}')"
        )

        t = _frozen_trade()
        with self.assertRaises(sqlite3.IntegrityError):
            repo.update(t, expected_revision=0, transition="freeze", now=_now())

        # The UPDATE to trades must have been rolled back too.
        fetched = repo.get("T-1")
        self.assertEqual(fetched.revision, 0)
        self.assertIsNone(fetched.trade.original_initial_entry_fill_price)


class TestSnapshots(unittest.TestCase):
    def test_snapshot_contents_after_update(self):
        repo, conn = _repo()
        repo.save(_new_trade(), now=_now())
        t = _frozen_trade()
        repo.update(t, expected_revision=0, transition="freeze_initial_reference", now=_now() + timedelta(minutes=1))

        row = conn.execute(
            "SELECT revision, transition, snapshot_at, full_state_json FROM trade_snapshots "
            "WHERE trade_id = ? AND revision = 1",
            ("T-1",),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 1)
        self.assertEqual(row[1], "freeze_initial_reference")
        self.assertEqual(row[2], (_now() + timedelta(minutes=1)).isoformat())

    def test_full_state_json_round_trips_to_same_trade(self):
        from trade.serialization import row_to_trade

        repo, conn = _repo()
        repo.save(_new_trade(), now=_now())
        t = _frozen_trade()
        repo.update(t, expected_revision=0, transition="freeze", now=_now())

        raw = conn.execute(
            "SELECT full_state_json FROM trade_snapshots WHERE trade_id = ? AND revision = 1", ("T-1",)
        ).fetchone()[0]
        rebuilt = row_to_trade(json.loads(raw))
        self.assertEqual(rebuilt, t)

    def test_repository_never_infers_transition_label(self):
        repo, conn = _repo()
        repo.save(_new_trade(), now=_now())
        t = _frozen_trade()
        repo.update(t, expected_revision=0, transition="whatever-the-caller-said", now=_now())
        stored = conn.execute(
            "SELECT transition FROM trade_snapshots WHERE trade_id = ? AND revision = 1", ("T-1",)
        ).fetchone()[0]
        self.assertEqual(stored, "whatever-the-caller-said")


class TestProtectiveOrderHistory(unittest.TestCase):
    def test_initial_placement_recorded(self):
        repo, conn = _repo()
        repo.save(_new_trade(), now=_now())
        t = _frozen_trade()
        repo.update(t, expected_revision=0, transition="freeze", now=_now())
        t = t.update_protective_order(order_id="stop-1", stop_price=90.0, status="open", now=_now())
        repo.update(t, expected_revision=1, transition="protective_order", now=_now())

        rows = conn.execute(
            "SELECT sequence, order_id FROM protective_order_history WHERE trade_id = ? ORDER BY sequence",
            ("T-1",),
        ).fetchall()
        self.assertEqual([(r[0], r[1]) for r in rows], [(1, "stop-1")])

    def test_replacement_recorded(self):
        repo, conn = _repo()
        repo.save(_new_trade(), now=_now())
        t = _frozen_trade()
        repo.update(t, expected_revision=0, transition="freeze", now=_now())
        t = t.update_protective_order(order_id="stop-1", stop_price=90.0, status="open", now=_now())
        repo.update(t, expected_revision=1, transition="protective_order_1", now=_now())
        t = t.update_protective_order(order_id="stop-2", stop_price=104.5, status="open", now=_now())
        repo.update(t, expected_revision=2, transition="protective_order_2", now=_now())

        rows = conn.execute(
            "SELECT sequence, order_id FROM protective_order_history WHERE trade_id = ? ORDER BY sequence",
            ("T-1",),
        ).fetchall()
        self.assertEqual([(r[0], r[1]) for r in rows], [(1, "stop-1"), (2, "stop-2")])

        fetched = repo.get("T-1")
        self.assertEqual(fetched.trade.protective_order_id, "stop-2")
        self.assertEqual(fetched.trade.protective_order_lineage, ("stop-1",))

    def test_unchanged_protective_order_not_recorded_again(self):
        repo, conn = _repo()
        repo.save(_new_trade(), now=_now())
        t = _frozen_trade()
        repo.update(t, expected_revision=0, transition="freeze", now=_now())
        t = t.update_protective_order(order_id="stop-1", stop_price=90.0, status="open", now=_now())
        repo.update(t, expected_revision=1, transition="protective_order", now=_now())

        # A later, unrelated update (e.g. reconciliation bookkeeping)
        # that does not change protective_order_id at all.
        t2 = t.record_reconciliation(now=_now())
        repo.update(t2, expected_revision=2, transition="reconciliation", now=_now())

        count = conn.execute(
            "SELECT COUNT(*) FROM protective_order_history WHERE trade_id = ?", ("T-1",)
        ).fetchone()[0]
        self.assertEqual(count, 1)


class TestCorruptedRowRejection(unittest.TestCase):
    def test_get_raises_on_invariant_violation(self):
        repo, conn = _repo()
        repo.save(_new_trade(), now=_now())
        t = _frozen_trade()
        repo.update(t, expected_revision=0, transition="freeze", now=_now())

        conn.execute("UPDATE trades SET original_initial_entry_fill_price = NULL WHERE trade_id = ?", ("T-1",))
        with self.assertRaises(Exception):
            repo.get("T-1")


class TestListActive(unittest.TestCase):
    def test_covers_all_describe_status_cases(self):
        repo, _ = _repo()

        repo.save(_new_trade(trade_id="T-await"), now=_now())

        repo.save(_new_trade(trade_id="T-abandoned"), now=_now())
        abandoned = _new_trade(trade_id="T-abandoned").freeze_initial_reference(
            order_status=InitialOrderStatus.EXPIRED,
            filled_shares=0,
            fill_price=None,
            strategy=_strategy(),
            now=_now(),
        )
        repo.update(abandoned, expected_revision=0, transition="abandon", now=_now())

        repo.save(_new_trade(trade_id="T-active"), now=_now())
        active = _frozen_trade(trade_id="T-active")
        repo.update(active, expected_revision=0, transition="freeze", now=_now())

        repo.save(_new_trade(trade_id="T-closed"), now=_now())
        closed_base = _frozen_trade(trade_id="T-closed")
        repo.update(closed_base, expected_revision=0, transition="freeze", now=_now())
        closed = closed_base.reconcile_position(
            total_shares=0, weighted_avg_entry_price=None, strategy=_strategy(), now=_now()
        )
        repo.update(closed, expected_revision=1, transition="close", now=_now())

        active_ids = {record.trade.trade_id for record in repo.list_active()}
        self.assertEqual(active_ids, {"T-await", "T-active"})

        for tid in ("T-await", "T-active"):
            self.assertIn(describe_status(repo.get(tid).trade), ("AWAITING_INITIAL_FILL", "ACTIVE"))
        self.assertEqual(describe_status(repo.get("T-abandoned").trade), "ABANDONED")
        self.assertEqual(describe_status(repo.get("T-closed").trade), "CLOSED")


class TestListForSymbol(unittest.TestCase):
    def test_returns_only_matching_symbol(self):
        repo, _ = _repo()
        repo.save(_new_trade(trade_id="T-1", symbol="TSLA"), now=_now())
        repo.save(_new_trade(trade_id="T-2", symbol="TSLA"), now=_now())
        repo.save(_new_trade(trade_id="T-3", symbol="DELL"), now=_now())

        tsla_ids = {r.trade.trade_id for r in repo.list_for_symbol("TSLA")}
        self.assertEqual(tsla_ids, {"T-1", "T-2"})
        dell_ids = {r.trade.trade_id for r in repo.list_for_symbol("DELL")}
        self.assertEqual(dell_ids, {"T-3"})


class TestReopenAndReload(unittest.TestCase):
    def test_close_reopen_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.sqlite")
            conn1 = connect(db_path)
            bootstrap_schema(conn1)
            repo1 = SqliteTradeRepository(conn1)
            repo1.save(_new_trade(), now=_now())
            t = _frozen_trade()
            repo1.update(t, expected_revision=0, transition="freeze", now=_now())
            conn1.close()

            conn2 = connect(db_path)
            bootstrap_schema(conn2)  # no-op, already bootstrapped
            repo2 = SqliteTradeRepository(conn2)
            fetched = repo2.get("T-1")
            self.assertEqual(fetched.trade, t)
            self.assertEqual(fetched.revision, 1)
            conn2.close()


class TestFrozenReferenceNeverMutatesAcrossUpdates(unittest.TestCase):
    def test_original_levels_unchanged_through_full_lifecycle(self):
        repo, _ = _repo()
        repo.save(_new_trade(), now=_now())
        t = _frozen_trade(price=100.0)
        repo.update(t, expected_revision=0, transition="freeze", now=_now())
        frozen_values = (
            t.original_initial_entry_fill_price,
            t.ladder1_price,
            t.ladder2_price,
            t.original_floor_price,
        )

        t = t.record_ladder_fill(
            TradeAction.LADDER_1,
            order_id="o1",
            fill_price=95.0,
            fill_qty=10,
            new_total_shares=20,
            new_weighted_avg_entry_price=97.5,
            strategy=_strategy(),
        )
        repo.update(t, expected_revision=1, transition="ladder1", now=_now())

        t = t.record_ladder_fill(
            TradeAction.LADDER_2,
            order_id="o2",
            fill_price=92.0,
            fill_qty=20,
            new_total_shares=40,
            new_weighted_avg_entry_price=94.75,
            strategy=_strategy(),
        )
        repo.update(t, expected_revision=2, transition="ladder2", now=_now())

        t = t.activate_trailing(current_price=110.0, now=_now())
        repo.update(t, expected_revision=3, transition="activate", now=_now())

        fetched = repo.get("T-1").trade
        self.assertEqual(
            (
                fetched.original_initial_entry_fill_price,
                fetched.ladder1_price,
                fetched.ladder2_price,
                fetched.original_floor_price,
            ),
            frozen_values,
        )


class TestNeverPersistedFieldsAtRepositoryLevel(unittest.TestCase):
    def test_trades_row_has_no_derived_or_status_columns(self):
        repo, conn = _repo()
        repo.save(_frozen_trade(), now=_now())
        columns = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
        self.assertNotIn("active_floor_price", columns)
        self.assertNotIn("active_floor_source", columns)
        self.assertNotIn("status", columns)


if __name__ == "__main__":
    unittest.main()
