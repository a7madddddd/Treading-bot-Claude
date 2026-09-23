import json
import threading
import time
import unittest
from typing import List, Mapping, Optional, Tuple

from engine.decision_source import ControllerDecision, DecisionKind
from notifications.telegram_decision import (
    TelegramDecisionConfigError,
    TelegramDecisionSource,
    TransportResponse,
)


# ---- test transport ----------------------------------------------------


class _StubTransport:
    """Scripted transport that returns queued responses in order.
    Falls back to an empty getUpdates result if the queue is drained,
    so a slow background thread doesn't crash tests."""

    def __init__(self, empty_after_queue: bool = True):
        self._queue: List = []
        self._empty_after_queue = empty_after_queue
        self.calls: List[Tuple[str, Optional[bytes]]] = []
        self._lock = threading.Lock()

    def queue(self, response: TransportResponse) -> None:
        with self._lock:
            self._queue.append(response)

    def queue_exception(self, exc: BaseException) -> None:
        with self._lock:
            self._queue.append(exc)

    def __call__(self, url: str, data: Optional[bytes], headers: Mapping[str, str], timeout: float) -> TransportResponse:
        with self._lock:
            self.calls.append((url, data))
            if self._queue:
                nxt = self._queue.pop(0)
            elif self._empty_after_queue:
                nxt = _ok_updates_body()
            else:
                raise AssertionError(f"unexpected transport call: {url}")
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt


def _ok_updates_body(updates=()) -> TransportResponse:
    return TransportResponse(
        status_code=200,
        body=json.dumps({"ok": True, "result": list(updates)}).encode("utf-8"),
    )


def _callback_update(*, update_id: int, sender_id: int, data: str) -> dict:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cbq-{update_id}",
            "from": {"id": sender_id, "first_name": "X"},
            "data": data,
        },
    }


def _text_update(*, update_id: int, sender_id: int, text: str) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "from": {"id": sender_id, "first_name": "X"},
            "text": text,
        },
    }


def _source(transport, *, admin_ids=(111,)) -> TelegramDecisionSource:
    return TelegramDecisionSource(
        bot_token="TEST_TOKEN",
        admin_user_ids=admin_ids,
        transport=transport,
        long_poll_seconds=0,          # keeps tests fast
        http_timeout_seconds=1.0,     # must exceed long_poll_seconds
        backoff_base_seconds=0.001,
        backoff_max_seconds=0.001,
        sleep_fn=lambda s: None,
    )


# ---- construction ------------------------------------------------------


class TestConstruction(unittest.TestCase):
    def test_rejects_empty_bot_token(self):
        with self.assertRaises(TelegramDecisionConfigError):
            TelegramDecisionSource(bot_token="", admin_user_ids=(111,))
        with self.assertRaises(TelegramDecisionConfigError):
            TelegramDecisionSource(bot_token="   ", admin_user_ids=(111,))

    def test_rejects_empty_admin_list(self):
        with self.assertRaises(TelegramDecisionConfigError):
            TelegramDecisionSource(bot_token="T", admin_user_ids=[])

    def test_rejects_non_positive_admin_id(self):
        with self.assertRaises(TelegramDecisionConfigError):
            TelegramDecisionSource(bot_token="T", admin_user_ids=[0])
        with self.assertRaises(TelegramDecisionConfigError):
            TelegramDecisionSource(bot_token="T", admin_user_ids=[-1])
        with self.assertRaises(TelegramDecisionConfigError):
            TelegramDecisionSource(bot_token="T", admin_user_ids=["111"])  # type: ignore[list-item]

    def test_rejects_http_timeout_not_exceeding_long_poll(self):
        with self.assertRaises(TelegramDecisionConfigError):
            TelegramDecisionSource(
                bot_token="T", admin_user_ids=[111],
                long_poll_seconds=25, http_timeout_seconds=25.0,
            )


# ---- direct pipeline: _process_one_batch + poll -----------------------


