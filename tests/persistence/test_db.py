import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from persistence.db import (
    APPROVED_SCHEMA_VERSION,
    MigrationError,
    PersistenceConnectionError,
    SchemaVersionError,
    bootstrap_schema,
    connect,
    get_schema_version,
    transaction,
    validate_migration_directory,
)


def _write_migration(directory, version, table_name, valid=True):
    """Writes a minimal, self-contained fixture migration file --
    deliberately unrelated to the real project schema, since these
    fixture-directory tests exercise the migration RUNNER mechanism
    itself, not the real trades/proposals schema (already covered by
    every other test in this file)."""
    path = Path(directory) / f"{version:04d}_{table_name}.sql"
    if valid:
        path.write_text(f"CREATE TABLE {table_name} (id INTEGER PRIMARY KEY);")
    else:
        path.write_text(f"CREATE TABLE {table_name} (id INTEGER PRIMARY KEY); THIS IS NOT VALID SQL;")
    return path


def _table_names_fixture(conn):
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r[0] for r in rows}

EXPECTED_TABLES = {"trades", "proposals", "trade_snapshots", "protective_order_history"}
EXPECTED_INDEXES = {
    "ix_trades_symbol",
    "ix_proposals_trade_action_state",
    "ix_proposals_symbol",
    "ux_proposals_one_live_pending",
}


def _table_names(conn):
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r[0] for r in rows}


def _index_names(conn):
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    return {r[0] for r in rows}


class TestFreshDatabaseBootstrap(unittest.TestCase):
    def test_creates_expected_tables(self):
        conn = connect(":memory:")
        bootstrap_schema(conn)
        self.assertTrue(EXPECTED_TABLES.issubset(_table_names(conn)))

    def test_creates_expected_indexes(self):
        conn = connect(":memory:")
        bootstrap_schema(conn)
        self.assertTrue(EXPECTED_INDEXES.issubset(_index_names(conn)))

    def test_schema_version_is_approved_version_after_bootstrap(self):
        conn = connect(":memory:")
        self.assertEqual(get_schema_version(conn), 0)
        bootstrap_schema(conn)
        self.assertEqual(get_schema_version(conn), APPROVED_SCHEMA_VERSION)
        self.assertEqual(APPROVED_SCHEMA_VERSION, 4)

    def test_trades_table_has_expected_columns(self):
        conn = connect(":memory:")
        bootstrap_schema(conn)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
        for expected in (
            "trade_id",
            "symbol",
            "created_at",
            "original_initial_entry_fill_price",
            "ladder1_price",
            "ladder2_price",
            "original_floor_price",
            "total_shares",
            "trailing_activated",
            "protective_order_id",
            "revision",
        ):
            self.assertIn(expected, columns)
        # active_floor_price/active_floor_source are derived, never persisted.
        self.assertNotIn("active_floor_price", columns)
        self.assertNotIn("active_floor_source", columns)
        # Trade carries no stored status field, by design.
        self.assertNotIn("status", columns)

    def test_proposals_table_has_expected_columns(self):
        conn = connect(":memory:")
        bootstrap_schema(conn)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(proposals)").fetchall()}
        for expected in (
            "proposal_id",
            "trade_id",
            "proposed_action",
            "approval_state",
            "approved_action",
            "expired_at",
            "assumptions_json",
            "risks_json",
        ):
            self.assertIn(expected, columns)

    def test_order_executions_table_exists_but_no_order_attempts_table_or_trade_client_order_id(self):
        # D-0024 continued: `order_executions` (OrderExecution
        # persistence, migration 0002) now exists -- this table's
        # earlier-considered-and-rejected name `order_attempts` never
        # does, and `client_order_id` still belongs exclusively to
        # `order_executions`, never to `trades`.
        conn = connect(":memory:")
        bootstrap_schema(conn)
        self.assertIn("order_executions", _table_names(conn))
        self.assertNotIn("order_attempts", _table_names(conn))
        trade_columns = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
        self.assertNotIn("client_order_id", trade_columns)
        execution_columns = {row[1] for row in conn.execute("PRAGMA table_info(order_executions)").fetchall()}
        self.assertIn("client_order_id", execution_columns)


