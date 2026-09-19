"""WatchlistSource -- the minimal interface the Engine depends on for
which symbols to monitor (Controller-approved: "Universe/Watchlist
drives the Engine... must consume the approved Universe/Watchlist
boundary and must not invent its own symbol-selection logic").

The Engine never selects, ranks, or filters symbols itself -- it only
asks "what should I be watching right now" and reacts. Eligibility is
always someone else's decision: the real Universe subsystem
(`src/d0026/`, still BLOCKED per pre-apply-checklist B15/B16) or, for
this phase, the Controller directly via a static, explicitly-documented
list (see `decisions.md`).

`ApprovedUniverseSnapshot` (`src/d0026/`) remains the only object a
future Strategy Engine may ever depend on from that subsystem
(docs/architecture/universe.md §1) -- but building one correctly
requires its internal Evidence/Confidence Layer machinery (identity
mapping, evidence classification, etc.), explicitly internal to that
subsystem and disproportionate to what a static, Controller-approved
test list needs. `StaticWatchlistSource` therefore does NOT construct a
real `ApprovedUniverseSnapshot` -- it is an honest, minimal, static
provider behind the SAME `WatchlistSource` interface a future
snapshot-backed provider would also implement, so swapping the concrete
provider later requires zero Engine change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Tuple


class WatchlistSource(ABC):
    @abstractmethod
    def get_active_symbols(self) -> Tuple[str, ...]:
        """Returns every symbol the Engine should currently be
        monitoring. Never raises for an empty result -- an empty tuple
        is a valid, normal answer (nothing to watch right now)."""
        raise NotImplementedError


class StaticWatchlistSource(WatchlistSource):
    """A fixed, Controller-approved list of symbols, explicitly
    documented as such (see `docs/trading/decisions.md`) -- never a
    silently-invented default. Used for this phase while the real
    Universe/D-0026 pipeline remains blocked; TSLA is TEST-ONLY
    (D-0026 §5)."""

    def __init__(self, symbols: Tuple[str, ...]) -> None:
        self._symbols = tuple(s.strip().upper() for s in symbols)

    def get_active_symbols(self) -> Tuple[str, ...]:
        return self._symbols