class TestParsingPipeline(unittest.TestCase):
    def test_callback_approve_produces_decision(self):
        transport = _StubTransport(empty_after_queue=False)
        transport.queue(_ok_updates_body(updates=[
            _callback_update(update_id=1, sender_id=111, data="approve:P-1"),
        ]))
        src = _source(transport)
        src._process_one_batch()
        decisions = src.poll()
        self.assertEqual(len(decisions), 1)
        d = decisions[0]
        self.assertEqual(d.proposal_id, "P-1")
        self.assertEqual(d.kind, DecisionKind.APPROVE)
        self.assertEqual(d.decided_by, "111")

    def test_callback_reject_produces_decision(self):
        transport = _StubTransport(empty_after_queue=False)
        transport.queue(_ok_updates_body(updates=[
            _callback_update(update_id=1, sender_id=111, data="reject:P-2"),
        ]))
        src = _source(transport)
        src._process_one_batch()
        decisions = src.poll()
        self.assertEqual(decisions[0].kind, DecisionKind.REJECT)
        self.assertEqual(decisions[0].proposal_id, "P-2")

    def test_callback_confirm_l2_produces_decision(self):
        transport = _StubTransport(empty_after_queue=False)
        transport.queue(_ok_updates_body(updates=[
            _callback_update(update_id=1, sender_id=111, data="confirm_l2:P-3"),
        ]))
        src = _source(transport)
        src._process_one_batch()
        decisions = src.poll()
        self.assertEqual(decisions[0].kind, DecisionKind.CONFIRM_LADDER2_PARTIAL_FILL)

    def test_text_command_approve_produces_decision(self):
        transport = _StubTransport(empty_after_queue=False)
        transport.queue(_ok_updates_body(updates=[
            _text_update(update_id=1, sender_id=111, text="/approve P-9"),
        ]))
        src = _source(transport)
        src._process_one_batch()
        decisions = src.poll()
        self.assertEqual(decisions[0].kind, DecisionKind.APPROVE)
        self.assertEqual(decisions[0].proposal_id, "P-9")

    def test_text_command_with_bot_mention_accepted(self):
        transport = _StubTransport(empty_after_queue=False)
        transport.queue(_ok_updates_body(updates=[
            _text_update(update_id=1, sender_id=111, text="/reject@MyBot P-4"),
        ]))
        src = _source(transport)
        src._process_one_batch()
        decisions = src.poll()
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].kind, DecisionKind.REJECT)

    def test_unauthorized_sender_is_dropped(self):
        transport = _StubTransport(empty_after_queue=False)
        transport.queue(_ok_updates_body(updates=[
            _callback_update(update_id=1, sender_id=999, data="approve:P-1"),
        ]))
        src = _source(transport, admin_ids=(111,))
        src._process_one_batch()
        self.assertEqual(src.poll(), [])

    def test_unknown_callback_data_is_dropped(self):
        transport = _StubTransport(empty_after_queue=False)
        transport.queue(_ok_updates_body(updates=[
            _callback_update(update_id=1, sender_id=111, data="mystery:P-1"),
            _callback_update(update_id=2, sender_id=111, data="approve"),  # no colon
            _callback_update(update_id=3, sender_id=111, data="approve:"),  # empty id
            _callback_update(update_id=4, sender_id=111, data="approve:not/valid"),  # bad id
        ]))
        src = _source(transport)
        src._process_one_batch()
        self.assertEqual(src.poll(), [])

    def test_non_command_text_is_ignored(self):
        transport = _StubTransport(empty_after_queue=False)
        transport.queue(_ok_updates_body(updates=[
            _text_update(update_id=1, sender_id=111, text="hello there"),
        ]))
        src = _source(transport)
        src._process_one_batch()
        self.assertEqual(src.poll(), [])

    def test_offset_advances_past_unauthorized_and_unknown(self):
        transport = _StubTransport(empty_after_queue=False)
        transport.queue(_ok_updates_body(updates=[
            _callback_update(update_id=5, sender_id=999, data="approve:P-1"),
            _callback_update(update_id=7, sender_id=111, data="mystery:P-2"),
        ]))
        src = _source(transport)
        src._process_one_batch()
        # Offset must advance to (max update_id + 1) = 8 so these
        # updates are never re-delivered.
        self.assertEqual(src._offset, 8)

    def test_offset_advances_past_valid_updates(self):
        transport = _StubTransport(empty_after_queue=False)
        transport.queue(_ok_updates_body(updates=[
            _callback_update(update_id=42, sender_id=111, data="approve:P-1"),
        ]))
        src = _source(transport)
        src._process_one_batch()
        self.assertEqual(src._offset, 43)

    def test_next_call_uses_advanced_offset(self):
        transport = _StubTransport(empty_after_queue=False)
        transport.queue(_ok_updates_body(updates=[
            _callback_update(update_id=42, sender_id=111, data="approve:P-1"),
        ]))
        transport.queue(_ok_updates_body(updates=[]))
        src = _source(transport)
        src._process_one_batch()
        src._process_one_batch()
        # The second URL must contain offset=43.
        self.assertIn("offset=43", transport.calls[1][0])

    def test_edited_message_is_ignored(self):
        transport = _StubTransport(empty_after_queue=False)
        transport.queue(_ok_updates_body(updates=[
            {"update_id": 10, "edited_message": {"text": "/approve P-1"}},
        ]))
        src = _source(transport)
        src._process_one_batch()
        self.assertEqual(src.poll(), [])

    def test_poll_drains_exactly_once(self):
        transport = _StubTransport(empty_after_queue=False)
        transport.queue(_ok_updates_body(updates=[
            _callback_update(update_id=1, sender_id=111, data="approve:P-1"),
            _callback_update(update_id=2, sender_id=111, data="reject:P-2"),
        ]))
        src = _source(transport)
        src._process_one_batch()
        self.assertEqual(len(src.poll()), 2)
        self.assertEqual(src.poll(), [])  # second call: nothing left


# ---- error handling ---------------------------------------------------