class TestReopenIsSafeAndIdempotent(unittest.TestCase):
    def test_reopening_does_not_recreate_or_lose_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.sqlite")

            conn1 = connect(db_path)
            bootstrap_schema(conn1)
            with transaction(conn1):
                conn1.execute(
                    "INSERT INTO trades (trade_id, symbol, created_at) VALUES (?, ?, ?)",
                    ("T-1", "TSLA", "2026-09-17T14:00:00+00:00"),
                )
            conn1.close()

            conn2 = connect(db_path)
            bootstrap_schema(conn2)  # must be a no-op -- table already exists
            self.assertEqual(get_schema_version(conn2), APPROVED_SCHEMA_VERSION)
            row = conn2.execute("SELECT symbol FROM trades WHERE trade_id = ?", ("T-1",)).fetchone()
            self.assertEqual(row[0], "TSLA")
            conn2.close()

    def test_bootstrap_called_twice_on_same_connection_is_noop(self):
        conn = connect(":memory:")
        bootstrap_schema(conn)
        bootstrap_schema(conn)  # must not raise, must not recreate tables
        self.assertEqual(get_schema_version(conn), APPROVED_SCHEMA_VERSION)


class TestUnsupportedSchemaVersionFailsClosed(unittest.TestCase):
    def test_unknown_nonzero_version_raises(self):
        conn = connect(":memory:")
        conn.execute("PRAGMA user_version = 99")
        with self.assertRaises(SchemaVersionError):
            bootstrap_schema(conn)

    def test_unknown_version_leaves_no_tables_created(self):
        conn = connect(":memory:")
        conn.execute("PRAGMA user_version = 99")
        with self.assertRaises(SchemaVersionError):
            bootstrap_schema(conn)
        self.assertEqual(_table_names(conn), set())


class TestConstraintsEnforced(unittest.TestCase):
    def test_foreign_keys_are_enforced(self):
        conn = connect(":memory:")
        bootstrap_schema(conn)
        with self.assertRaises(sqlite3.IntegrityError):
            with transaction(conn):
                conn.execute(
                    "INSERT INTO proposals (proposal_id, trade_id, proposed_action, symbol, candidate_source, "
                    "current_price_at_proposal, proposed_entry, ladder_1_trigger, ladder_1_quantity, "
                    "ladder_2_trigger, ladder_2_quantity, floor_trigger, maximum_position, proposal_created_at, "
                    "assumptions_json, risks_json) VALUES "
                    "('P-1', 'does-not-exist', 'initial_entry', 'TSLA', 'fixed_watchlist', "
                    "100.0, 100.0, 95.0, 10, 92.0, 20, 90.0, 40, '2026-09-17T14:00:00+00:00', '[]', '[]')"
                )

    def test_invalid_enum_value_rejected(self):
        conn = connect(":memory:")
        bootstrap_schema(conn)
        with self.assertRaises(sqlite3.IntegrityError):
            with transaction(conn):
                conn.execute(
                    "INSERT INTO trades (trade_id, symbol, created_at, initial_order_status) "
                    "VALUES ('T-1', 'TSLA', '2026-09-17T14:00:00+00:00', 'not-a-real-status')"
                )

    def test_only_one_live_pending_per_trade_action(self):
        conn = connect(":memory:")
        bootstrap_schema(conn)
        with transaction(conn):
            conn.execute(
                "INSERT INTO trades (trade_id, symbol, created_at) VALUES ('T-1', 'TSLA', '2026-09-17T14:00:00+00:00')"
            )

        def _insert_pending(proposal_id):
            conn.execute(
                "INSERT INTO proposals (proposal_id, trade_id, proposed_action, symbol, candidate_source, "
                "current_price_at_proposal, proposed_entry, ladder_1_trigger, ladder_1_quantity, "
                "ladder_2_trigger, ladder_2_quantity, floor_trigger, maximum_position, proposal_created_at, "
                "assumptions_json, risks_json, approval_state) VALUES "
                f"('{proposal_id}', 'T-1', 'ladder_1', 'TSLA', 'fixed_watchlist', "
                "100.0, 100.0, 95.0, 10, 92.0, 20, 90.0, 40, '2026-09-17T14:00:00+00:00', '[]', '[]', 'pending')"
            )

        with transaction(conn):
            _insert_pending("P-1")
        with self.assertRaises(sqlite3.IntegrityError):
            with transaction(conn):
                _insert_pending("P-2")


