"""Generic, symbol-agnostic Trade state entity (Controller-approved
design, this session). Pure data + pure transition methods only --
no persistence, no Alpaca, no execution, no scheduler, no D-0011, no
Telegram. All explicitly deferred to a future phase.
"""

from .models import (
    TERMINAL_INITIAL_ORDER_STATUSES,
    InitialOrderStatus,
    Trade,
    TradeStateError,
    describe_status,
)

__all__ = [
    "Trade",
    "TradeStateError",
    "InitialOrderStatus",
    "TERMINAL_INITIAL_ORDER_STATUSES",
    "describe_status",
]
