# Deployment — Oracle Cloud VM

Everything here runs on the Controller's Oracle VM
(`/home/opc/Treading-bot-Claude`). Nothing here runs in a Claude session
container: a container is reclaimed when idle, and a background process
dies with it, which is what made the engine look supervised while it was
not.

Two units, approved 2026-10-05.

| unit | what it does | when |
|---|---|---|
| `trading-engine.service` | keeps the engine alive | always |
| `universe-refresh.timer` | writes today's snapshot | weekdays 08:45 ET |

Both are needed. The engine alone is not enough: it only READS
snapshots — nothing in `src/engine` writes one — and
`SnapshotUniverseSource` looks up TODAY's US-Eastern trading date only,
never reusing yesterday's. Since D-0054 removed the fallback watchlist,
a day without the refresh is a day with **no new trades at all**.

---

## Install

```bash
cd ~/Treading-bot-Claude
git pull --no-rebase --no-edit origin claude/youthful-goodall-4cr0ei
chmod +x deploy/engine-run.sh deploy/universe-refresh.sh

sudo cp deploy/trading-engine.service   /etc/systemd/system/
sudo cp deploy/universe-refresh.service /etc/systemd/system/
sudo cp deploy/universe-refresh.timer   /etc/systemd/system/
sudo systemctl daemon-reload
```

Stop any hand-started engine first, or the new unit will refuse to start
on the lock held by the old process:

```bash
pkill -TERM -f run_paper_session; sleep 12
```

Then enable both:

```bash
sudo systemctl enable --now trading-engine.service
sudo systemctl enable --now universe-refresh.timer
```

## Verify

```bash
systemctl status trading-engine.service --no-pager
systemctl list-timers universe-refresh.timer --no-pager
```

`list-timers` must show the next elapse at **08:45 America/New_York**.
If it shows 08:45 UTC instead, this systemd is too old to parse a
timezone in `OnCalendar` — see the fallback below.

Liveness is the heartbeat, not the log. Python buffers stdout when it is
redirected to a file, so an empty log does not mean a dead engine:

```bash
cd ~/Treading-bot-Claude && python3.11 -c "
import sqlite3, datetime
c = sqlite3.connect('engine_lock.sqlite'); c.row_factory = sqlite3.Row
for r in c.execute('SELECT * FROM engine_lock'):
    hb = datetime.datetime.fromisoformat(r['heartbeat_at'])
    age = (datetime.datetime.now(datetime.timezone.utc) - hb).total_seconds()
    print('pid', r['pid'], '| heartbeat age', round(age, 1), 's')
"
```

A heartbeat under ~60s old is healthy (the reconcile interval is 30s).

Run the refresh once by hand without waiting for the timer:

```bash
sudo systemctl start universe-refresh.service
journalctl -u universe-refresh --since "10 min ago" --no-pager
```

## Logs

Both units log to the **journal**, not to files:

```bash
journalctl -u trading-engine -f              # live
journalctl -u trading-engine --since today
journalctl -u universe-refresh --since today
```

File logging under `/home` was tried first and systemd refused the unit
with `status=209/STDOUT`: on Oracle Linux 9 with SELinux enforcing, a
system service may not write into a user home directory. The journal
needs no policy change, and it also avoids a real trap — Python buffers
stdout to a FILE, which is why a hand-started engine's log can sit at 22
bytes for a minute while the engine is perfectly healthy. The journal is
a pipe, so lines appear immediately.

---

## The three settings that are not defaults

Each was derived from the code. Getting any of them wrong leaves the
system looking supervised while it is actually dead.

**`Restart=always`, not `on-failure`.**
`run_paper_session.py` exits with status **0** when its `--max-hours`
cap expires. That is a clean shutdown — and it is exactly what stopped
the engine on 2026-10-04. To `Restart=on-failure`, a status-0 exit looks
like success, so it would never restart.

**`RestartSec=310`, not the default 100ms.**
`src/engine/lock.py` judges liveness ONLY by `heartbeat_at`, with
`STALE_THRESHOLD_SECONDS = 300`. A clean stop releases the lock
(`Engine.shutdown()` runs in the runner's `finally`), but after a hard
kill — SIGKILL, OOM, power loss — the row still looks live for up to 5
minutes, and a restarting engine refuses with `EngineLockHeldError`.
310s waits that out on the first attempt.

**`StartLimitIntervalSec=0`.**
systemd's default gives up after 5 starts in 10s and parks the unit in
a FAILED state. With `RestartSec=310` that limit is unreachable today,
but a future change to `RestartSec` must not be able to silently brick
the supervisor.

---

## Timer fallback for older systemd

`OnCalendar` accepted a timezone from systemd v240. Check with
`systemctl --version`. On an older build, drop the timezone and use UTC,
then **correct it twice a year** at the US DST switches:

```
OnCalendar=Mon-Fri 12:45    # EDT (summer) = 08:45 ET
OnCalendar=Mon-Fri 13:45    # EST (winter) = 08:45 ET
```

This is the drift D-0041 warned about, so prefer the named timezone
whenever systemd supports it.

---

## Two SQLite files

`paper_session.sqlite` holds trading state. `engine_lock.sqlite` holds
only the liveness heartbeat and is gitignored — deleting it never loses
trading state, and it is safe to remove if a stale lock ever blocks a
start.

From 2026-10-05 the universe refresh is a SECOND process writing
`paper_session.sqlite` while the engine runs. `persistence/db.py` sets
`busy_timeout` to 30000ms for that reason; the measurements behind the
value are in `connect()`'s comment and in P-023.

---

## What is NOT the mechanism

`scripts/run_scheduler.py` is **not** used in production. Its job
callable only prints a line. The engine runs its own D-0021 loop
internally, and the daily refresh is this timer.