class TestErrorHandling(unittest.TestCase):
    def test_401_raises_permanent(self):
        transport = _StubTransport(empty_after_queue=False)
        transport.queue(TransportResponse(status_code=401, body=b'{"description":"bad token"}'))
        src = _source(transport)
        from notifications.telegram_decision import _PermanentTelegramError  # type: ignore[attr-defined]
        with self.assertRaises(_PermanentTelegramError):
            src._process_one_batch()

    def test_404_raises_permanent(self):
        transport = _StubTransport(empty_after_queue=False)
        transport.queue(TransportResponse(status_code=404, body=b""))
        src = _source(transport)
        from notifications.telegram_decision import _PermanentTelegramError  # type: ignore[attr-defined]
        with self.assertRaises(_PermanentTelegramError):
            src._process_one_batch()

    def test_5xx_raises_transient(self):
        transport = _StubTransport(empty_after_queue=False)
        transport.queue(TransportResponse(status_code=503, body=b""))
        src = _source(transport)
        from notifications.telegram_decision import _TransientTelegramError  # type: ignore[attr-defined]
        with self.assertRaises(_TransientTelegramError):
            src._process_one_batch()

    def test_ok_false_raises_transient(self):
        transport = _StubTransport(empty_after_queue=False)
        transport.queue(TransportResponse(
            status_code=200,
            body=json.dumps({"ok": False, "description": "err"}).encode("utf-8"),
        ))
        src = _source(transport)
        from notifications.telegram_decision import _TransientTelegramError  # type: ignore[attr-defined]
        with self.assertRaises(_TransientTelegramError):
            src._process_one_batch()

    def test_malformed_json_raises_transient(self):
        transport = _StubTransport(empty_after_queue=False)
        transport.queue(TransportResponse(status_code=200, body=b"not json"))
        src = _source(transport)
        from notifications.telegram_decision import _TransientTelegramError  # type: ignore[attr-defined]
        with self.assertRaises(_TransientTelegramError):
            src._process_one_batch()


# ---- background lifecycle --------------------------------------------


class TestBackgroundLifecycle(unittest.TestCase):
    def test_start_stop_cleanly(self):
        transport = _StubTransport(empty_after_queue=True)
        src = _source(transport)
        src.start()
        # Give the thread a moment to make at least one call.
        for _ in range(50):
            with transport._lock:
                if transport.calls:
                    break
            time.sleep(0.005)
        src.stop(timeout=2.0)
        self.assertFalse(
            src._thread is not None and src._thread.is_alive(),
            "background thread must not remain alive after stop()",
        )

    def test_start_is_idempotent(self):
        transport = _StubTransport(empty_after_queue=True)
        src = _source(transport)
        src.start()
        thread_ref = src._thread
        src.start()  # must not spawn a second thread
        self.assertIs(src._thread, thread_ref)
        src.stop(timeout=2.0)

    def test_context_manager_stops_thread(self):
        transport = _StubTransport(empty_after_queue=True)
        with _source(transport) as src:
            self.assertTrue(src._thread is not None and src._thread.is_alive())
        self.assertFalse(src._thread is not None and src._thread.is_alive())

    def test_decisions_from_background_reach_poll(self):
        transport = _StubTransport(empty_after_queue=True)
        transport.queue(_ok_updates_body(updates=[
            _callback_update(update_id=1, sender_id=111, data="approve:P-1"),
        ]))
        with _source(transport) as src:
            got: List[ControllerDecision] = []
            deadline = time.time() + 2.0
            while time.time() < deadline and not got:
                got = src.poll()
                if not got:
                    time.sleep(0.005)
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0].kind, DecisionKind.APPROVE)
            self.assertEqual(got[0].proposal_id, "P-1")


# ---- from_env --------------------------------------------------------


class TestFromEnv(unittest.TestCase):
    def _clear_env(self):
        import os
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        os.environ.pop("TELEGRAM_ADMIN_USER_IDS", None)

    def setUp(self):
        self._clear_env()

    def tearDown(self):
        self._clear_env()

    def test_missing_token_raises(self):
        import os
        os.environ["TELEGRAM_ADMIN_USER_IDS"] = "111"
        with self.assertRaises(TelegramDecisionConfigError):
            TelegramDecisionSource.from_env()

    def test_missing_admin_ids_raises(self):
        import os
        os.environ["TELEGRAM_BOT_TOKEN"] = "T"
        with self.assertRaises(TelegramDecisionConfigError):
            TelegramDecisionSource.from_env()

    def test_bad_admin_id_raises(self):
        import os
        os.environ["TELEGRAM_BOT_TOKEN"] = "T"
        os.environ["TELEGRAM_ADMIN_USER_IDS"] = "111,notanumber"
        with self.assertRaises(TelegramDecisionConfigError):
            TelegramDecisionSource.from_env()

    def test_parses_comma_separated_admin_ids(self):
        import os
        os.environ["TELEGRAM_BOT_TOKEN"] = "T"
        os.environ["TELEGRAM_ADMIN_USER_IDS"] = " 111 , 222 "
        src = TelegramDecisionSource.from_env(
            transport=_StubTransport(),
            long_poll_seconds=0, http_timeout_seconds=1.0,
        )
        self.assertEqual(src._admin_ids, {111, 222})


if __name__ == "__main__":
    unittest.main()
