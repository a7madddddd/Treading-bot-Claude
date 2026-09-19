"""TradeRepository -- persistence abstraction for Trade.

Mirrors `src/proposals/repository.py`'s `ProposalRepository` pattern:
an ABC plus a concrete implementation (`SqliteTradeRepository`, in
`sqlite_repository.py`). No business logic lives here -- `Trade`'s own
transition methods (freeze/ladder-fill/trailing/etc.) already own every
invariant and every strategy calculation; this module only defines the
persistence contract for storing and retrieving the `Trade` objects a
caller produces via those methods.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from .models import Trade


class TradeRepositoryError(RuntimeError):
    """Base class for TradeRepository errors."""


class TradeAlreadyExistsError(TradeRepositoryError):
    """Raised by save() when trade_id already exists. save() creates a
    NEW trade only -- an existing trade must go through update()."""


class TradeRevisionConflictError(TradeRepositoryError):
    """Raised by update() when the stored revision no longer matches
    `expected_revision` -- someone else updated this trade since the
    caller loaded it. On this error: NOTHING is written -- no change
    to the current-state row, no trade_snapshots row, no
    protective_order_history row. The caller must reload the current
    TradeRecord and decide how to proceed; that policy is not this
    repository's concern."""


@dataclass(frozen=True)
class TradeRecord:
    """A `Trade` together with its persistence revision. `Trade` itself
    carries no `revision` field (it is persistence-only bookkeeping,
    not a domain concept) -- this wrapper is how every read/write
    method hands the caller the revision needed for a subsequent
    `update()` call, without any hidden state inside the repository."""

    trade: Trade
    revision: int


class TradeRepository(ABC):
    @abstractmethod
    def save(self, trade: Trade, *, now: datetime) -> TradeRecord:
        """Persists a NEW trade. Raises TradeAlreadyExistsError if
        trade_id already exists. Always creates at revision 0."""
        raise NotImplementedError

    @abstractmethod
    def update(
        self, trade: Trade, *, expected_revision: int, transition: str, now: datetime
    ) -> TradeRecord:
        """The ONLY supported way to persist a change to an existing
        trade -- `trade` must be the NEW Trade instance returned by one
        of Trade's own transition methods (freeze_initial_reference,
        record_ladder_fill, activate_trailing, ratchet_trailing,
        reconcile_position, update_protective_order,
        record_reconciliation). Raises TradeRevisionConflictError if
        `expected_revision` no longer matches the stored revision.
        `transition` is a caller-supplied audit label for the
        trade_snapshots history row -- this repository never infers or
        interprets it."""
        raise NotImplementedError

    @abstractmethod
    def get(self, trade_id: str) -> Optional[TradeRecord]:
        raise NotImplementedError

    @abstractmethod
    def list_active(self) -> List[TradeRecord]:
        """Every trade whose derived status (describe_status()) is
        AWAITING_INITIAL_FILL or ACTIVE -- i.e. not yet terminal. Used
        for restart recovery."""
        raise NotImplementedError

    @abstractmethod
    def list_for_symbol(self, symbol: str) -> List[TradeRecord]:
        raise NotImplementedError
