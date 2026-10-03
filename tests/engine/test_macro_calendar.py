"""Tests for MacroEventCalendar (D-0050 Phase 16)."""

import unittest
from datetime import date, datetime, timezone, timedelta
from engine.macro_calendar import (
    MacroEvent, MacroCalendarConfig, MacroEventCalendar,
)


class TestMacroCalendar(unittest.TestCase):
    def test_block_fires_in_window(self):
        ev = MacroEvent(date(2026, 10, 15), "CPI", "12:30")
        cal = MacroEventCalendar(MacroCalendarConfig(
            lookahead_hours=24, events=(ev,)))
        now = datetime(2026, 10, 14, 20, 0, tzinfo=timezone.utc)
        block, reason = cal.should_block_new_proposals(now)
        self.assertTrue(block)
        self.assertIn("CPI", reason)

    def test_no_block_outside_window(self):
        ev = MacroEvent(date(2026, 10, 15), "CPI", "12:30")
        cal = MacroEventCalendar(MacroCalendarConfig(
            lookahead_hours=24, events=(ev,)))
        now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
        block, reason = cal.should_block_new_proposals(now)
        self.assertFalse(block)
        self.assertEqual(reason, "")

    def test_empty_calendar_never_blocks(self):
        cal = MacroEventCalendar(MacroCalendarConfig(events=()))
        now = datetime(2026, 10, 15, tzinfo=timezone.utc)
        block, _ = cal.should_block_new_proposals(now)
        self.assertFalse(block)

    def test_next_event(self):
        evs = (
            MacroEvent(date(2026, 10, 15), "CPI"),
            MacroEvent(date(2026, 10, 29), "FOMC"),
            MacroEvent(date(2026, 11, 7),  "NFP"),
        )
        cal = MacroEventCalendar(MacroCalendarConfig(events=evs))
        now = datetime(2026, 10, 20, tzinfo=timezone.utc)
        nxt = cal.next_event(now)
        self.assertIsNotNone(nxt)
        self.assertEqual(nxt.name, "FOMC")

    def test_malformed_time_safe_default(self):
        ev = MacroEvent(date(2026, 10, 15), "X", "bad-time")
        self.assertIsInstance(ev.event_datetime_utc(), datetime)

    def test_builtin_calendar_has_events(self):
        from engine.macro_calendar import _BUILTIN_EVENTS
        self.assertGreater(len(_BUILTIN_EVENTS), 0)
        for ev in _BUILTIN_EVENTS:
            self.assertIsInstance(ev.event_date, date)
            self.assertTrue(ev.name)


if __name__ == "__main__":
    unittest.main()
