#!/usr/bin/env python3
"""Is the engine's loop alive, and if it is stuck, WHERE?

Built 2026-10-06 after the engine's first 10-symbol cycle hung for
seven minutes inside Engine._check_watchlist with the market open and
five positions unprotected. There is no heartbeat inside that method,
so a frozen heartbeat is the only signal, and the evidence that names
the culprit -- the open sockets -- disappears the moment the process is
restarted.

This samples both together, and with --watch it keeps sampling so the
evidence is captured at the moment of the hang instead of after it.

Read-only. Touches nothing but /proc and the lock file.

    PYTHONPATH=src python3.11 scripts/engine_health.py
    PYTHONPATH=src python3.11 scripts/engine_health.py --watch
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone

STALE_SECONDS = 300.0   # src/engine/lock.py STALE_THRESHOLD_SECONDS
LOCK_DB = "engine_lock.sqlite"


def _pid() -> int | None:
    try:
        out = subprocess.run(
            ["systemctl", "--user", "show", "-p", "ExecMainPID",
             "--value", "trading-engine.service"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        return int(out) if out and out != "0" else None
    except Exception:  # noqa: BLE001
        return None


def _heartbeat_age() -> float | None:
    if not os.path.exists(LOCK_DB):
        return None
    try:
        row = sqlite3.connect(LOCK_DB).execute(
            "SELECT heartbeat_at FROM engine_lock").fetchone()
        if not row:
            return None
        hb = datetime.fromisoformat(row[0])
        if hb.tzinfo is None:
            hb = hb.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - hb).total_seconds()
    except Exception:  # noqa: BLE001
        return None


def _cpu_and_threads(pid: int):
    try:
        out = subprocess.run(
            ["ps", "-o", "time=,nlwp=", "-p", str(pid)],
            capture_output=True, text=True, timeout=10).stdout.split()
        return (out[0], int(out[1])) if len(out) >= 2 else (None, None)
    except Exception:  # noqa: BLE001
        return (None, None)


def _sockets(pid: int):
    """The evidence that names the stuck source. api.telegram.org is
    expected and healthy -- it is the long-poll that receives the
    Approve/Reject buttons."""
    try:
        out = subprocess.run(["ss", "-tnp"], capture_output=True,
                             text=True, timeout=10).stdout
        return [l.strip() for l in out.splitlines() if f"pid={pid}" in l]
    except Exception:  # noqa: BLE001
        return []


def _sample(pid, prev_cpu):
    age = _heartbeat_age()
    cpu, threads = _cpu_and_threads(pid) if pid else (None, None)
    stamp = datetime.now().strftime("%H:%M:%S")
    if age is None:
        print(f"[{stamp}] no lock row -- is the engine running?")
        return cpu
    state = "STUCK" if age > 90 else ("stale" if age > STALE_SECONDS
                                      else "ok")
    moving = "" if prev_cpu is None else (
        " cpu MOVING" if cpu != prev_cpu else " cpu FROZEN")
    print(f"[{stamp}] pid={pid} heartbeat={age:6.0f}s {state:5}"
          f" cpu={cpu} threads={threads}{moving}")
    if age > 90 and pid:
        print("    --- the loop is inside _check_watchlist. sockets: ---")
        socks = _sockets(pid)
        if not socks:
            print("    (none visible -- try: sudo ss -tnp)")
        for s in socks:
            tag = ("  <- Telegram long-poll, EXPECTED"
                   if "149.154." in s else "  <- SUSPECT")
            print(f"    {s}{tag}")
        if cpu is not None and cpu == prev_cpu:
            print("    cpu FROZEN with a heartbeat this old = a hung "
                  "socket read, not slow work. It will not finish.")
    return cpu


def main() -> int:
    watch = "--watch" in sys.argv
    pid = _pid()
    if pid is None:
        print("could not read the engine PID from systemd")
    prev = None
    if not watch:
        _sample(pid, None)
        time.sleep(10)
        _sample(pid, prev)
        return 0
    print("watching every 15s -- Ctrl-C to stop")
    try:
        while True:
            if pid is None or not os.path.exists(f"/proc/{pid}"):
                pid = _pid()
            prev = _sample(pid, prev)
            time.sleep(15)
    except KeyboardInterrupt:
        print("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
