"""Tests for scripts/session_status.py and scripts/session_summary.py.

Both scripts are read-only over a bootstrapped SQLite DB. Tests
seed the DB with a small fixture and invoke the scripts as
subprocesses to verify they print sensible output.
"""

import os
import subprocess
import sys
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone


_here = os.path.abspath(os.path.dirname(__file__))
_repo_root = os.path.abspath(os.path.join(_here, "..", ".."))


def _bootstrap(db_path: str) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.join(_repo_root, "src")
    code = (
        "from persistence.db import connect, bootstrap_schema; "
        f"c = connect({db_path!r}); bootstrap_schema(c); c.close()"
    )
    subprocess.check_call([sys.executable, "-c", code], env=env)


def _seed(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    now = datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO engine_lock (id, pid, host, started_at, heartbeat_at) "
        "VALUES (1, 1234, 'testhost', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO trades (trade_id, symbol, created_at, "
        "initial_order_status, initial_order_reconciled, "
        "initial_filled_shares, total_shares, weighted_avg_entry_price, "
        "original_initial_entry_fill_price, freeze_timestamp, "
        "ladder1_price, ladder2_price, original_floor_price) "
        "VALUES ('T-1', 'AAPL', ?, 'filled', 1, 10, 10, 100.0, "
        "100.0, ?, 95.0, 92.0, 90.0)",
        (now, now),
    )
    conn.commit()
    conn.close()


class TestSessionStatus(unittest.TestCase):
    def test_prints_engine_lock_and_trades(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "s.sqlite")
            _bootstrap(db)
            _seed(db)
            env = os.environ.copy()
            env["PYTHONPATH"] = os.path.join(_repo_root, "src")
            r = subprocess.run(
                [sys.executable,
                 os.path.join(_repo_root, "scripts", "session_status.py"),
                 "--db-path", db],
                env=env, capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("engine_lock", r.stdout)
            self.assertIn("AAPL", r.stdout)
            self.assertIn("T-1", r.stdout)

    def test_fails_gracefully_when_db_missing(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = os.path.join(_repo_root, "src")
        r = subprocess.run(
            [sys.executable,
             os.path.join(_repo_root, "scripts", "session_status.py"),
             "--db-path", "/nonexistent/nope.sqlite"],
            env=env, capture_output=True, text=True, timeout=15,
        )
        self.assertNotEqual(r.returncode, 0)


class TestSessionSummary(unittest.TestCase):
    def test_prints_summary_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "s.sqlite")
            _bootstrap(db)
            _seed(db)
            env = os.environ.copy()
            env["PYTHONPATH"] = os.path.join(_repo_root, "src")
            r = subprocess.run(
                [sys.executable,
                 os.path.join(_repo_root, "scripts", "session_summary.py"),
                 "--db-path", db],
                env=env, capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("TRADES", r.stdout)
            self.assertIn("PROPOSALS", r.stdout)
            self.assertIn("EXECUTIONS", r.stdout)
            self.assertIn("REALIZED P&L", r.stdout)


if __name__ == "__main__":
    unittest.main()
