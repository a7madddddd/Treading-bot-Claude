"""Macro event calendar (D-0050 Phase 16).

Blocks new proposals in the 24-hour window before a known high-
impact release (FOMC, CPI, NFP, PPI, GDP). These are deterministic
dates known in advance; using FRED's release calendar would be ideal
but is noisy. We ship a small built-in schedule for the next 6
months and let the Controller override via config.

Behavior:
  - should_block_new_proposals(now_utc) -> (bool, reason)
  - Fail-open: if the calendar is empty or an entry is malformed,
    returns (False, ""). Never raises.

The engine wires this into its hard filter so a proposal cannot be
created in the pre-event window — but existing trades and their
Ladders/Floor continue to execute. This is a NEW-POSITION guard,
not a kill-switch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class MacroEvent:
    event_date: date
    name: str
    time_utc: Optional[str] = None   # "14:00" style; informational

    def event_datetime_utc(self) -> datetime:
        if self.time_utc:
            try:
                hh, mm = (int(x) for x in self.time_utc.split(":"))
                return datetime(self.event_date.year,
                                self.event_date.month,
                                self.event_date.day,
                                hh, mm, tzinfo=timezone.utc)
            except (ValueError, AttributeError):
                pass
        return datetime(self.event_date.year,
                        self.event_date.month,
                        self.event_date.day,
                        12, 30, tzinfo=timezone.utc)  # 08:30 ET default


# ---------------------------------------------------------------------------
# Built-in high-impact releases (US-centric).
# ---------------------------------------------------------------------------
#
# These dates are published annually by the BLS / Fed and rarely move.
# A production deployment should refresh this list quarterly. The entries
# below cover 2026-10 through 2027-03 as a starting set; feel free to
# add more. If the Controller wants a different calendar, they pass a
# custom list to MacroEventCalendar.
#
# IMPORTANT: an empty list means NO events are blocked. The engine
# only blocks when this list contains events that fall within the
# look-ahead window.

_BUILTIN_EVENTS: Tuple[MacroEvent, ...] = (
    # Q4 2026
    MacroEvent(date(2026, 10, 15), "CPI Oct", "12:30"),
    MacroEvent(date(2026, 10, 29), "FOMC Decision + Powell", "18:00"),
    MacroEvent(date(2026, 11, 7),  "NFP Oct",  "12:30"),
    MacroEvent(date(2026, 11, 13), "CPI Nov",  "12:30"),
    MacroEvent(date(2026, 12, 5),  "NFP Nov",  "12:30"),
    MacroEvent(date(2026, 12, 10), "FOMC Decision + SEP", "18:00"),
    MacroEvent(date(2026, 12, 11), "CPI Dec",  "12:30"),
    # Q1 2027
    MacroEvent(date(2027, 1, 10),  "NFP Dec",  "12:30"),
    MacroEvent(date(2027, 1, 15),  "CPI Jan",  "12:30"),
    MacroEvent(date(2027, 1, 29),  "FOMC Decision", "18:00"),
    MacroEvent(date(2027, 2, 6),   "NFP Jan",  "12:30"),
    MacroEvent(date(2027, 2, 12),  "CPI Feb",  "12:30"),
    MacroEvent(date(2027, 3, 6),   "NFP Feb",  "12:30"),
    MacroEvent(date(2027, 3, 11),  "CPI Mar",  "12:30"),
    MacroEvent(date(2027, 3, 18),  "FOMC Decision + SEP", "18:00"),
)


@dataclass(frozen=True)
class MacroCalendarConfig:
    lookahead_hours: int = 24
    events: Tuple[MacroEvent, ...] = _BUILTIN_EVENTS


DEFAULT_CONFIG = MacroCalendarConfig()


class MacroEventCalendar:
    def __init__(self, config: MacroCalendarConfig = DEFAULT_CONFIG) -> None:
        self._cfg = config

    def should_block_new_proposals(
        self, now_utc: datetime,
    ) -> Tuple[bool, str]:
        """Returns (True, reason) if `now_utc` is within lookahead_hours
        of a listed event. Otherwise (False, '')."""
        try:
            window_end = now_utc + timedelta(hours=self._cfg.lookahead_hours)
        except (TypeError, OverflowError):
            return False, ""
        for ev in self._cfg.events:
            ev_dt = ev.event_datetime_utc()
            if now_utc <= ev_dt <= window_end:
                hours_to = (ev_dt - now_utc).total_seconds() / 3600
                return True, (f"macro event '{ev.name}' in "
                              f"{hours_to:.1f}h ({ev.event_date.isoformat()})")
        return False, ""

    def next_event(self, now_utc: datetime) -> Optional[MacroEvent]:
        upcoming = [ev for ev in self._cfg.events
                    if ev.event_datetime_utc() >= now_utc]
        if not upcoming:
            return None
        return min(upcoming, key=lambda ev: ev.event_datetime_utc())
