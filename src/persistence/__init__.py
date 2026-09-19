"""SQLite persistence infrastructure (D-0024). Connection/schema/
transaction plumbing only -- no Trade, Proposal, D-0007, Option E, or
Alpaca logic. Repositories (SqliteTradeRepository, SqliteProposalRepository
-- future work, not yet implemented) are the sole callers.
"""

from .db import (
    APPROVED_SCHEMA_VERSION,
    PersistenceConnectionError,
    PersistenceError,
    SchemaVersionError,
    bootstrap_schema,
    connect,
    get_schema_version,
    transaction,
)

__all__ = [
    "APPROVED_SCHEMA_VERSION",
    "PersistenceError",
    "PersistenceConnectionError",
    "SchemaVersionError",
    "connect",
    "bootstrap_schema",
    "get_schema_version",
    "transaction",
]
