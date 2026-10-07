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
    # Optional inline buttons. Each tuple = (visible_label, callback_data).
    # Callback data must match `_parse_callback_data` in
    # `telegram_decision.py`: "<action>:<proposal_id>" where action is
    # one of approve/reject/confirm_l2.
    interactive_actions: Tuple[Tuple[str, str], ...] = field(default_factory=tuple)

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


class LoggingNotificationService(INotificationService):
    """Writes every notification to the session log, then delegates.

    D-0085. Until now a notification existed ONLY in Telegram. Nothing
    reached disk, with two consequences that both bit on 2026-10-07:

      - an outage could not be analysed after the fact. Investigating
        that morning's HTTP 429 alerts meant asking the Controller to
        paste the messages back, because `grep 429 logs/engine.log`
        returned nothing -- not because the 429s had not happened, but
        because no notification is ever written there.
      - if Telegram is unreachable, a CRITICAL event is lost outright
        and nobody ever learns it occurred. `send()` is contractually
        forbidden from raising, so the failure is silent by design.

    The line is written BEFORE delegating, so a crash inside the
    transport still leaves the trace. Writing never raises: a logging
    failure must not become a trading input any more than a delivery
    failure may (execution.md, CLAUDE.md §6).

    This is a decorator, not an edit to the Telegram service, so it
    composes with any provider and with the existing retry wrapper --
    the same shape as `common.http_retry.with_retry`.
    """

    def __init__(self, inner: "INotificationService", *, write=None) -> None:
        self._inner = inner
        if write is not None:
            self._write = write
        else:
            def _default(line: str) -> None:
                print(line, flush=True)
            self._write = _default

    def send(self, event: NotificationEvent) -> NotificationResult:
        try:
            self._write(self.format(event))
        except Exception:  # noqa: BLE001 - never let logging break delivery
            pass
        return self._inner.send(event)

    @staticmethod
    def format(event: NotificationEvent) -> str:
        """One event, one line. Newlines inside the message become ' | '
        so a multi-line alert stays greppable."""
        stamp = event.effective_timestamp().isoformat()
        body = " | ".join(
            part.strip() for part in str(event.message).splitlines()
            if part.strip()
        )
        symbol = f" symbol={event.symbol}" if event.symbol else ""
        return (f"[notify] {stamp} {event.level.value} "
                f"event={event.event}{symbol} :: {body}")
