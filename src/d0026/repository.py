"""Repository abstraction for ApprovedUniverseSnapshot persistence
(D-0024: SQLite behind a repository abstraction — this module defines
the abstraction only; no SQLite, or any other real storage backend, is
wired up here).

``save`` must refuse to overwrite an existing snapshot_id, enforcing
immutability at the persistence layer as well as at the dataclass layer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Dict, Optional

from .snapshot import ApprovedUniverseSnapshot


class SnapshotAlreadyExistsError(RuntimeError):
    """Raised when attempting to save a snapshot whose snapshot_id is
    already persisted. A snapshot is immutable once approved — a
    correction must be a new snapshot with its own id, never an
    overwrite."""


class SnapshotRepository(ABC):
    @abstractmethod
    def save(self, snapshot: ApprovedUniverseSnapshot) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_by_id(self, snapshot_id: str) -> Optional[ApprovedUniverseSnapshot]:
        raise NotImplementedError

    @abstractmethod
    def get_latest_for_date(
        self, effective_trading_date: date
    ) -> Optional[ApprovedUniverseSnapshot]:
        raise NotImplementedError


class InMemorySnapshotRepository(SnapshotRepository):
    """Test/scaffolding-only implementation. Holds snapshots in a plain
    dict for the lifetime of the process; touches no file, database, or
    network. Not intended, and not wired, for production use — D-0024's
    SQLite-backed implementation is a Phase B/E concern, not Phase A."""

    def __init__(self) -> None:
        self._by_id: Dict[str, ApprovedUniverseSnapshot] = {}

    def save(self, snapshot: ApprovedUniverseSnapshot) -> None:
        if snapshot.snapshot_id in self._by_id:
            existing = self._by_id[snapshot.snapshot_id]
            if existing == snapshot:
                # Re-saving byte-identical content is a no-op, not an
                # error — this is what determinism guarantees: the same
                # inputs replayed produce the same id and the same
                # content.
                return
            raise SnapshotAlreadyExistsError(
                f"snapshot_id {snapshot.snapshot_id!r} already exists with "
                "different content — a snapshot is immutable once approved"
            )
        self._by_id[snapshot.snapshot_id] = snapshot

    def get_by_id(self, snapshot_id: str) -> Optional[ApprovedUniverseSnapshot]:
        return self._by_id.get(snapshot_id)

    def get_latest_for_date(
        self, effective_trading_date: date
    ) -> Optional[ApprovedUniverseSnapshot]:
        candidates = [
            s
            for s in self._by_id.values()
            if s.effective_trading_date == effective_trading_date
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda s: s.snapshot_at)
