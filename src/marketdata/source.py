"""MarketDataSource -- the minimal, broker-agnostic abstraction the
Engine depends on for current price (Controller-approved Engine design
review, this session).

Verified sufficient for every current consumer -- trigger detection,
D-0007 revalidation, Ladder 1/Ladder 2 execution, and Floor handling --
because each of these already takes a single `current_price: float`
(D-0012: Alpaca Last Trade) and none needs a bid/ask spread, historical
bars, or a full quote object. Deliberately NOT a larger market-data
subsystem, and deliberately kept independent of `BrokerClient` -- a
future swap of data source (or of broker) should never require
touching the other, mirroring how `BrokerClient` and
`INotificationService` are already kept as separate concerns in this
codebase despite Alpaca happening to be able to serve more than one of
them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class MarketDataError(RuntimeError):
    """Base class for MarketDataSource errors."""


class MarketDataUnavailableError(MarketDataError):
    """Raised by get_last_trade() when the current price could not be
    obtained (e.g. a network/API failure) -- callers must never
    fabricate or guess a price; this must propagate and be handled by
    the caller (e.g. skip this trade for this cycle, never treat a
    missing price as $0 or as "no trigger")."""


class MarketDataSource(ABC):
    @abstractmethod
    def get_last_trade(self, symbol: str) -> float:
        """Returns the latest valid trade price for `symbol` (D-0012:
        Alpaca Last Trade). Raises MarketDataUnavailableError if the
        price could not be obtained -- never returns a stale, cached,
        or fabricated value silently."""
        raise NotImplementedError
