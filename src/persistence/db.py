"""SQLite connection/schema-bootstrap/transaction plumbing (D-0024:
SQLite behind a repository abstraction).

STRICTLY infrastructure only -- no domain, Trade, Proposal, D-0007,
Option E, or Alpaca logic of any kind lives here or may ever be added
here. Repositories (not yet implemented -- future work) are the only
callers of this module.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, Optional

APPROVED_SCHEMA_VERSION = 4
"""The explicit, Controller-approved live-authorization boundary --
the ONLY thing that makes a migration eligible to run. A migration
file numbered above this constant may exist in the migrations
directory (e.g. drafted/reviewed ahead of its own approval step) but
is never inspected or applied by bootstrap_schema() -- see
_discover_approved_migrations()'s docstring. Bumping this constant is
itself the auditable artifact of Controller approval for a schema
change, exactly mirroring every other explicit approval gate in this
project (docs/trading/decisions.md entries, commit/push authorization,
etc.) -- never bumped as a side effect of merely adding a file."""

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_MIGRATION_FILENAME_RE = re.compile(r"^(\d{4})_.+\.sql$")
"""The project's migration naming convention: NNNN_description.sql,
a zero-padded 4-digit version prefix. Parsed as an int, never compared
as a string (avoids lexicographic-vs-numeric ordering bugs, e.g.
'0010' sorting before '0002')."""


class PersistenceError(RuntimeError):
    """Base class for this module's errors."""


class PersistenceConnectionError(PersistenceError):
    """Raised when a SQLite connection cannot be opened or configured
    with the approved settings. Wraps the underlying sqlite3.Error with
    a clear message rather than letting it propagate raw."""


class SchemaVersionError(PersistenceError):
    """Raised when a database's PRAGMA user_version is greater than
    APPROVED_SCHEMA_VERSION -- code is older than the database. Per
    Controller governance: an unrecognized/newer schema version must
    BLOCK, never be silently auto-migrated or guessed at."""


class MigrationError(PersistenceError):
    """Raised when the APPROVED range (1..APPROVED_SCHEMA_VERSION) of
    migration files is invalid -- a gap, a duplicate version, or
    APPROVED_SCHEMA_VERSION itself naming a migration file that does
    not exist. Deliberately scoped to the approved range only: a
    malformed or gap-creating migration file numbered ABOVE
    APPROVED_SCHEMA_VERSION never raises this from bootstrap_schema()
    -- it has no live effect and must never be able to prevent the
    currently-approved schema from booting. Full-directory hygiene
    (including future/unapproved files) is a separate, deliberately
    non-runtime check -- see validate_migration_directory()."""


