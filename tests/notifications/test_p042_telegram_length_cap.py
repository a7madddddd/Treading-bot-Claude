"""P-042: Telegram rejects any sendMessage text over 4096 characters
with HTTP 400. Before this cap, a proposal message enriched by five
research sources could exceed it -- and the rejected message is the one
carrying the inline approve/reject buttons, so the Controller would
never see the trade at all.

The cap lives in the transport because the limit is the transport's. An
enricher added later must not have to know about it to be safe.
"""

import unittest
from datetime import datetime, timezone

from notifications.service import NotificationEvent, NotificationLevel
from notifications.telegram import TelegramNotificationService as T


def _event(message: str) -> NotificationEvent:
    return NotificationEvent(
        level=NotificationLevel.IMPORTANT,
        event="proposal_awaiting_approval",
        message=message,
        symbol="LOW",
        extra=(),
    )


class TestFit(unittest.TestCase):
    def test_short_text_is_untouched(self):
        self.assertEqual(T._fit("hello"), "hello")

    def test_text_exactly_at_the_limit_is_untouched(self):
        exact = "x" * T.TELEGRAM_MAX_TEXT_CHARS
        self.assertEqual(T._fit(exact), exact)

    def test_text_one_over_the_limit_is_truncated(self):
        over = "x" * (T.TELEGRAM_MAX_TEXT_CHARS + 1)
        self.assertLessEqual(len(T._fit(over)), T.TELEGRAM_MAX_TEXT_CHARS)

    def test_a_very_long_body_still_fits(self):
        self.assertLessEqual(len(T._fit("x" * 100_000)),
                             T.TELEGRAM_MAX_TEXT_CHARS)

    def test_truncation_is_visible_not_silent(self):
        out = T._fit("x" * 10_000)
        self.assertIn("shortened", out)

    def test_the_head_is_kept_not_the_tail(self):
        # The decision-critical part (symbol, prices, quantity,
        # safeguards) is at the top; the advisory research block is at
        # the bottom. Cutting the tail loses only the optional part.
        body = "DECISION-CRITICAL-HEAD" + ("y" * 10_000) + "RESEARCH-TAIL"
        out = T._fit(body)
        self.assertTrue(out.startswith("DECISION-CRITICAL-HEAD"))
        self.assertNotIn("RESEARCH-TAIL", out)


class TestFormattedPayloadRespectsTheLimit(unittest.TestCase):
    def test_a_huge_message_produces_a_sendable_payload(self):
        svc = T(bot_token="t", chat_id="c", transport=lambda *a, **k: None)
        text = svc._format_text(_event("z" * 50_000))
        self.assertLessEqual(len(text), T.TELEGRAM_MAX_TEXT_CHARS)

    def test_the_head_of_the_body_survives_truncation(self):
        # D-0071 drops the machine header on a self-describing event, so
        # the first line of the payload is the body's own first line --
        # which is where the symbol and the decision numbers live.
        svc = T(bot_token="t", chat_id="c", transport=lambda *a, **k: None)
        text = svc._format_text(_event("LOW — Buy\n" + "z" * 50_000))
        self.assertTrue(text.startswith("LOW — Buy"))

    def test_an_operational_event_keeps_its_header(self):
        # Only self-describing events lose the header; a technical
        # event still needs to say what it is.
        svc = T(bot_token="t", chat_id="c", transport=lambda *a, **k: None)
        ev = NotificationEvent(
            level=NotificationLevel.CRITICAL, event="floor_executed_full",
            message="sold", symbol="LOW", extra=())
        text = svc._format_text(ev)
        self.assertIn("floor_executed_full", text)
        self.assertIn("Symbol: LOW", text)

    def test_a_normal_message_is_not_altered(self):
        svc = T(bot_token="t", chat_id="c", transport=lambda *a, **k: None)
        text = svc._format_text(_event("Buy price: $100.00"))
        self.assertIn("Buy price: $100.00", text)
        self.assertNotIn("shortened", text)


if __name__ == "__main__":
    unittest.main()
