"""P-052: the daily database backup must tell the Controller what
happened, every time, in a form he can act on.

Controller's request, 2026-10-05: a short Telegram message saying the
database was updated for today on success, and a failure message on
failure.

These test `classify`, the pure decision about what to report. The git
and network work is deliberately not exercised here — what matters for
correctness is that no outcome is silent and that a failure is never
mistaken for a quiet day.
"""

import importlib.util
import os
import unittest


_here = os.path.abspath(os.path.dirname(__file__))
_repo_root = os.path.abspath(os.path.join(_here, "..", ".."))


def _load():
    path = os.path.join(_repo_root, "scripts", "backup_db.py")
    spec = importlib.util.spec_from_file_location("_backup_db", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_M = _load()
DAY = "2026-10-06"


class TestSuccess(unittest.TestCase):
    def _ok(self):
        return _M.classify(changed=True, error=None, commit="abc1234",
                           size_bytes=3 * 1024 * 1024, today=DAY)

    def test_status_is_pushed(self):
        self.assertEqual(self._ok().status, "pushed")

    def test_it_says_the_database_was_updated_for_today(self):
        self.assertIn("Database updated for 2026-10-06", self._ok().message)

    def test_it_names_the_commit(self):
        self.assertIn("abc1234", self._ok().message)

    def test_it_names_the_size(self):
        self.assertIn("3.0 MB", self._ok().message)

    def test_it_is_important_not_critical(self):
        # A successful backup must not use the level reserved for things
        # that threaten money.
        self.assertEqual(self._ok().level, "IMPORTANT")


class TestQuietDay(unittest.TestCase):
    def _quiet(self):
        return _M.classify(changed=False, error=None, today=DAY)

    def test_a_quiet_day_is_still_reported(self):
        # Silence would be ambiguous: the Controller could not tell
        # "nothing changed" from "the job is broken".
        self.assertTrue(self._quiet().message.strip())

    def test_it_says_the_job_ran(self):
        self.assertIn("healthy", self._quiet().message)

    def test_it_is_optional_level(self):
        self.assertEqual(self._quiet().level, "OPTIONAL")

    def test_it_is_not_reported_as_a_failure(self):
        self.assertEqual(self._quiet().status, "no_change")


class TestFailure(unittest.TestCase):
    def _bad(self):
        return _M.classify(changed=False,
                           error="git push: Permission denied (publickey)",
                           today=DAY)

    def test_status_is_failed(self):
        self.assertEqual(self._bad().status, "failed")

    def test_it_is_critical(self):
        self.assertEqual(self._bad().level, "CRITICAL")

    def test_it_carries_the_real_git_error(self):
        # Without this the Controller cannot tell a credential problem
        # from a network problem, which is what cost days before.
        self.assertIn("Permission denied", self._bad().message)

    def test_it_reassures_that_trading_is_unaffected(self):
        body = self._bad().message
        self.assertIn("still safe on the VM", body)
        self.assertIn("Nothing about trading is affected", body)

    def test_an_error_always_wins_over_changed(self):
        # A failure after a change must never be reported as a success.
        out = _M.classify(changed=True, error="boom", commit="x", today=DAY)
        self.assertEqual(out.status, "failed")


class TestNoOutcomeIsSilent(unittest.TestCase):
    def test_every_outcome_has_a_message_and_a_known_level(self):
        for kwargs in (
            dict(changed=True, error=None, commit="c", size_bytes=1),
            dict(changed=False, error=None),
            dict(changed=False, error="e"),
            dict(changed=True, error="e"),
        ):
            out = _M.classify(today=DAY, **kwargs)
            self.assertTrue(out.message.strip(), kwargs)
            self.assertIn(out.level, ("IMPORTANT", "OPTIONAL", "CRITICAL"))


class TestItOnlyEverTouchesTheDatabase(unittest.TestCase):
    def test_the_commit_is_scoped_to_one_file(self):
        # The guarantee the per-tick persister was approved with in 2026-10-01
        # and the reason an experiment can never ride along on a backup.
        with open(os.path.join(_repo_root, "scripts", "backup_db.py"),
                  encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn('"commit", "--only", DB_FILENAME', src)
        self.assertNotIn('"commit", "-a"', src)
        self.assertNotIn('"add", "."', src)

    def test_the_engine_keeps_no_db_push(self):
        with open(os.path.join(_repo_root, "deploy", "engine-run.sh"),
                  encoding="utf-8") as fh:
            self.assertIn("--no-db-push", fh.read())


if __name__ == "__main__":
    unittest.main()
