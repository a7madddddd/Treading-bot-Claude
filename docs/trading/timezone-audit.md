# Timezone Audit — America/Chicago (D-0005, D-0020)

Approved project timezone: **America/Chicago**, DST-aware.
Live triggers currently store **fixed UTC crons**, which drift by an
hour twice a year across DST transitions. This document names the drift
and proposes intended CT wall-clock times.

**No live trigger will be changed** until Controller says APPLY.

---

## 1. Reference — US market hours

- US equities regular session: **08:30–15:00 America/Chicago** (== 09:30–16:00 America/New_York).
- CDT (summer): UTC−5. CST (winter): UTC−6.

## 2. Current live cron expressions (stored as UTC)

| Routine | Cron (UTC) | Equivalent CT during CDT (summer) | Equivalent CT during CST (winter) |
|---|---|---|---|
| tsla-paper-trading-monitor | `0 14-20 * * 1-5` | 09:00–15:00 CT ✅ | 08:00–14:00 CT ⚠️ misses last hour |
| tsla-wheel-hourly-monitor | `0 13-20 * * 1-5` | 08:00–15:00 CT (extends 30 min pre-market) | 07:00–14:00 CT ⚠️ starts too early / misses close |
| tsla-wheel-daily-summary | `55 19 * * 1-5` | 14:55 CT (5 min before close) ✅ | 13:55 CT ⚠️ over an hour before close |
| capitol-trades-copy-ro-khanna | `0 14 * * 1-5` | 09:00 CT ✅ | 08:00 CT ⚠️ before open |

The ✅ rows are close to intent during CDT; every row silently breaks in November when the US switches back to CST.

## 3. Proposed CT wall-clock schedule (D-0020 compliant)

Approved anchor: **US regular session 08:30–15:00 America/Chicago**.
Each schedule shown as: intended CT time → cron expression to be evaluated **under an America/Chicago TZ-aware scheduler** (not fixed UTC).

| Routine | Intended CT | Cron in `TZ=America/Chicago` | Current-DST UTC equivalent for reference |
|---|---|---|---|
| tsla-paper-trading-monitor | 08:30 CT (open), then hourly on the half-hour through 14:30 CT (7 passes), Mon–Fri | `30 8-14 * * 1-5` | Summer (CDT, UTC−5): `30 13-19 * * 1-5` UTC; Winter (CST, UTC−6): `30 14-20 * * 1-5` UTC |
| tsla-wheel-hourly-monitor | *(disabled — D-0016)* | — | — |
| tsla-wheel-daily-summary | 14:55 CT (5 min before close), Mon–Fri | `55 14 * * 1-5` | Summer: `55 19 * * 1-5` UTC; Winter: `55 20 * * 1-5` UTC |
| capitol-trades-copy-ro-khanna | 07:00 CT (pre-market research), Mon–Fri | `0 7 * * 1-5` | Summer: `0 12 * * 1-5` UTC; Winter: `0 13 * * 1-5` UTC |

Notes:
- **08:30 CT** is the approved market-open anchor. The Controller directed that this must not be silently changed. The first monitor pass fires at the bell, then every 60 minutes thereafter, giving 8:30, 9:30, 10:30, 11:30, 12:30, 13:30, 14:30 CT. The final 30 minutes of the session are covered by the 14:30 pass followed by the 14:55 daily-summary run.
- Capitol Trades runs at 07:00 CT (pre-market) so findings feed the day's research before the open.

## 4. Scheduler-runtime inspection

Findings from inspecting the four live triggers (`list_triggers` response, 2026-09-14):

- Each trigger stores a plain 5-field `cron_expression` string.
- There is **no `timezone`/`tz` field** on the trigger record.
- The runtime evaluates the stored cron against **UTC** — verified by comparing `cron_expression` hours to `next_run_at` values (RFC3339 `Z`).
- Each trigger carries an `environment_variables: {}` block that is empty on all four routines. Values written there would be delivered to the runtime as env vars, which is the mechanism we should use for credentials.

**Implication.** The current runtime does not natively honor `TZ=America/Chicago`. To be D-0020 compliant we have three options:

- **(a) TZ-aware in-language scheduler**, replacing per-routine cron with an APScheduler / cron-with-`CRON_TZ` job that runs a small dispatcher inside a persistent process, and invokes the routine logic directly. Most correct; requires the language/runtime decision.
- **(b) Two UTC crons per routine** — one for CDT (Mar–Nov) and one for CST (Nov–Mar), each disabled in the other half of the year via `enabled` flag toggled by an external orchestrator on the DST-transition weekends. Ugly, but no runtime change required.
- **(c) One UTC cron, manually reissued twice per year** at the DST-transition weekends. Ugly and error-prone.

**Recommendation:** **(a)** as the target; **(b)** as an acceptable interim if we must ship on the existing trigger runtime before the language/runtime decision lands. **(c)** is not acceptable — it silently misses the intended CT time whenever the reissue is forgotten.

The Controller has not yet decided (a) vs (b). Do not act on either before that decision AND an explicit APPLY.

## 5. What we will NOT do

- Silently reissue UTC crons and pretend the DST bug is fixed.
- Change intended CT times without Controller approval.
