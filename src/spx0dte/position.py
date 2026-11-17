"""Open-position model for the SPX 0DTE module (D-0095-A item 4: exit/
close submission). Mirrors `proposal.OptionTradeProposal`'s own
immutable-transition shape (a position only ever moves open ->
closed once, via `with_exit`, which returns a NEW object) because
that shape is sound here too, not because this module depends on
proposal.py.

Created once an APPROVED proposal's entry order is submitted
(`orchestration.process_decisions`), and closed once
`live_exit.LiveExitTracker` signals an exit trigger and
`execution.submit_exit_order` has been called
(`live_loop.monitor_open_positions`).
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class OpenPosition:
    proposal_id: str
    occ_symbol: str
    direction: str
    quantity: int
    strike: int
    date: str
    entry_time: datetime
    entry_price: float
    hold_cap_minutes: float

    is_closed: bool = False
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.proposal_id:
            raise ValueError("proposal_id must be non-empty")
        if self.direction not in ("call", "put"):
            raise ValueError(f"direction must be 'call' or 'put', got {self.direction!r}")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.entry_price <= 0:
            raise ValueError("entry_price must be positive")

        if self.is_closed:
            if self.exit_time is None or self.exit_price is None or self.exit_reason is None:
                raise ValueError("a closed position must carry exit_time, exit_price, and exit_reason")
        else:
            if self.exit_time is not None or self.exit_price is not None or self.exit_reason is not None:
                raise ValueError("an open position must not carry any exit field")

    def with_exit(self, *, exit_time: datetime, exit_price: float, exit_reason: str) -> "OpenPosition":
        if self.is_closed:
            raise ValueError(f"position {self.proposal_id!r} is already closed -- may only close once")
        return dataclasses.replace(
            self, is_closed=True, exit_time=exit_time, exit_price=exit_price, exit_reason=exit_reason,
        )
