#!/usr/bin/env python3
"""Daily backup of paper_session.sqlite to GitHub, with a Telegram
confirmation either way (P-052, Controller request 2026-10-05).

WHY THIS IS NOT IN THE ENGINE
-----------------------------
`scripts/run_paper_session.py` already contains `_make_db_persister()`,
which does the same git work at the end of EVERY reconciliation tick.
That produced roughly 780 commits a trading day, which is why the engine
now runs with `--no-db-push`.

This job keeps that flag in place and runs once, after the close:

  * a backup is not trading logic, and its failure must never be able to
    touch a tick that is also evaluating protective exits;
  * running with the market closed means it can never compete with a
    trading tick for the SQLite write lock;
  * leaving `--no-db-push` on the engine makes a regression to per-tick
    pushing structurally impossible rather than a matter of care.

SAFETY
------
Only `paper_session.sqlite` is ever staged or committed -- `git commit
--only <file>` -- so nothing else that happens to be dirty in the
working tree can ride along. This is the same guarantee the per-tick
persister was approved with on 2026-10-01.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import NamedTuple, Optional, Sequence

_here = os.path.abspath(os.path.dirname(__file__))
_repo_root = os.path.abspath(os.path.join(_here, ".."))
_src = os.path.join(_repo_root, "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

DB_FILENAME = "paper_session.sqlite"


class BackupOutcome(NamedTuple):
    """What the run did. `level` is the Telegram level to report at."""

    status: str          # "pushed" | "no_change" | "failed"
    level: str           # "IMPORTANT" | "OPTIONAL" | "CRITICAL"
    message: str


def classify(*, changed: bool, error: Optional[str],
             commit: Optional[str] = None,
             size_bytes: Optional[int] = None,
             today: Optional[str] = None) -> BackupOutcome:
    """Pure decision: what to report, given what happened.

    Split out so the reporting rules are tested without touching git,
    the network, or Telegram.

    A quiet day is reported too, at OPTIONAL. Silence would be
    ambiguous -- the Controller could not tell "nothing changed today"
    from "the backup job is broken" -- and that ambiguity is the exact
    failure shape D-0054 removed from the trading side.
    """
    day = today or datetime.now(timezone.utc).date().isoformat()
    if error:
        return BackupOutcome(
            status="failed",
            level="CRITICAL",
            message=(
                f"Database backup FAILED for {day}.\n"
                f"\n"
                f"{error}\n"
                f"\n"
                f"The database is still safe on the VM's disk. Only the "
                f"off-machine copy is missing. Nothing about trading is "
                f"affected."
            ),
        )
    if not changed:
        return BackupOutcome(
            status="no_change",
            level="OPTIONAL",
            message=(
                f"Database backup {day}: nothing changed today, so "
                f"nothing was saved. The job ran and is healthy."
            ),
        )
    size_mb = (size_bytes or 0) / (1024 * 1024)
    return BackupOutcome(
        status="pushed",
        level="IMPORTANT",
        message=(
            f"Database updated for {day} ✅\n"
            f"\n"
            f"Saved to GitHub, {size_mb:.1f} MB"
            + (f", commit {commit}" if commit else "")
            + "."
        ),
    )


def _git(args: Sequence[str], *, check: bool = True):
    return subprocess.run(
        ["git", *args], cwd=_repo_root, check=check,
        capture_output=True, text=True, timeout=180,
    )


def _db_changed() -> bool:
    unstaged = _git(["diff", "--quiet", "--", DB_FILENAME], check=False)
    staged = _git(["diff", "--cached", "--quiet", "--", DB_FILENAME],
                  check=False)
    return unstaged.returncode != 0 or staged.returncode != 0


def _current_branch() -> str:
    return _git(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()


def run() -> BackupOutcome:
    db_path = os.path.join(_repo_root, DB_FILENAME)
    if not os.path.exists(db_path):
        return classify(changed=False, error=f"{DB_FILENAME} not found")

    try:
        branch = _current_branch()
        # Bring the branch up to date FIRST. Without this, any commit
        # pushed from elsewhere since the last backup makes this push a
        # non-fast-forward and the job fails every day until someone
        # notices.
        _git(["pull", "--no-rebase", "--no-edit", "origin", branch])

        if not _db_changed():
            return classify(changed=False, error=None)

        _git(["add", "--", DB_FILENAME])
        if not _db_changed():
            return classify(changed=False, error=None)

        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _git(["commit", "--only", DB_FILENAME, "-m",
              f"chore(db): daily backup {stamp}\n\n"
              f"Automated once-a-day backup after the close (P-052).\n"])
        _git(["push", "origin", branch])
        commit = _git(["rev-parse", "--short", "HEAD"]).stdout.strip()
        return classify(changed=True, error=None, commit=commit,
                        size_bytes=os.path.getsize(db_path))
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        return classify(changed=False,
                        error=f"git {' '.join(exc.cmd[1:3])}: {detail[:400]}")
    except Exception as exc:  # noqa: BLE001 - a backup must never crash loudly
        return classify(changed=False, error=f"{type(exc).__name__}: {exc}")


def _send(outcome: BackupOutcome) -> None:
    try:
        from notifications.telegram import TelegramNotificationService
        from notifications.service import (
            NotificationEvent, NotificationLevel,
        )
        TelegramNotificationService.from_env().send(NotificationEvent(
            level=NotificationLevel[outcome.level],
            event=f"db_backup_{outcome.status}",
            message=outcome.message, symbol=None, extra=(),
        ))
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] telegram send failed: {exc}", file=sys.stderr)


def main() -> int:
    outcome = run()
    print(f"[backup] {outcome.status}: {outcome.message.splitlines()[0]}")
    _send(outcome)
    return 1 if outcome.status == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
