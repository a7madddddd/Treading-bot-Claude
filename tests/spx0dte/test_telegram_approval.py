import json
import unittest

from spx0dte.telegram_approval import (
    ControllerDecision, DecisionKind, SpxTelegramDecisionSource,
    TelegramConfigError, send_message, send_proposal_message,
)


class FakePost:
    def __init__(self, status=200, body=b"{}"):
        self.status = status
        self.body = body
        self.calls = []

    def __call__(self, url, payload, timeout):
        self.calls.append((url, payload, timeout))
        return self.status, self.body


class TestSendProposalMessage(unittest.TestCase):
    def test_success_includes_prefixed_callback_data(self):
        poster = FakePost(status=200)
        import spx0dte.telegram_approval as mod
        orig = mod._urllib_post_json
        mod._urllib_post_json = poster
        try:
            ok = send_proposal_message(
                bot_token="t", chat_id="123", text="hello", proposal_id="SPX-1",
            )
        finally:
            mod._urllib_post_json = orig
        self.assertTrue(ok)
        self.assertEqual(len(poster.calls), 1)
        _, payload, _ = poster.calls[0]
        buttons = payload["reply_markup"]["inline_keyboard"][0]
        self.assertEqual(buttons[0]["callback_data"], "spx_approve:SPX-1")
        self.assertEqual(buttons[1]["callback_data"], "spx_reject:SPX-1")

    def test_failure_status_returns_false(self):
        poster = FakePost(status=500)
        import spx0dte.telegram_approval as mod
        orig = mod._urllib_post_json
        mod._urllib_post_json = poster
        try:
            ok = send_proposal_message(bot_token="t", chat_id="123", text="hi", proposal_id="SPX-1")
        finally:
            mod._urllib_post_json = orig
        self.assertFalse(ok)


class TestSendMessage(unittest.TestCase):
    def test_success_no_inline_keyboard(self):
        poster = FakePost(status=200)
        import spx0dte.telegram_approval as mod
        orig = mod._urllib_post_json
        mod._urllib_post_json = poster
        try:
            ok = send_message(bot_token="t", chat_id="123", text="session started")
        finally:
            mod._urllib_post_json = orig
        self.assertTrue(ok)
        _, payload, _ = poster.calls[0]
        self.assertNotIn("reply_markup", payload)

    def test_failure_status_returns_false(self):
        poster = FakePost(status=500)
        import spx0dte.telegram_approval as mod
        orig = mod._urllib_post_json
        mod._urllib_post_json = poster
        try:
            ok = send_message(bot_token="t", chat_id="123", text="x")
        finally:
            mod._urllib_post_json = orig
        self.assertFalse(ok)


class TestSpxTelegramDecisionSourceConfig(unittest.TestCase):
    def test_requires_bot_token(self):
        with self.assertRaises(TelegramConfigError):
            SpxTelegramDecisionSource(bot_token="", admin_user_ids=[1])

    def test_requires_at_least_one_admin(self):
        with self.assertRaises(TelegramConfigError):
            SpxTelegramDecisionSource(bot_token="t", admin_user_ids=[])

    def test_rejects_non_positive_admin_id(self):
        with self.assertRaises(TelegramConfigError):
            SpxTelegramDecisionSource(bot_token="t", admin_user_ids=[-1])


def _getupdates_response(updates: list) -> tuple:
    body = json.dumps({"ok": True, "result": updates}).encode("utf-8")
    return 200, body


class TestParseCallback(unittest.TestCase):
    def setUp(self):
        self.source = SpxTelegramDecisionSource(bot_token="t", admin_user_ids=[111])

    def test_authorized_approve_enqueues_decision(self):
        update = {
            "update_id": 1,
            "callback_query": {
                "id": "cbid",
                "from": {"id": 111},
                "data": "spx_approve:SPX-1",
                "message": {"chat": {"id": 111}, "message_id": 5},
            },
        }
        calls = {"get": [], "post": []}

        def fake_get(url, timeout):
            calls["get"].append(url)
            if len(calls["get"]) == 1:
                return _getupdates_response([update])
            return _getupdates_response([])

        def fake_post(url, payload, timeout):
            calls["post"].append((url, payload))
            return 200, b"{}"

        source = SpxTelegramDecisionSource(
            bot_token="t", admin_user_ids=[111], http_get=fake_get, http_post=fake_post,
        )
        source._process_one_batch()
        decisions = source.poll()
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0], ControllerDecision(proposal_id="SPX-1", kind=DecisionKind.APPROVE, decided_by="111"))
        # ack + dismiss keyboard both attempted
        self.assertEqual(len(calls["post"]), 2)

    def test_unauthorized_sender_dropped(self):
        update = {
            "update_id": 2,
            "callback_query": {"id": "cbid", "from": {"id": 999}, "data": "spx_approve:SPX-1"},
        }

        def fake_get(url, timeout):
            return _getupdates_response([update])

        source = SpxTelegramDecisionSource(bot_token="t", admin_user_ids=[111], http_get=fake_get)
        source._process_one_batch()
        self.assertEqual(source.poll(), [])

    def test_reject_callback(self):
        update = {
            "update_id": 3,
            "callback_query": {"id": "cbid", "from": {"id": 111}, "data": "spx_reject:SPX-2"},
        }

        def fake_get(url, timeout):
            return _getupdates_response([update])

        source = SpxTelegramDecisionSource(bot_token="t", admin_user_ids=[111], http_get=fake_get)
        source._process_one_batch()
        decisions = source.poll()
        self.assertEqual(decisions[0].kind, DecisionKind.REJECT)
        self.assertEqual(decisions[0].proposal_id, "SPX-2")

    def test_offset_advances_even_for_dropped_update(self):
        update = {
            "update_id": 42,
            "callback_query": {"id": "cbid", "from": {"id": 999}, "data": "spx_approve:SPX-1"},
        }

        def fake_get(url, timeout):
            return _getupdates_response([update])

        source = SpxTelegramDecisionSource(bot_token="t", admin_user_ids=[111], http_get=fake_get)
        source._process_one_batch()
        self.assertEqual(source._offset, 43)


if __name__ == "__main__":
    unittest.main()
