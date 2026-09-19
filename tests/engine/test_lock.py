import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from engine.lock import EngineLock, EngineLockHeldError, EngineLockNotHeldError
from persistence.db import bootstrap_schema, connect


def _now():
    return datetime(2026, 9, 19, 14, 0, 0, tzinfo=timezone.utc)


class LockTestCase(unittest.TestCase):
    """Uses a real temp-file database, never :memory: -- :memory:
    databases are not shared across separate connections, so a
    concurrency test against two independent EngineLock instances
    (simulating two Engine processes) requires a real file."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmpdir.name) / "engine.db")
        conn = connect(self.db_path)
        bootstrap_schema(conn)
        conn.close()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _new_connection(self):
        return connect(self.db_path)


class TestAcquireAndRelease(LockTestCase):
    def test_acquire_succeeds_when_no_lock_exists(self):
        conn = self._new_connection()
        lock = EngineLock(conn, pid=111, host="host-a")
        lock.acquire(now=_now())
        self.assertTrue(lock.held)

        state = lock.current_state()
        self.assertEqual(state.pid, 111)
        self.assertEqual(state.host, "host-a")
        self.assertEqual(state.started_at, _now())
        self.assertEqual(state.heartbeat_at, _now())

    def test_second_process_rejected_while_heartbeat_is_fresh(self):
        conn_a = self._new_connection()
        conn_b = self._new_connection()
        lock_a = EngineLock(conn_a, pid=111, host="host-a")
        lock_b = EngineLock(conn_b, pid=222, host="host-b")

        lock_a.acquire(now=_now())
        with self.assertRaises(EngineLockHeldError):
            lock_b.acquire(now=_now() + timedelta(seconds=10))

        # The rejected attempt must not have touched the row.
        state = lock_a.current_state()
        self.assertEqual(state.pid, 111)

    def test_clean_shutdown_releases_lock_for_a_new_owner(self):
        conn_a = self._new_connection()
        conn_b = self._new_connection()
        lock_a = EngineLock(conn_a, pid=111, host="host-a")
        lock_b = EngineLock(conn_b, pid=222, host="host-b")

        lock_a.acquire(now=_now())
        lock_a.release()
        self.assertFalse(lock_a.held)
        self.assertIsNone(lock_a.current_state())

        # A new owner can acquire immediately -- no staleness wait needed.
        lock_b.acquire(now=_now() + timedelta(seconds=1))
        self.assertEqual(lock_b.current_state().pid, 222)

    def test_release_is_a_noop_if_never_held(self):
        conn = self._new_connection()
        lock = EngineLock(conn, pid=111, host="host-a")
        lock.release()  # must not raise
        self.assertIsNone(lock.current_state())

    def test_release_does_not_delete_a_lock_stolen_from_it(self):
        conn_a = self._new_connection()
        conn_b = self._new_connection()
        lock_a = EngineLock(conn_a, pid=111, host="host-a")
        lock_b = EngineLock(conn_b, pid=222, host="host-b")

        lock_a.acquire(now=_now())
        # Simulate lock_a going stale and being stolen by lock_b.
        conn_a.execute(
            "UPDATE engine_lock SET heartbeat_at = ? WHERE id = 1",
            ((_now() - timedelta(minutes=10)).isoformat(),),
        )
        lock_b.acquire(now=_now())
        self.assertEqual(lock_b.current_state().pid, 222)

        # lock_a's own release() must be a no-op now -- it must never
        # delete lock_b's live row.
        lock_a.release()
        self.assertEqual(lock_b.current_state().pid, 222)


class TestHeartbeat(LockTestCase):
    def test_heartbeat_updates_the_timestamp(self):
        conn = self._new_connection()
        lock = EngineLock(conn, pid=111, host="host-a")
        lock.acquire(now=_now())

        later = _now() + timedelta(seconds=30)
        lock.heartbeat(now=later)
        self.assertEqual(lock.current_state().heartbeat_at, later)

    def test_heartbeat_without_holding_raises(self):
        conn = self._new_connection()
        lock = EngineLock(conn, pid=111, host="host-a")
        with self.assertRaises(EngineLockNotHeldError):
            lock.heartbeat(now=_now())

    def test_heartbeat_after_losing_ownership_raises(self):
        conn_a = self._new_connection()
        conn_b = self._new_connection()
        lock_a = EngineLock(conn_a, pid=111, host="host-a")
        lock_b = EngineLock(conn_b, pid=222, host="host-b")

        lock_a.acquire(now=_now())
        conn_a.execute(
            "UPDATE engine_lock SET heartbeat_at = ? WHERE id = 1",
            ((_now() - timedelta(minutes=10)).isoformat(),),
        )
        lock_b.acquire(now=_now())  # steals it

        with self.assertRaises(EngineLockNotHeldError):
            lock_a.heartbeat(now=_now())


class TestStaleLockRecoveryAndRestart(LockTestCase):
    def test_stale_lock_is_stolen_past_the_threshold(self):
        conn_a = self._new_connection()
        conn_b = self._new_connection()
        lock_a = EngineLock(conn_a, pid=111, host="host-a", stale_threshold_seconds=300.0)
        lock_b = EngineLock(conn_b, pid=222, host="host-b", stale_threshold_seconds=300.0)

        lock_a.acquire(now=_now())
        crash_time = _now() + timedelta(seconds=45)  # last real heartbeat before "crash"
        conn_a.execute(
            "UPDATE engine_lock SET heartbeat_at = ? WHERE id = 1", (crash_time.isoformat(),)
        )

        # Just under the threshold -- still refused.
        with self.assertRaises(EngineLockHeldError):
            lock_b.acquire(now=crash_time + timedelta(seconds=299))

        # Past the threshold -- restart after crash succeeds.
        lock_b.acquire(now=crash_time + timedelta(seconds=301))
        state = lock_b.current_state()
        self.assertEqual(state.pid, 222)
        self.assertEqual(state.host, "host-b")

    def test_restarted_owner_can_heartbeat_normally_after_stealing(self):
        conn_a = self._new_connection()
        conn_b = self._new_connection()
        lock_a = EngineLock(conn_a, pid=111, host="host-a", stale_threshold_seconds=300.0)
        lock_b = EngineLock(conn_b, pid=222, host="host-b", stale_threshold_seconds=300.0)

        lock_a.acquire(now=_now())
        conn_a.execute(
            "UPDATE engine_lock SET heartbeat_at = ? WHERE id = 1",
            ((_now() - timedelta(minutes=10)).isoformat(),),
        )
        lock_b.acquire(now=_now())
        lock_b.heartbeat(now=_now() + timedelta(seconds=30))
        self.assertEqual(lock_b.current_state().heartbeat_at, _now() + timedelta(seconds=30))


class TestConcurrentAcquisitionRace(LockTestCase):
    def test_two_real_threads_racing_to_acquire_only_one_wins(self):
        results = {}

        def attempt(name, pid):
            conn = self._new_connection()
            lock = EngineLock(conn, pid=pid, host=name)
            try:
                lock.acquire(now=_now())
                results[name] = "acquired"
            except EngineLockHeldError:
                results[name] = "rejected"
            finally:
                conn.close()

        t1 = threading.Thread(target=attempt, args=("thread-a", 111))
        t2 = threading.Thread(target=attempt, args=("thread-b", 222))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        outcomes = list(results.values())
        self.assertEqual(outcomes.count("acquired"), 1)
        self.assertEqual(outcomes.count("rejected"), 1)


if __name__ == "__main__":
    unittest.main()
