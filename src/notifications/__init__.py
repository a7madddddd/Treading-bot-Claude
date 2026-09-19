"""Notification abstraction and providers.

Per `docs/architecture/overview.md` §6: the trading/system side depends
only on `INotificationService`, never on a concrete provider. Telegram is
the first, and currently only, provider. This package has zero import-time
dependency on `src/d0026/` or any trading/execution logic — it is a pure
observer, never a source of trading authority (CLAUDE.md §5, §6;
`docs/trading/execution.md`).
"""

from .service import (
    INotificationService,
    NotificationEvent,
    NotificationLevel,
    NotificationResult,
)
from .telegram import TelegramConfigError, TelegramNotificationService

__all__ = [
    "INotificationService",
    "NotificationEvent",
    "NotificationLevel",
    "NotificationResult",
    "TelegramConfigError",
    "TelegramNotificationService",
]
