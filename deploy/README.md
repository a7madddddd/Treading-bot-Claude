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
| `universe-refresh.timer` | writes today's snapshot | weekdays 06:00 ET |
| `db-backup.timer` | pushes the database to GitHub | weekdays 16:30 ET |

Both are needed. The engine alone is not enough: it only READS
snapshots — nothing in `src/engine` writes one — and
`SnapshotUniverseSource` looks up TODAY's US-Eastern trading date only,
never reusing yesterday's. Since D-0054 removed the fallback watchlist,
a day without the refresh is a day with **no new trades at all**.

---

## Install

These are **user units** — no `sudo`, and they install into the `opc`
user's own systemd, not `/etc/systemd/system`. The reason is SELinux,
and it was proven on this VM rather than assumed: see the comment block
at the top of `trading-engine.service` for the two captured AVC denials.

```bash
cd ~/Treading-bot-Claude
git pull --no-rebase --no-edit origin claude/youthful-goodall-4cr0ei
chmod +x deploy/engine-run.sh deploy/universe-refresh.sh

mkdir -p ~/.config/systemd/user
cp deploy/trading-engine.service   ~/.config/systemd/user/
cp deploy/universe-refresh.service ~/.config/systemd/user/
cp deploy/universe-refresh.timer   ~/.config/systemd/user/
cp deploy/db-backup.service        ~/.config/systemd/user/
cp deploy/db-backup.timer          ~/.config/systemd/user/
systemctl --user daemon-reload
```

**Lingering is required**, once:

```bash
sudo loginctl enable-linger opc
```

Without it the units run only while someone is logged in over SSH, and
stop when the session closes — exactly the failure this is meant to
remove.

If an earlier attempt installed SYSTEM units, remove them first:

```bash
sudo systemctl disable --now trading-engine.service 2>/dev/null
sudo rm -f /etc/systemd/system/trading-engine.service \
           /etc/systemd/system/universe-refresh.service \
           /etc/systemd/system/universe-refresh.timer
sudo systemctl daemon-reload
```

Stop any hand-started engine, or the unit refuses to start on its lock:

```bash
pkill -TERM -f run_paper_session; sleep 12
```

Then enable both:

```bash
systemctl --user enable --now trading-engine.service
systemctl --user enable --now universe-refresh.timer
systemctl --user enable --now db-backup.timer
```

## The daily database backup (P-052)

`db-backup.timer` is the ONLY thing that pushes `paper_session.sqlite`.
The engine keeps `--no-db-push`, so the old per-tick behaviour — roughly
780 commits a trading day — cannot come back by accident.

It requires the VM to authenticate to GitHub. As of 2026-10-05 that is
an SSH **deploy key** scoped to this one repository with write access:
`~/.ssh/github_deploy_key`, selected by a `Host github.com` block in
`~/.ssh/config`, with the remote set to the `git@github.com:` form. The
private half never leaves the VM and is never committed.

Before the key existed the remote was HTTPS with no credential helper,
so a push sat forever at `Username for 'https://github.com':` — which is
exactly how an unattended timer hangs. Verify with:

```bash
ssh -T git@github.com        # expect: Hi <owner>/<repo>! You've successfully authenticated
git push --dry-run           # expect: Everything up-to-date, never 'Write access ... not granted'
```

Every run reports to Telegram: IMPORTANT on a successful push, OPTIONAL
when nothing changed that day, CRITICAL with the real git error on
failure. A quiet day is reported deliberately — silence would not let
the Controller tell "nothing changed" from "the job is broken".

## Verify

```bash
systemctl --user status trading-engine.service --no-pager
systemctl --user list-timers universe-refresh.timer --no-pager
```

`list-timers` must show the next elapse at **06:00 America/New_York**
(10:00 UTC while EDT is in force). If it shows **12:45 UTC** the
installed copy is the superseded 08:45 schedule — the repo was edited
but the unit was never re-copied and re-loaded. If it shows 06:00 UTC,
this systemd is too old to parse a timezone in `OnCalendar` — see the
fallback below.

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

Run the refresh once by hand without waiting for the timer.

**Use `--no-block`.** `systemctl start` on a `Type=oneshot` service
BLOCKS until the service finishes, and a full run takes around an hour —
so without it your terminal sits there with no output and it looks hung
when it is actually working. `--no-block` queues the job and returns
immediately.

If you already started one without it, press `Ctrl+C`: that stops the
systemctl client waiting, not the service. The job keeps running.

```bash
systemctl --user start --no-block universe-refresh.service
tail -f ~/Treading-bot-Claude/logs/universe.log
```

Watch progress and elapsed time:

```bash
systemctl --user is-active universe-refresh.service
systemctl --user show universe-refresh.service -p ActiveEnterTimestamp --value
tail -15 ~/Treading-bot-Claude/logs/universe.log
```

## Logs

Both units log to **files** under `logs/`:

```bash
tail -f  ~/Treading-bot-Claude/logs/engine.log
tail -40 ~/Treading-bot-Claude/logs/universe.log
```

This took two attempts, and both are worth knowing:

1. As SYSTEM units, file logging was refused with `status=209/STDOUT` —
   SELinux denies `init_t` writing to `user_home_t`. That is what drove
   the move to user units (D-0063).
2. The journal was then tried, and is **unreadable on this VM**:
   `/var/log/journal` does not exist and `journald.conf` has
   `Storage=auto`, so the journal is volatile only and
   `journalctl --user` answers "No journal files were found". A log you
   cannot read is not a log — it hid the outcome of the first real
   universe-refresh run.

Files are safe HERE specifically because these are USER units, which
run as `unconfined_t`. The same lines in a system unit would fail again.

`PYTHONUNBUFFERED=1` is exported by both wrapper scripts, because Python
buffers stdout when redirected to a file — which is why a hand-started
engine's `/tmp/engine.log` sat at 22 bytes for over a minute while the
engine was perfectly healthy.

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

## The setting that silently breaks this unit

`TimeoutStartSec=4h` on `universe-refresh.service` is not optional.

For `Type=oneshot`, `TimeoutStartSec` bounds the WHOLE run and defaults
to `DefaultTimeoutStartSec` = **90 seconds**. The full run enriches
11,683 symbols at one bars request each — roughly **58 minutes**.

The default killed the first real run: the timer fired at 14:19 on
2026-10-05, systemd killed it 90 seconds later, and the only evidence
was `Active: inactive (dead)` with no snapshot and no error. Running the
same script by hand succeeded at once, which is what proved the code was
fine and the unit was not.

---

## Timer fallback for older systemd

`OnCalendar` accepted a timezone from systemd v240. Check with
`systemctl --version`. On an older build, drop the timezone and use UTC,
then **correct it twice a year** at the US DST switches:

```
OnCalendar=Mon-Fri 10:00    # EDT (summer) = 06:00 ET
OnCalendar=Mon-Fri 11:00    # EST (winter) = 06:00 ET
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
