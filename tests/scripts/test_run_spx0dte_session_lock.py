"""Tests for the single-instance flock added to
scripts/run_spx0dte_session.py (2026-11-22, P-068/P-070 session's own
real incident: two run_spx0dte_session.py processes were found running
concurrently against the same spx0dte.sqlite and broker account).

Loads the script as a module directly (it has no import-time side
effects beyond a sys.path insertion and logging.basicConfig) so the
locking function can be exercised without spawning a real subprocess --
`fcntl.flock` is per OPEN FILE DESCRIPTION, not per-process, so two
separate `open()` calls to the same path from this same test process
still correctly simulate two independent processes contending for the
same lock file.
"""
import importlib.util
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
_SRC = os.path.join(_REPO_ROOT, "src")
_SCRIPTS = os.path.join(_REPO_ROOT, "scripts")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "run_spx0dte_session", os.path.join(_REPO_ROOT, "scripts", "run_spx0dte_session.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestSingleInstanceLock(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()
        fd, self.lock_path = tempfile.mkstemp(suffix=".lock")
        os.close(fd)
        os.remove(self.lock_path)  # the function itself creates it via open(..., "w")
        self.mod._LOCK_FILE_PATH = self.lock_path

    def tearDown(self):
        if self.mod._lock_file_handle is not None:
            self.mod._lock_file_handle.close()
        if os.path.exists(self.lock_path):
            os.remove(self.lock_path)

    def test_first_acquire_succeeds(self):
        self.mod._acquire_single_instance_lock()
        self.assertIsNotNone(self.mod._lock_file_handle)
        with open(self.lock_path) as f:
            self.assertEqual(f.read().strip(), str(os.getpid()))

    def test_second_acquire_in_the_same_process_exits(self):
        # First "instance" acquires and holds the lock (its handle stays open --
        # flock is released only on close, same as a real process exiting).
        self.mod._acquire_single_instance_lock()
        held_handle = self.mod._lock_file_handle

        # A "second instance" -- fresh module load, same lock path -- must refuse to start.
        second = _load_module()
        second._LOCK_FILE_PATH = self.lock_path
        with self.assertRaises(SystemExit) as ctx:
            second._acquire_single_instance_lock()
        self.assertEqual(ctx.exception.code, 75)

        held_handle.close()  # release the first "instance"'s lock, same as process exit

    def test_lock_is_released_after_close_so_a_later_instance_can_start(self):
        self.mod._acquire_single_instance_lock()
        self.mod._lock_file_handle.close()  # simulates the first instance exiting
        self.mod._lock_file_handle = None

        second = _load_module()
        second._LOCK_FILE_PATH = self.lock_path
        second._acquire_single_instance_lock()  # must not raise
        self.assertIsNotNone(second._lock_file_handle)
        second._lock_file_handle.close()


if __name__ == "__main__":
    unittest.main()