class TestTransactionHelper(unittest.TestCase):
    def test_successful_transaction_commits(self):
        conn = connect(":memory:")
        bootstrap_schema(conn)
        with transaction(conn):
            conn.execute(
                "INSERT INTO trades (trade_id, symbol, created_at) VALUES ('T-1', 'TSLA', '2026-09-17T14:00:00+00:00')"
            )
        row = conn.execute("SELECT trade_id FROM trades WHERE trade_id='T-1'").fetchone()
        self.assertIsNotNone(row)

    def test_exception_inside_block_rolls_back(self):
        conn = connect(":memory:")
        bootstrap_schema(conn)
        with self.assertRaises(ValueError):
            with transaction(conn):
                conn.execute(
                    "INSERT INTO trades (trade_id, symbol, created_at) VALUES ('T-1', 'TSLA', '2026-09-17T14:00:00+00:00')"
                )
                raise ValueError("simulated failure mid-transaction")
        row = conn.execute("SELECT trade_id FROM trades WHERE trade_id='T-1'").fetchone()
        self.assertIsNone(row)

    def test_begin_immediate_blocks_a_second_writer(self):
        # A second connection's transaction() must fail/block while the
        # first is still open -- proves BEGIN IMMEDIATE acquires the
        # write lock up front, closing the Option E TOCTOU race.
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.sqlite")
            conn1 = connect(db_path)
            bootstrap_schema(conn1)

            conn2 = connect(db_path)
            conn2.execute("PRAGMA busy_timeout = 0")

            conn1.execute("BEGIN IMMEDIATE")
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    conn2.execute("BEGIN IMMEDIATE")
            finally:
                conn1.execute("ROLLBACK")
                conn1.close()
                conn2.close()


class TestConnectionFailureIsClear(unittest.TestCase):
    def test_invalid_path_raises_persistence_connection_error(self):
        with self.assertRaises(PersistenceConnectionError):
            connect("/this/directory/does/not/exist/test.sqlite")

    def test_error_message_names_the_path(self):
        bad_path = "/this/directory/does/not/exist/test.sqlite"
        with self.assertRaises(PersistenceConnectionError) as ctx:
            connect(bad_path)
        self.assertIn(bad_path, str(ctx.exception))