def connect(db_path: str) -> sqlite3.Connection:
    """Opens a SQLite connection with the approved settings enforced:

    - isolation_level=None (autocommit): callers control every
      transaction boundary explicitly via `transaction()` below
      (BEGIN IMMEDIATE ... COMMIT/ROLLBACK) -- never sqlite3's own
      implicit transaction handling, which is required for
      BEGIN IMMEDIATE's write-lock-up-front guarantee to actually hold.
    - foreign_keys=ON: SQLite disables FK enforcement by default; the
      approved schema (proposals/trade_snapshots/protective_order_history
      all reference trades.trade_id) relies on real enforcement.

    Raises PersistenceConnectionError (never a raw sqlite3.Error) on
    failure -- invalid path, permission error, etc."""

    try:
        conn = sqlite3.connect(db_path, isolation_level=None)
    except sqlite3.Error as exc:
        raise PersistenceConnectionError(f"failed to open SQLite database at {db_path!r}: {exc}") from exc

    try:
        conn.execute("PRAGMA foreign_keys = ON")
    except sqlite3.Error as exc:
        conn.close()
        raise PersistenceConnectionError(f"failed to configure connection to {db_path!r}: {exc}") from exc

    return conn


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Reads PRAGMA user_version -- 0 for a never-bootstrapped
    database, or whatever version bootstrap_schema() last set."""
    row = conn.execute("PRAGMA user_version").fetchone()
    return row[0]


def _parse_migration_version(filename: str) -> Optional[int]:
    match = _MIGRATION_FILENAME_RE.match(filename)
    return int(match.group(1)) if match else None


def _discover_approved_migrations(
    approved_version: int, migrations_dir: Path
) -> Dict[int, Path]:
    """Scans `migrations_dir` and returns {version: path} for every
    migration whose parsed version is in 1..approved_version --
    INCLUSIVE of the boundary, and nothing beyond it. A file whose name
    doesn't match the NNNN_description.sql convention is silently
    ignored here (it is simply not a candidate for the approved range;
    runtime bootstrap has no opinion about non-migration files sitting
    in the directory) -- ONLY files numbered within the approved range
    are ever opened/read by this function or by _apply_migration().

    Raises MigrationError if the approved range itself is broken: a
    duplicate version within 1..approved_version, or any version in
    that range with no corresponding file (this covers BOTH a genuine
    gap and APPROVED_SCHEMA_VERSION naming a migration that doesn't
    exist -- both are "a version in the approved range has no file")."""

    found: Dict[int, Path] = {}
    for path in sorted(migrations_dir.iterdir()):
        if not path.is_file():
            continue
        version = _parse_migration_version(path.name)
        if version is None or version > approved_version:
            continue
        if version in found:
            raise MigrationError(
                f"duplicate migration version {version} within the approved range "
                f"(1..{approved_version}): {found[version].name!r} and {path.name!r}"
            )
        found[version] = path

    missing = [v for v in range(1, approved_version + 1) if v not in found]
    if missing:
        raise MigrationError(
            f"migration version(s) {missing} missing from the approved range "
            f"(1..{approved_version}) -- refusing to bootstrap with an incomplete "
            "or gapped approved schema"
        )

    return found


def validate_migration_directory(migrations_dir: Optional[Path] = None) -> None:
    """Full-directory migration hygiene check -- inspects EVERY
    NNNN_description.sql file present in `migrations_dir` (defaulting
    to the real project migrations directory), approved or not, for
    valid filename/version parsing, uniqueness, and contiguity from
    version 1 upward. Raises MigrationError on any violation.

    Deliberately NEVER called by bootstrap_schema() or any other
    runtime path -- a malformed, gap-creating, or duplicate FUTURE
    (not-yet-approved) migration file must never prevent the
    currently-approved schema from booting (see MigrationError's
    docstring, and _discover_approved_migrations() above, which is the
    only migration discovery bootstrap_schema() itself ever calls).
    This function exists so that authoring mistake is still caught --
    immediately, via the test suite -- without threatening any live,
    already-approved deployment. Intended to be called only from
    tests."""

    directory = migrations_dir if migrations_dir is not None else _MIGRATIONS_DIR

    versions: Dict[int, Path] = {}
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix != ".sql":
            continue
        version = _parse_migration_version(path.name)
        if version is None:
            raise MigrationError(
                f"migration file {path.name!r} does not match the required "
                "NNNN_description.sql naming convention"
            )
        if version in versions:
            raise MigrationError(
                f"duplicate migration version {version}: "
                f"{versions[version].name!r} and {path.name!r}"
            )
        versions[version] = path

    if not versions:
        return

    missing = [v for v in range(1, max(versions) + 1) if v not in versions]
    if missing:
        raise MigrationError(
            f"migration version(s) {missing} missing -- versions must be contiguous "
            f"starting at 1 (highest version present: {max(versions)})"
        )


def _apply_migration(conn: sqlite3.Connection, version: int, path: Path) -> None:
    """Applies exactly one migration file inside its own BEGIN
    IMMEDIATE transaction, DDL and the PRAGMA user_version advance
    atomic together. Re-reads the schema version AFTER the write lock
    is acquired (inside the transaction) -- if a racing connection
    already committed this version (or a later one) while this call
    was waiting for the lock, this call no-ops rather than re-running
    DDL against a database that already has it."""

    with transaction(conn) as tconn:
        actual_current = get_schema_version(tconn)
        if actual_current >= version:
            return
        if actual_current != version - 1:
            raise SchemaVersionError(
                f"cannot apply migration {version}: database is at version "
                f"{actual_current}, expected {version - 1} -- migrations must be "
                "applied strictly in order"
            )

        migration_sql = path.read_text()
        # Deliberately NOT using conn.executescript(): it issues an implicit
        # commit of its own before running, which defeats wrapping the whole
        # migration in one atomic BEGIN IMMEDIATE ... COMMIT. Statements are
        # executed individually instead, inside this single explicit
        # transaction (the same discipline 0001_initial.sql was always
        # applied with).
        statements = [s.strip() for s in migration_sql.split(";") if s.strip()]
        for statement in statements:
            tconn.execute(statement)
        tconn.execute(f"PRAGMA user_version = {version}")


def bootstrap_schema(
    conn: sqlite3.Connection,
    *,
    approved_version: int = APPROVED_SCHEMA_VERSION,
    migrations_dir: Optional[Path] = None,
) -> None:
    """Idempotent and safe to call on every startup:

    - version == approved_version: already bootstrapped -- applies
      NOTHING (the approved range is still re-validated on every call,
      cheap and side-effect-free against the filesystem, but no SQL is
      executed against the database).
    - version < approved_version: applies every migration strictly
      greater than the current version and up to approved_version, in
      order, one per transaction (see _apply_migration()).
    - version > approved_version: fails closed. Raises
      SchemaVersionError -- this code is older than the database and
      must never guess or proceed.

    Only migration files numbered 1..approved_version are ever
    discovered, read, or applied -- see
    _discover_approved_migrations()'s docstring. `approved_version`/
    `migrations_dir` default to the real project constant/directory;
    tests may override either to exercise multi-migration upgrades
    without touching the real migrations directory."""

    directory = migrations_dir if migrations_dir is not None else _MIGRATIONS_DIR
    approved_migrations = _discover_approved_migrations(approved_version, directory)

    current = get_schema_version(conn)
    if current > approved_version:
        raise SchemaVersionError(
            f"database schema is at version {current}, which is newer than this "
            f"code's approved version {approved_version} -- refusing to proceed"
        )

    for version in range(current + 1, approved_version + 1):
        _apply_migration(conn, version, approved_migrations[version])


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE ... COMMIT/ROLLBACK. Required for every
    read-then-conditionally-write repository operation (e.g. the
    proposal Option E save gate: fetch siblings, decide, write).

    BEGIN IMMEDIATE acquires SQLite's write lock immediately, before
    the caller's own SELECT runs inside the block -- a second
    connection's own `transaction()` call blocks until this one
    commits or rolls back, so it always observes this transaction's
    already-committed result rather than racing against a stale read.
    Plain/deferred BEGIN does not provide this guarantee (the lock is
    acquired lazily, only at first write), which is why every
    read-then-write path must use this helper rather than issuing SQL
    directly against the connection.

    On any exception inside the `with` block, the transaction is
    rolled back and the exception re-raised -- no partial write is
    ever left committed."""

    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
