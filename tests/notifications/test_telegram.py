import json
import unittest
from datetime import datetime, timezone

from notifications.service import NotificationEvent, NotificationLevel
from notifications.telegram import TelegramConfigError, TelegramNotificationService, TransportResponse


class FakeTransport:
    """Records every call and returns a pre-programmed sequence of
    responses (or raises a pre-programmed exception), so tests never
    perform a real network call and never need a real bot token."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, url, data, headers, timeout):
        self.calls.append({"url": url, "data": data, "headers": dict(headers), "timeout": timeout})
        next_item = self._responses.pop(0)
        if isinstance(next_item, Exception):
            raise next_item
        return next_item


def _no_sleep(_seconds):
    return None


def _make_event(**overrides):
    defaults = dict(
        level=NotificationLevel.CRITICAL,
        event="ORDER_FILLED",
        message="TSLA buy filled",
        symbol="TSLA",
        timestamp=datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return NotificationEvent(**defaults)


class TestConstruction(unittest.TestCase):
    def test_missing_bot_token_raises_config_error(self):
        with self.assertRaises(TelegramConfigError):
            TelegramNotificationService(bot_token="", chat_id="123")

    def test_missing_chat_id_raises_config_error(self):
        with self.assertRaises(TelegramConfigError):
            TelegramNotificationService(bot_token="abc:def", chat_id="")

    def test_whitespace_only_token_raises_config_error(self):
        with self.assertRaises(TelegramConfigError):
            TelegramNotificationService(bot_token="   ", chat_id="123")

    def test_from_env_raises_when_env_vars_absent(self):
        import os

        saved = {k: os.environ.pop(k, None) for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")}
        try:
            with self.assertRaises(TelegramConfigError):
                TelegramNotificationService.from_env()
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def test_config_error_message_never_contains_token_value(self):
        try:
            TelegramNotificationService(bot_token="", chat_id="123")
        except TelegramConfigError as exc:
            self.assertNotIn("SECRET_TOKEN_VALUE", str(exc))


class TestSuccessfulSend(unittest.TestCase):
    def test_successful_send_returns_success_result(self):
        transport = FakeTransport([TransportResponse(status_code=200, body=b'{"ok":true}')])
        service = TelegramNotificationService(
            bot_token="123:ABC", chat_id="456", transport=transport, sleep_fn=_no_sleep
        )
        result = service.send(_make_event())
        self.assertTrue(result.success)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(result.status_code, 200)
        self.assertIsNone(result.error)

    def test_request_payload_correctness(self):
        transport = FakeTransport([TransportResponse(status_code=200, body=b"{}")])
        service = TelegramNotificationService(
            bot_token="123:ABC", chat_id="456", transport=transport, sleep_fn=_no_sleep
        )
        service.send(_make_event())

        self.assertEqual(len(transport.calls), 1)
        call = transport.calls[0]
        self.assertEqual(call["url"], "https://api.telegram.org/bot123:ABC/sendMessage")
        self.assertEqual(call["headers"]["Content-Type"], "application/json")

        body = json.loads(call["data"].decode("utf-8"))
        self.assertEqual(body["chat_id"], "456")
        self.assertIn("TSLA buy filled", body["text"])
        self.assertIn("CRITICAL", body["text"])
        self.assertIn("ORDER_FILLED", body["text"])
        self.assertIn("TSLA", body["text"])

    def test_message_formatting_includes_extra_fields_and_timestamp(self):
        transport = FakeTransport([TransportResponse(status_code=200, body=b"{}")])
        service = TelegramNotificationService(
            bot_token="123:ABC", chat_id="456", transport=transport, sleep_fn=_no_sleep
        )
        event = _make_event(extra=(("qty", "10"), ("price", "95.00")))
        service.send(event)

        body = json.loads(transport.calls[0]["data"].decode("utf-8"))
        self.assertIn("qty: 10", body["text"])
        self.assertIn("price: 95.00", body["text"])
        self.assertIn("2026-09-16T12:00:00+00:00", body["text"])


class TestTransientFailureRetry(unittest.TestCase):
    def test_retries_on_5xx_then_succeeds(self):
        transport = FakeTransport(
            [
                TransportResponse(status_code=500, body=b"server error"),
                TransportResponse(status_code=200, body=b"{}"),
            ]
        )
        service = TelegramNotificationService(
            bot_token="123:ABC", chat_id="456", transport=transport, sleep_fn=_no_sleep, max_attempts=3
        )
        result = service.send(_make_event())
        self.assertTrue(result.success)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(len(transport.calls), 2)

    def test_retries_on_429_rate_limit(self):
        transport = FakeTransport(
            [
                TransportResponse(status_code=429, body=b"too many requests"),
                TransportResponse(status_code=200, body=b"{}"),
            ]
        )
        service = TelegramNotificationService(
            bot_token="123:ABC", chat_id="456", transport=transport, sleep_fn=_no_sleep
        )
        result = service.send(_make_event())
        self.assertTrue(result.success)
        self.assertEqual(result.attempts, 2)

    def test_retries_on_transport_exception_then_succeeds(self):
        transport = FakeTransport(
            [
                ConnectionError("connection reset"),
                TransportResponse(status_code=200, body=b"{}"),
            ]
        )
        service = TelegramNotificationService(
            bot_token="123:ABC", chat_id="456", transport=transport, sleep_fn=_no_sleep
        )
        result = service.send(_make_event())
        self.assertTrue(result.success)
        self.assertEqual(result.attempts, 2)

    def test_gives_up_after_max_attempts_on_persistent_transient_failure(self):
        transport = FakeTransport(
            [
                TransportResponse(status_code=500, body=b"e1"),
                TransportResponse(status_code=500, body=b"e2"),
                TransportResponse(status_code=500, body=b"e3"),
            ]
        )
        service = TelegramNotificationService(
            bot_token="123:ABC", chat_id="456", transport=transport, sleep_fn=_no_sleep, max_attempts=3
        )
        result = service.send(_make_event())
        self.assertFalse(result.success)
        self.assertEqual(result.attempts, 3)
        self.assertEqual(len(transport.calls), 3)
        self.assertIsNotNone(result.error)


class TestPermanentFailureNoRetry(unittest.TestCase):
    def test_401_unauthorized_is_not_retried(self):
        transport = FakeTransport([TransportResponse(status_code=401, body=b"unauthorized")])
        service = TelegramNotificationService(
            bot_token="123:ABC", chat_id="456", transport=transport, sleep_fn=_no_sleep, max_attempts=3
        )
        result = service.send(_make_event())
        self.assertFalse(result.success)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(result.status_code, 401)

    def test_400_bad_request_is_not_retried(self):
        transport = FakeTransport([TransportResponse(status_code=400, body=b"bad chat id")])
        service = TelegramNotificationService(
            bot_token="123:ABC", chat_id="456", transport=transport, sleep_fn=_no_sleep, max_attempts=3
        )
        result = service.send(_make_event())
        self.assertFalse(result.success)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(len(transport.calls), 1)

    def test_404_not_found_is_not_retried(self):
        transport = FakeTransport([TransportResponse(status_code=404, body=b"not found")])
        service = TelegramNotificationService(
            bot_token="123:ABC", chat_id="456", transport=transport, sleep_fn=_no_sleep, max_attempts=3
        )
        result = service.send(_make_event())
        self.assertFalse(result.success)
        self.assertEqual(result.attempts, 1)


class TestSecretSafety(unittest.TestCase):
    def test_bot_token_not_present_in_result_on_failure(self):
        transport = FakeTransport([TransportResponse(status_code=401, body=b"unauthorized")])
        secret_token = "999999:SUPER-SECRET-SHOULD-NEVER-LEAK"
        service = TelegramNotificationService(
            bot_token=secret_token, chat_id="456", transport=transport, sleep_fn=_no_sleep
        )
        result = service.send(_make_event())
        self.assertNotIn(secret_token, repr(result))
        self.assertNotIn(secret_token, str(result.error))

    def test_bot_token_not_present_in_result_on_transport_exception(self):
        secret_token = "999999:SUPER-SECRET-SHOULD-NEVER-LEAK"
        transport = FakeTransport([RuntimeError(f"failed talking to {secret_token}")])
        service = TelegramNotificationService(
            bot_token=secret_token, chat_id="456", transport=transport, sleep_fn=_no_sleep, max_attempts=1
        )
        result = service.send(_make_event())
        self.assertNotIn(secret_token, str(result.error))


class TestNotificationFailureIsolation(unittest.TestCase):
    def test_send_never_raises_even_when_transport_always_raises(self):
        transport = FakeTransport([RuntimeError("boom"), RuntimeError("boom"), RuntimeError("boom")])
        service = TelegramNotificationService(
            bot_token="123:ABC", chat_id="456", transport=transport, sleep_fn=_no_sleep, max_attempts=3
        )
        try:
            result = service.send(_make_event())
        except Exception as exc:  # noqa: BLE001
            self.fail(f"send() must never raise, but raised {exc!r}")
        self.assertFalse(result.success)

    def test_send_never_raises_on_malformed_transport_response(self):
        transport = FakeTransport([TransportResponse(status_code=500, body=b"")])
        service = TelegramNotificationService(
            bot_token="123:ABC", chat_id="456", transport=transport, sleep_fn=_no_sleep, max_attempts=1
        )
        try:
            result = service.send(_make_event())
        except Exception as exc:  # noqa: BLE001
            self.fail(f"send() must never raise, but raised {exc!r}")
        self.assertFalse(result.success)


if __name__ == "__main__":
    unittest.main()
