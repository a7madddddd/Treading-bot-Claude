# Routines

Snapshot of the account-level scheduled Routines (triggers) that operate
this project. Each subdirectory mirrors one live Routine:

| Directory | Live trigger id | Name | Schedule (UTC cron) |
|---|---|---|---|
| `tsla-paper-trading-monitor/` | `trig_01NeX4pSm5jEHPXBJzCaNWkW` | TSLA Paper Trading Monitor | `0 14-20 * * 1-5` |
| `tsla-wheel-hourly-monitor/` | `trig_01581wxFHJzBnuLXJUUuQtDS` | TSLA Wheel Strategy — Hourly Monitor | `0 13-20 * * 1-5` |
| `tsla-wheel-daily-summary/` | `trig_01TxPK91QKfqtsHVgexhTxVB` | TSLA Wheel Strategy — Daily Summary | `55 19 * * 1-5` |
| `capitol-trades-copy-ro-khanna/` | `trig_01UzNbZZgcGj8Jkj9SZHJwJR` | Capitol Trades — Copy Ro Khanna | `0 14 * * 1-5` |

## Important

- The cron expressions on the live triggers appear to be in **UTC** (not
  CT). See `docs/trading/decisions.md` D-0005 for the approved timezone
  and confirm with the Controller whether these need converting.
- Each `prompt.md` here is the **live prompt with Alpaca API keys
  redacted** and replaced by `${ALPACA_API_KEY_ID}` /
  `${ALPACA_API_SECRET_KEY}`. **Never commit the real keys.**
- The live Routines still hold the leaked keys until you rotate them
  and update each trigger.
- Any change to a routine's trading behavior requires Controller
  approval and a decision-log entry (`docs/trading/decisions.md`)
  before it goes live.

## Policy alignment status

See `docs/trading/routine-policy-alignment.md` for a per-routine
comparison against the approved policy in `docs/trading/strategy.md`
and open reconciliation items.