class TestMultiMigrationBootstrap(unittest.TestCase):
    """Exercises the migration RUNNER against synthetic fixture
    migration directories -- never the real project migrations
    directory -- so these tests are fully independent of whatever the
    real, currently-approved schema happens to be."""

    def test_fresh_database_applies_all_approved_migrations(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_migration(tmp, 1, "alpha")
            _write_migration(tmp, 2, "beta")
            _write_migration(tmp, 3, "gamma")

            conn = connect(":memory:")
            bootstrap_schema(conn, approved_version=3, migrations_dir=Path(tmp))

            self.assertEqual(get_schema_version(conn), 3)
            self.assertTrue({"alpha", "beta", "gamma"}.issubset(_table_names_fixture(conn)))

    def test_version_0_to_approved_latest(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_migration(tmp, 1, "alpha")
            _write_migration(tmp, 2, "beta")

            conn = connect(":memory:")
            self.assertEqual(get_schema_version(conn), 0)
            bootstrap_schema(conn, approved_version=2, migrations_dir=Path(tmp))
            self.assertEqual(get_schema_version(conn), 2)

    def test_version_1_to_version_2_using_fixture_migrations(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_migration(tmp, 1, "alpha")
            _write_migration(tmp, 2, "beta")

            conn = connect(":memory:")
            # Bootstrap only migration 1 first (simulates a database
            # already at version 1 from an earlier deployment).
            bootstrap_schema(conn, approved_version=1, migrations_dir=Path(tmp))
            self.assertEqual(get_schema_version(conn), 1)
            self.assertNotIn("beta", _table_names_fixture(conn))

            # Now the code's approved version advances to 2.
            bootstrap_schema(conn, approved_version=2, migrations_dir=Path(tmp))
            self.assertEqual(get_schema_version(conn), 2)
            self.assertIn("alpha", _table_names_fixture(conn))
            self.assertIn("beta", _table_names_fixture(conn))

    def test_already_current_is_a_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_migration(tmp, 1, "alpha")
            conn = connect(":memory:")
            bootstrap_schema(conn, approved_version=1, migrations_dir=Path(tmp))
            self.assertEqual(get_schema_version(conn), 1)

            # Calling again must not re-apply or error, and must not
            # touch the table (dropping the fixture file would prove a
            # re-apply was attempted -- instead we prove no error and
            # version is unchanged).
            bootstrap_schema(conn, approved_version=1, migrations_dir=Path(tmp))
            self.assertEqual(get_schema_version(conn), 1)

    def test_approved_migration_missing_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_migration(tmp, 1, "alpha")
            # No 0002 file, but approved_version=2 claims it should exist.
            conn = connect(":memory:")
            with self.assertRaises(MigrationError):
                bootstrap_schema(conn, approved_version=2, migrations_dir=Path(tmp))
            self.assertEqual(get_schema_version(conn), 0)

    def test_gap_inside_approved_range_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_migration(tmp, 1, "alpha")
            _write_migration(tmp, 3, "gamma")  # no 0002
            conn = connect(":memory:")
            with self.assertRaises(MigrationError):
                bootstrap_schema(conn, approved_version=3, migrations_dir=Path(tmp))
            self.assertEqual(get_schema_version(conn), 0)

    def test_duplicate_version_inside_approved_range_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_migration(tmp, 1, "alpha")
            (Path(tmp) / "0001_also_alpha.sql").write_text("CREATE TABLE also_alpha (id INTEGER PRIMARY KEY);")
            conn = connect(":memory:")
            with self.assertRaises(MigrationError):
                bootstrap_schema(conn, approved_version=1, migrations_dir=Path(tmp))
            self.assertEqual(get_schema_version(conn), 0)

    def test_database_version_above_approved_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_migration(tmp, 1, "alpha")
            conn = connect(":memory:")
            conn.execute("PRAGMA user_version = 5")
            with self.assertRaises(SchemaVersionError):
                bootstrap_schema(conn, approved_version=1, migrations_dir=Path(tmp))

    def test_migration_failure_rolls_back_entirely(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_migration(tmp, 1, "alpha")
            _write_migration(tmp, 2, "beta", valid=False)  # CREATE TABLE beta; then broken SQL

            conn = connect(":memory:")
            bootstrap_schema(conn, approved_version=1, migrations_dir=Path(tmp))
            with self.assertRaises(sqlite3.OperationalError):
                bootstrap_schema(conn, approved_version=2, migrations_dir=Path(tmp))

            # The valid first statement (CREATE TABLE beta) must have
            # been rolled back along with the rest of the transaction.
            self.assertNotIn("beta", _table_names_fixture(conn))

    def test_failed_migration_leaves_user_version_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_migration(tmp, 1, "alpha")
            _write_migration(tmp, 2, "beta", valid=False)

            conn = connect(":memory:")
            bootstrap_schema(conn, approved_version=1, migrations_dir=Path(tmp))
            with self.assertRaises(sqlite3.OperationalError):
                bootstrap_schema(conn, approved_version=2, migrations_dir=Path(tmp))
            self.assertEqual(get_schema_version(conn), 1)

    def test_retry_after_failed_migration_starts_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_migration(tmp, 1, "alpha")
            bad_path = _write_migration(tmp, 2, "beta", valid=False)

            conn = connect(":memory:")
            bootstrap_schema(conn, approved_version=1, migrations_dir=Path(tmp))
            with self.assertRaises(sqlite3.OperationalError):
                bootstrap_schema(conn, approved_version=2, migrations_dir=Path(tmp))

            # Fix the migration file and retry -- must resume cleanly
            # from version 1, not error about "already partially applied".
            bad_path.write_text("CREATE TABLE beta (id INTEGER PRIMARY KEY);")
            bootstrap_schema(conn, approved_version=2, migrations_dir=Path(tmp))
            self.assertEqual(get_schema_version(conn), 2)
            self.assertIn("beta", _table_names_fixture(conn))

    def test_migration_ordering_is_numeric_not_lexicographic(self):
        # A directory listing sorts "0002" before "0010" lexicographically
        # too (both zero-padded to 4 digits), so this specifically proves
        # parsing yields real integers rather than relying on string
        # comparisons anywhere in the apply path.
        from persistence.db import _parse_migration_version

        self.assertEqual(_parse_migration_version("0002_beta.sql"), 2)
        self.assertEqual(_parse_migration_version("0010_kappa.sql"), 10)
        self.assertLess(_parse_migration_version("0002_beta.sql"), _parse_migration_version("0010_kappa.sql"))

        with tempfile.TemporaryDirectory() as tmp:
            for v, name in [(1, "a"), (2, "b"), (3, "c")]:
                _write_migration(tmp, v, name)
            conn = connect(":memory:")
            bootstrap_schema(conn, approved_version=3, migrations_dir=Path(tmp))
            self.assertEqual(get_schema_version(conn), 3)
            self.assertTrue({"a", "b", "c"}.issubset(_table_names_fixture(conn)))

    def test_concurrent_bootstrap_fails_closed_never_corrupts(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_migration(tmp, 1, "alpha")
            _write_migration(tmp, 2, "beta")

            with tempfile.TemporaryDirectory() as db_dir:
                db_path = os.path.join(db_dir, "test.sqlite")
                conn1 = connect(db_path)
                bootstrap_schema(conn1, approved_version=1, migrations_dir=Path(tmp))

                conn2 = connect(db_path)
                conn2.execute("PRAGMA busy_timeout = 0")

                # conn1 holds the write lock mid-upgrade (simulating a
                # concurrent bootstrap attempt racing conn2's).
                conn1.execute("BEGIN IMMEDIATE")
                try:
                    with self.assertRaises(sqlite3.OperationalError):
                        bootstrap_schema(conn2, approved_version=2, migrations_dir=Path(tmp))
                finally:
                    conn1.execute("ROLLBACK")

                # conn2's failed attempt must not have corrupted or
                # partially advanced anything -- retry now succeeds cleanly.
                bootstrap_schema(conn2, approved_version=2, migrations_dir=Path(tmp))
                self.assertEqual(get_schema_version(conn2), 2)
                self.assertIn("beta", _table_names_fixture(conn2))

                conn1.close()
                conn2.close()

    def test_reopen_and_rerun_bootstrap_is_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_migration(tmp, 1, "alpha")
            _write_migration(tmp, 2, "beta")

            with tempfile.TemporaryDirectory() as db_dir:
                db_path = os.path.join(db_dir, "test.sqlite")
                conn1 = connect(db_path)
                bootstrap_schema(conn1, approved_version=2, migrations_dir=Path(tmp))
                conn1.close()

                conn2 = connect(db_path)
                bootstrap_schema(conn2, approved_version=2, migrations_dir=Path(tmp))
                self.assertEqual(get_schema_version(conn2), 2)
                self.assertTrue({"alpha", "beta"}.issubset(_table_names_fixture(conn2)))
                conn2.close()


class TestFutureMigrationsDoNotAffectRuntimeBootstrap(unittest.TestCase):
    def test_future_migration_above_approved_is_ignored_by_bootstrap(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_migration(tmp, 1, "alpha")
            # Deliberately broken future migration -- must have zero
            # effect on bootstrapping the currently-approved version 1.
            _write_migration(tmp, 2, "beta", valid=False)

            conn = connect(":memory:")
            bootstrap_schema(conn, approved_version=1, migrations_dir=Path(tmp))
            self.assertEqual(get_schema_version(conn), 1)
            self.assertIn("alpha", _table_names_fixture(conn))

    def test_future_gap_does_not_affect_approved_bootstrap(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_migration(tmp, 1, "alpha")
            _write_migration(tmp, 4, "delta")  # gap at 2/3, all above approved
            conn = connect(":memory:")
            bootstrap_schema(conn, approved_version=1, migrations_dir=Path(tmp))
            self.assertEqual(get_schema_version(conn), 1)

    def test_future_duplicate_does_not_affect_approved_bootstrap(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_migration(tmp, 1, "alpha")
            _write_migration(tmp, 2, "beta")
            (Path(tmp) / "0002_also_beta.sql").write_text("CREATE TABLE also_beta (id INTEGER PRIMARY KEY);")
            conn = connect(":memory:")
            bootstrap_schema(conn, approved_version=1, migrations_dir=Path(tmp))
            self.assertEqual(get_schema_version(conn), 1)


class TestFullDirectoryHygieneCheck(unittest.TestCase):
    """validate_migration_directory() -- never called by bootstrap_schema()
    itself; exercised directly here, exactly as it is meant to be used
    (from the test suite, independent of any running system)."""

    def test_clean_directory_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_migration(tmp, 1, "alpha")
            _write_migration(tmp, 2, "beta")
            validate_migration_directory(Path(tmp))  # must not raise

    def test_detects_malformed_future_migration_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_migration(tmp, 1, "alpha")
            (Path(tmp) / "not_a_valid_migration_name.sql").write_text("CREATE TABLE x (id INTEGER);")
            with self.assertRaises(MigrationError):
                validate_migration_directory(Path(tmp))

    def test_detects_gap_in_future_migration_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_migration(tmp, 1, "alpha")
            _write_migration(tmp, 3, "gamma")  # no 0002
            with self.assertRaises(MigrationError):
                validate_migration_directory(Path(tmp))

    def test_detects_duplicate_future_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_migration(tmp, 1, "alpha")
            _write_migration(tmp, 2, "beta")
            (Path(tmp) / "0002_also_beta.sql").write_text("CREATE TABLE also_beta (id INTEGER PRIMARY KEY);")
            with self.assertRaises(MigrationError):
                validate_migration_directory(Path(tmp))

    def test_real_project_migrations_directory_is_currently_clean(self):
        # Exercises the default (no argument) path against the REAL
        # migrations directory -- must pass today (only 0001 exists).
        validate_migration_directory()


class TestRealMigrationsDirectoryDefaultBehavior(unittest.TestCase):
    """Confirms bootstrap_schema(conn) with NO override arguments --
    i.e. every existing caller's exact call shape -- behaves
    identically against the real project migrations directory."""

    def test_default_bootstrap_still_reaches_approved_version(self):
        conn = connect(":memory:")
        bootstrap_schema(conn)
        self.assertEqual(get_schema_version(conn), APPROVED_SCHEMA_VERSION)
        self.assertEqual(APPROVED_SCHEMA_VERSION, 4)
        self.assertTrue(EXPECTED_TABLES.issubset(_table_names(conn)))


if __name__ == "__main__":
    unittest.main()
