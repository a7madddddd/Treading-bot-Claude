# TSLA Wheel Strategy — Hourly Monitor

- **Live trigger id:** `trig_01581wxFHJzBnuLXJUUuQtDS`
- **Enabled:** yes
- **Cron (as stored):** `0 13-20 * * 1-5` (interpreted UTC).
- **Interpretation:** Every hour on the hour, 13:00–20:00 UTC, Mon–Fri.
- **Model:** `claude-sonnet-5`
- **Allowed tools:** `Bash`
- **Environment id:** `env_017itRgk71mMf3EAdFhJu1WS`
- **Created:** 2026-09-12
- **Purpose:** Manage a **TSLA options wheel strategy** (cash-secured
  puts / covered calls) on Alpaca paper. Auto-executes options open/close
  based on 50% profit close and 10%-away strike selection.

## Policy alignment

This routine implements a **different strategy** (Wheel) not currently
covered by the approved policy (which is a ladder + floor + trailing on
equities only). It should be treated as an **experiment** until the
Controller decides whether to promote it to policy or archive it. See
`docs/trading/routine-policy-alignment.md` and consider adding an entry
to `docs/trading/experiments.md`.
