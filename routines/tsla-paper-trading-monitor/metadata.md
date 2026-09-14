# TSLA Paper Trading Monitor

- **Live trigger id:** `trig_01NeX4pSm5jEHPXBJzCaNWkW`
- **Enabled:** yes
- **Cron (as stored):** `0 14-20 * * 1-5` (interpreted UTC by the live scheduler)
- **Interpretation:** Every hour on the hour, 14:00–20:00 UTC, Mon–Fri.
- **Model:** `claude-sonnet-5`
- **Allowed tools:** `Bash`
- **Environment id:** `env_017itRgk71mMf3EAdFhJu1WS`
- **Created:** 2026-09-12
- **Purpose:** Manage a single active TSLA laddered position on the
  Alpaca paper account (ladder + floor + trailing floor).

## Policy alignment (must reconcile — see `docs/trading/routine-policy-alignment.md`)

The prompt uses the running weighted-average entry (`avg_entry_price`)
as the reference for Ladder 1, Ladder 2, and Floor triggers, and
recomputes the floor after each fill. The approved policy (D-0001,
D-0009) requires those original references to be **frozen from the
initial fill only**. The trailing-floor math also diverges from
D-0004 (compounded thresholds) and D-0008 (threshold × 0.95).
Ladder execution is fully automatic, but D-0003 requires Controller
approval per ladder trigger.

These are documented conflicts; the live routine has not been changed.
