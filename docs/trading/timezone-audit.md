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

Each schedule shown as: intended CT time → cron expression to be evaluated **under an America/Chicago TZ-aware scheduler** (not fixed UTC).

| Routine | Intended CT | Cron in `TZ=America/Chicago` | Current-DST UTC equivalent for reference |
|---|---|---|---|
| tsla-paper-trading-monitor | Every hour on the hour 09:00–15:00 CT, Mon–Fri | `0 9-15 * * 1-5` | Summer: `0 14-20 * * 1-5` UTC; Winter: `0 15-21 * * 1-5` UTC |
| tsla-wheel-hourly-monitor | *(disabled — D-0016)* | — | — |
| tsla-wheel-daily-summary | 14:55 CT (5 min before close), Mon–Fri | `55 14 * * 1-5` | Summer: `55 19 * * 1-5` UTC; Winter: `55 20 * * 1-5` UTC |
| capitol-trades-copy-ro-khanna | 07:00 CT (pre-market research), Mon–Fri | `0 7 * * 1-5` | Summer: `0 12 * * 1-5` UTC; Winter: `0 13 * * 1-5` UTC |

Notes:
- 09:00 CT is 30 minutes after regular open — matches the current CDT behavior. Confirm this is the intent, or shift to `0 8-15 * * 1-5` to include the open hour.
- Capitol Trades moved to 07:00 CT (pre-market) so its findings feed the day's research before the open.

## 4. Implementation constraint

The scheduler currently used for these Routines interprets cron as UTC.
To be D-0020 compliant we must either:

- **(a)** run the routines under a scheduler that supports a `TZ` field (e.g. host cron with `CRON_TZ=America/Chicago`, or an in-language scheduler like APScheduler with a timezone argument), OR
- **(b)** re-issue the UTC cron twice a year on DST transitions. Ugly and error-prone.

**Recommendation:** (a). This is one of the reasons the language/runtime + scheduler decision needs to happen before we call `update_trigger`.

## 5. What we will NOT do

- Silently reissue UTC crons and pretend the DST bug is fixed.
- Change intended CT times without Controller approval.
