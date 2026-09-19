"""INotificationService — the abstraction trading/system code depends on.

Mirrors this project's existing dormant-adapter pattern (`d0026/provider.py`'s
`UniverseSourceProvider`, `d0026/ranking.py`'s registry): a small ABC
interface plus a real, tested, concrete implementation, kept out of any
live execution path until separately wired in.

`send()` must never raise. A failed or slow notification is never allowed
to become a trading decision input or to affect trading execution
(`docs/trading/execution.md`, CLAUDE.md §6) — callers get a
`NotificationResult` back and decide for themselves whether/how to log it;
they never need to catch an exception from this interface to stay safe.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Tuple


class NotificationLevel(Enum):
    """Matches `docs/architecture/overview.md` §6's three-tier model."""

    CRITICAL = "CRITICAL"
    IMPORTANT = "IMPORTANT"
    OPTIONAL = "OPTIONAL"


@dataclass(frozen=True)
class NotificationEvent:
    """One notification to deliver. Carries no trading authority — this
    is presentation/audit data only, the same discipline already used for
    `score_summary` in `d0026/models.py` and the evidence-package fields
    in `d0026/evidence.py`."""

    level: NotificationLevel
    event: str
    message: str
    symbol: Optional[str] = None
    timestamp: Optional[datetime] = None
    extra: Tuple[Tuple[str, str], ...] = field(default_factory=tuple)

    def effective_timestamp(self) -> datetime:
        return self.timestamp if self.timestamp is not None else datetime.now(timezone.utc)


@dataclass(frozen=True)
class NotificationResult:
    """Outcome of one `send()` call. `success=False` must never be
    escalated by a caller into retrying a trade or blocking execution —
    see `docs/trading/execution.md`."""

    success: bool
    attempts: int
    status_code: Optional[int] = None
    error: Optional[str] = None


class INotificationService(ABC):
    """The only thing trading/system code should depend on. A concrete
    provider (e.g. `TelegramNotificationService`) is an implementation
    detail behind this interface."""

    @abstractmethod
    def send(self, event: NotificationEvent) -> NotificationResult:
        raise NotImplementedError
