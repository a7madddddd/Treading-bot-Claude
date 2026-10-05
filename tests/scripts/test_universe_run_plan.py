"""P-036 + P-037 wiring in scripts/run_universe_selection.py.

These test `plan_run`, the pure decision the runner makes BEFORE it
touches the broker: may this run publish a snapshot, and what
candidate-pool size will it insist on.

Why this exists. On 2026-10-05 a capped 40-candidate experiment wrote
the production snapshot for the day; the engine then traded a
one-symbol universe for a whole session with nothing reporting it.
Every case below is one way that must stay impossible.
"""

import importlib.util
import os
import unittest


_here = os.path.abspath(os.path.dirname(__file__))
_repo_root = os.path.abspath(os.path.join(_here, "..", ".."))


def _load():
    path = os.path.join(_repo_root, "scripts", "run_universe_selection.py")
    spec = importlib.util.spec_from_file_location("_uni_runner", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_M = _load()


class TestP037CappedRunsDoNotPublish(unittest.TestCase):
    def test_capped_run_does_not_persist(self):
        plan = _M.plan_run(max_symbols=40, allow_test_snapshot=False,
                           whitelist=(), min_candidates=500)
        self.assertFalse(plan.persist)

    def test_the_exact_2026_10_05_invocation_would_not_have_published(self):
        # 40 candidates, no opt-in flag: the run that caused the incident.
        plan = _M.plan_run(max_symbols=40, allow_test_snapshot=False,
                           whitelist=(), min_candidates=500)
        self.assertFalse(plan.persist)
        self.assertIn("NOT saved", plan.reason)

    def test_capped_run_publishes_only_with_the_explicit_opt_in(self):
        plan = _M.plan_run(max_symbols=40, allow_test_snapshot=True,
                           whitelist=(), min_candidates=500)
        self.assertTrue(plan.persist)

    def test_a_small_cap_is_still_a_cap(self):
        plan = _M.plan_run(max_symbols=1, allow_test_snapshot=False,
                           whitelist=(), min_candidates=500)
        self.assertFalse(plan.persist)

    def test_zero_cap_means_no_cap_and_publishes(self):
        plan = _M.plan_run(max_symbols=0, allow_test_snapshot=False,
                           whitelist=(), min_candidates=500)
        self.assertTrue(plan.persist)

    def test_reason_is_always_populated_for_the_log(self):
        for kwargs in (
            dict(max_symbols=40, allow_test_snapshot=False,
                 whitelist=(), min_candidates=500),
            dict(max_symbols=40, allow_test_snapshot=True,
                 whitelist=(), min_candidates=500),
            dict(max_symbols=0, allow_test_snapshot=False,
                 whitelist=("TSLA",), min_candidates=500),
            dict(max_symbols=0, allow_test_snapshot=False,
                 whitelist=(), min_candidates=500),
        ):
            self.assertTrue(_M.plan_run(**kwargs).reason.strip())


class TestP036ThresholdWiring(unittest.TestCase):
    def test_full_run_carries_the_configured_threshold(self):
        plan = _M.plan_run(max_symbols=0, allow_test_snapshot=False,
                           whitelist=(), min_candidates=500)
        self.assertEqual(plan.min_raw_candidates, 500)

    def test_whitelist_run_suppresses_the_threshold(self):
        # A whitelist is a stated intent to run on a small pool, not a
        # broken fetch; the guard would refuse every whitelist run.
        plan = _M.plan_run(max_symbols=0, allow_test_snapshot=False,
                           whitelist=("TSLA", "AAPL"), min_candidates=500)
        self.assertEqual(plan.min_raw_candidates, 0)
        self.assertTrue(plan.persist)

    def test_capped_run_suppresses_the_threshold(self):
        # P-037 already made it harmless; refusing as well would just
        # make quick experiments impossible.
        plan = _M.plan_run(max_symbols=40, allow_test_snapshot=False,
                           whitelist=(), min_candidates=500)
        self.assertEqual(plan.min_raw_candidates, 0)

    def test_negative_threshold_is_clamped_not_propagated(self):
        # The pipeline raises ValueError on a negative value; the runner
        # must never be the thing that crashes a nightly job.
        plan = _M.plan_run(max_symbols=0, allow_test_snapshot=False,
                           whitelist=(), min_candidates=-5)
        self.assertEqual(plan.min_raw_candidates, 0)

    def test_threshold_zero_is_honoured_as_disabled(self):
        plan = _M.plan_run(max_symbols=0, allow_test_snapshot=False,
                           whitelist=(), min_candidates=0)
        self.assertEqual(plan.min_raw_candidates, 0)
        self.assertTrue(plan.persist)


class TestDefaultsMatchTheProductionInvocation(unittest.TestCase):
    def test_production_path_passes_no_cap_and_no_whitelist(self):
        # deploy/universe-refresh.sh runs the script with
        # --send-telegram only, so the defaults ARE production.
        path = os.path.join(_repo_root, "deploy", "universe-refresh.sh")
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        # Only the invocation itself -- the file's comments discuss
        # these flags deliberately, and matching those would make this
        # test pass or fail on prose.
        exec_lines = [ln for ln in lines
                      if ln.strip().startswith("exec ")
                      or "run_universe_selection.py" in ln
                      and not ln.strip().startswith("#")]
        self.assertTrue(exec_lines, "no invocation line found")
        invocation = " ".join(exec_lines)
        self.assertNotIn("--max-symbols", invocation)
        self.assertNotIn("--whitelist", invocation)
        self.assertNotIn("--allow-test-snapshot", invocation)

    def test_default_min_candidates_is_a_real_guard(self):
        import argparse
        # Mirror of the parser default; a regression to 0 would silently
        # disable P-036 on the nightly run.
        path = os.path.join(_repo_root, "scripts",
                            "run_universe_selection.py")
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn('p.add_argument("--min-candidates", type=int, '
                      'default=500,', body)
        del argparse


if __name__ == "__main__":
    unittest.main()
