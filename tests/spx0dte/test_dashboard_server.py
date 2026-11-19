"""Tests for the live dashboard's HTTP server (scripts/spx0dte_dashboard_server.py),
in particular the manual "Sell Now" POST /close/<id> endpoint added
2026-11-19 -- the Controller's own request. The script lives in
scripts/, not src/, so it is imported via sys.path like
run_spx0dte_session.py itself does."""
import http.client
import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
_SCRIPTS = os.path.join(_REPO_ROOT, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from spx0dte_dashboard_server import start_server_in_background  # noqa: E402


class _FakeState:
    """Stand-in for LiveSessionState -- only the one field this
    endpoint touches."""

    def __init__(self):
        self.manual_close_requests = set()


class TestClosePostEndpoint(unittest.TestCase):
    def setUp(self):
        self.state = _FakeState()
        # Port 0 would be ideal, but ThreadingHTTPServer needs a fixed port to
        # hand to http.client afterwards -- use a high, unlikely-to-collide one.
        self.port = 18787
        self.server = start_server_in_background(
            status_file_path="/tmp/spx0dte_test_status_does_not_exist.json",
            port=self.port, state=self.state,
        )

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def _post(self, path: str):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            conn.request("POST", path)
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    def test_close_request_is_added_to_state(self):
        status, raw_body = self._post("/close/SPX-abc123")
        body = json.loads(raw_body)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "queued")
        self.assertEqual(body["proposal_id"], "SPX-abc123")
        self.assertIn("SPX-abc123", self.state.manual_close_requests)

    def test_empty_proposal_id_rejected(self):
        status, _ = self._post("/close/")
        self.assertEqual(status, 400)
        self.assertEqual(self.state.manual_close_requests, set())

    def test_unknown_path_is_404(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            conn.request("POST", "/not-a-real-path")
            resp = conn.getresponse()
            resp.read()
            self.assertEqual(resp.status, 404)
        finally:
            conn.close()


class TestClosePostWithoutLiveState(unittest.TestCase):
    """A standalone dashboard process (no `state`, e.g. `main()`'s own
    CLI entry point) must acknowledge honestly that it cannot queue
    anything, never a false "queued"."""

    def setUp(self):
        self.port = 18788
        self.server = start_server_in_background(
            status_file_path="/tmp/spx0dte_test_status_does_not_exist.json",
            port=self.port, state=None,
        )

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_no_state_reports_no_live_session(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            conn.request("POST", "/close/SPX-xyz")
            resp = conn.getresponse()
            body = json.loads(resp.read())
        finally:
            conn.close()
        self.assertEqual(body["status"], "no_live_session")


if __name__ == "__main__":
    unittest.main()
