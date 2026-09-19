import unittest
from datetime import datetime, timezone

from notifications.service import (
    INotificationService,
    NotificationEvent,
    NotificationLevel,
    NotificationResult,
)


class TestNotificationEvent(unittest.TestCase):
    def test_effective_timestamp_uses_supplied_value(self):
        ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
        event = NotificationEvent(
            level=NotificationLevel.CRITICAL, event="ORDER_FILLED", message="filled", timestamp=ts
        )
        self.assertEqual(event.effective_timestamp(), ts)

    def test_effective_timestamp_defaults_to_now_when_absent(self):
        event = NotificationEvent(level=NotificationLevel.OPTIONAL, event="X", message="y")
        before = datetime.now(timezone.utc)
        ts = event.effective_timestamp()
        after = datetime.now(timezone.utc)
        self.assertLessEqual(before, ts)
        self.assertLessEqual(ts, after)

    def test_extra_defaults_to_empty_tuple(self):
        event = NotificationEvent(level=NotificationLevel.IMPORTANT, event="X", message="y")
        self.assertEqual(event.extra, ())

    def test_event_is_frozen(self):
        event = NotificationEvent(level=NotificationLevel.IMPORTANT, event="X", message="y")
        with self.assertRaises(Exception):
            event.message = "changed"  # type: ignore[misc]


class TestNotificationResult(unittest.TestCase):
    def test_is_frozen(self):
        result = NotificationResult(success=True, attempts=1)
        with self.assertRaises(Exception):
            result.success = False  # type: ignore[misc]


class TestINotificationServiceContract(unittest.TestCase):
    def test_cannot_instantiate_abstract_class(self):
        with self.assertRaises(TypeError):
            INotificationService()  # type: ignore[abstract]

    def test_concrete_subclass_must_implement_send(self):
        class Incomplete(INotificationService):
            pass

        with self.assertRaises(TypeError):
            Incomplete()  # type: ignore[abstract]

        class Complete(INotificationService):
            def send(self, event: NotificationEvent) -> NotificationResult:
                return NotificationResult(success=True, attempts=1)

        instance = Complete()
        result = instance.send(NotificationEvent(level=NotificationLevel.OPTIONAL, event="X", message="y"))
        self.assertTrue(result.success)


if __name__ == "__main__":
    unittest.main()
