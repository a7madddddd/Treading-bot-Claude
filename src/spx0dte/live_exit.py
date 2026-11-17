"""Incremental, tick-by-tick exit decision tracker for the SPX 0DTE
module (D-0095-A item 4: exit/close submission).

Mirrors `exit_logic.simulate_exit`'s exact decision rules and
priority order (target -> armed [breakeven/stall] -> pre-arm [stop_loss/
lagging_pace] -> time_cap) -- same constants
(TARGET_PCT/STOP_PCT/ARM_PCT/STALL_BARS/LAG_THRESHOLD imported from
that module, never re-guessed here), but applied ONE PRICE ESTIMATE AT
A TIME instead of a batch of real historical bars. This is necessary,
not a stylistic choice: a live position must be monitored using
`live_pricing`'s repriced estimates (Option B), because Alpaca's free
tier has no real-time options data at all (D-0095-A) -- there is no
batch of real "bars since entry" available while the trade is still
open.

Deliberately a SEPARATE implementation from `simulate_exit`, not a
refactor of it: `simulate_exit` is heavily used and already validated
by every backtest script in D-0095; duplicating this decision logic
is safer than restructuring an already-proven, already-tested
function to serve two calling shapes.

HONEST CAVEAT: a live position's `update()` calls happen roughly once
per tick (e.g. every 60s, driven by `live_loop.run_one_tick`'s own
`--tick-seconds`), each fed a `live_pricing` repriced ESTIMATE, not a
real traded price. This means the exits this tracker produces are not
necessarily what `simulate_exit`'s own backtested statistics describe
-- those were computed against REAL observed prices. This tracker
is the best approximation available under the free-tier's data
constraint, not a guarantee of matching backtest performance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from spx0dte.exit_logic import (
    ARM_PCT, LAG_THRESHOLD, MIN_BARS_BEFORE_LAG_CHECK, MIN_ELAPSED_MINUTES_BEFORE_LAG_CHECK,
    STALL_BARS, STOP_PCT, TARGET_PCT, ExitReason,
)


@dataclass
class LiveExitTracker:
    entry_price: float
    entry_time: datetime
    hold_cap_minutes: float
    target_pct: float = TARGET_PCT
    stop_pct: float = STOP_PCT
    arm_pct: float = ARM_PCT
    stall_bars: int = STALL_BARS
    lag_threshold: float = LAG_THRESHOLD
    min_ticks_before_lag_check: int = MIN_BARS_BEFORE_LAG_CHECK
    min_elapsed_minutes_before_lag_check: float = MIN_ELAPSED_MINUTES_BEFORE_LAG_CHECK

    running_max: float = field(init=False)
    bars_since_new_high: int = field(default=0, init=False)
    armed: bool = field(default=False, init=False)
    ticks_seen: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if self.entry_price <= 0:
            raise ValueError("entry_price must be positive")
        if self.hold_cap_minutes <= 0:
            raise ValueError("hold_cap_minutes must be positive")
        self.running_max = self.entry_price

    def update(self, price: float, now: datetime) -> Optional[ExitReason]:
        """Feed one new price estimate; returns the exit reason if
        this tick triggers an exit, else None. The caller is
        responsible for actually submitting the close order and for
        not calling update() again on a tracker that has already
        returned a reason -- this tracker does not enforce its own
        terminal state, mirroring a real position's lifecycle (closed
        means closed, not "keep polling")."""
        self.ticks_seen += 1
        if price > self.running_max:
            self.running_max = price
            self.bars_since_new_high = 0
        else:
            self.bars_since_new_high += 1

        if not self.armed and self.running_max >= self.entry_price * (1 + self.arm_pct):
            self.armed = True

        target_price = self.entry_price * (1 + self.target_pct)
        if price >= target_price:
            return "target"

        elapsed_minutes = (now - self.entry_time).total_seconds() / 60

        if self.armed:
            if price <= self.entry_price:
                return "breakeven_stop"
            if self.bars_since_new_high >= self.stall_bars:
                return "stall"
        else:
            pre_arm_stop_price = self.entry_price * (1 - self.stop_pct)
            if price <= pre_arm_stop_price:
                return "stop_loss"
            if (
                self.ticks_seen >= self.min_ticks_before_lag_check
                and elapsed_minutes >= self.min_elapsed_minutes_before_lag_check
            ):
                time_ratio = min(elapsed_minutes / self.hold_cap_minutes, 1.0)
                progress_ratio = max((self.running_max - self.entry_price) / self.entry_price, 0.0) / self.arm_pct
                if progress_ratio < self.lag_threshold * time_ratio:
                    return "lagging_pace"

        if elapsed_minutes >= self.hold_cap_minutes:
            return "time_cap"

        return None
