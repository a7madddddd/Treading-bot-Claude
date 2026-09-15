"""UniverseSourceProvider — the interface boundary that makes the raw
candidate source swappable without touching the pipeline stages, the
snapshot format, or the Strategy Engine.

**No concrete implementation in this module is connected to any real
data source.** Per the 2026-09-15 implementation-plan discussion §D: the
verified free point-in-time sources (e.g. arielNacamulli/pitindex) are
validation/control inputs only and must never be wired in as this
interface's production implementation — doing so would silently narrow
D-0026 to an index-scoped strategy, which every prior design document in
this project has explicitly rejected.

The only implementation provided here is ``InMemoryUniverseSourceProvider``,
which serves synthetic, caller-supplied candidates for tests only.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Mapping, Sequence, Tuple

from .models import RawCandidateRef


class UniverseSourceProvider(ABC):
    """Given a date, return the raw candidate pool eligible for
    evaluation that day. Implementations decide nothing about
    tradability, data quality, ranking, or any other pipeline concern —
    those are the pipeline stages' job, not the provider's."""

    @abstractmethod
    def get_raw_candidates(self, as_of_date: date) -> Tuple[RawCandidateRef, ...]:
        raise NotImplementedError


class InMemoryUniverseSourceProvider(UniverseSourceProvider):
    """Test/scaffolding-only provider backed by a caller-supplied,
    in-memory mapping of date -> tickers. Never fetches from, or is
    configured to point at, any real data source, network endpoint, or
    file."""

    def __init__(self, candidates_by_date: Mapping[date, Sequence[str]]) -> None:
        self._candidates_by_date = {
            d: tuple(tickers) for d, tickers in candidates_by_date.items()
        }

    def get_raw_candidates(self, as_of_date: date) -> Tuple[RawCandidateRef, ...]:
        tickers = self._candidates_by_date.get(as_of_date, ())
        return tuple(RawCandidateRef(ticker=t, as_of_date=as_of_date) for t in tickers)
