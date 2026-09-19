"""CandidateSource -- the narrow interface `proposal.py` consumes.

Mirrors `src/d0026/provider.py`'s `UniverseSourceProvider` pattern
deliberately: a small ABC plus one concrete, explicitly-scoped
implementation, so that replacing this fixed watchlist with a real
`ApprovedUniverseSnapshot` reader later is a single-component swap that
never touches proposal generation, the approval state machine, or
revalidation.

`FixedWatchlistCandidateSource` is Phase A MVP infrastructure, NOT
Universe Search. It performs no filtering, ranking, scoring, regime
detection, or liquidity qualification of any kind -- it returns exactly
the symbols it was configured with.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Sequence, Tuple


class CandidateSource(ABC):
    """Given a date, return today's candidate symbols. Implementations
    decide nothing about tradability, ranking, or strategy fit -- that
    is out of scope for this interface in Phase A."""

    @property
    @abstractmethod
    def source_label(self) -> str:
        """A short, honest label identifying what kind of source this
        is (e.g. 'fixed_watchlist'). Consumers must use this label
        verbatim in any proposal they generate -- never a claim of
        'Universe Search' or similar."""
        raise NotImplementedError

    @abstractmethod
    def get_candidate_symbols(self, as_of_date: date) -> Tuple[str, ...]:
        raise NotImplementedError


class FixedWatchlistCandidateSource(CandidateSource):
    """Returns exactly the Controller-specified symbols, every day,
    with no filtering or ranking. This is temporary MVP infrastructure
    -- see `docs/trading/decisions.md` for the decision recording its
    temporary status once logged."""

    def __init__(self, symbols: Sequence[str]) -> None:
        if not symbols:
            raise ValueError(
                "FixedWatchlistCandidateSource requires at least one "
                "Controller-specified symbol -- it must never invent one"
            )
        normalized = tuple(s.strip().upper() for s in symbols)
        if any(not s for s in normalized):
            raise ValueError("watchlist symbols must not be empty/whitespace")
        self._symbols = normalized

    @property
    def source_label(self) -> str:
        return "fixed_watchlist"

    def get_candidate_symbols(self, as_of_date: date) -> Tuple[str, ...]:
        return self._symbols
