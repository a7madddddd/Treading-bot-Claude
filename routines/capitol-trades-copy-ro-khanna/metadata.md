# Capitol Trades — Copy Ro Khanna

- **Live trigger id:** `trig_01UzNbZZgcGj8Jkj9SZHJwJR`
- **Enabled:** yes
- **Cron (as stored):** `0 14 * * 1-5` (interpreted UTC).
- **Interpretation:** Weekdays at 14:00 UTC.
- **Model:** `claude-sonnet-5`
- **Allowed tools:** `Bash`, `WebFetch`
- **Environment id:** `env_017itRgk71mMf3EAdFhJu1WS`
- **Created:** 2026-09-12
- **Purpose:** Scrape https://www.capitoltrades.com for Ro Khanna's
  newly disclosed trades within the last 24 hours and mirror them
  (1 share per BUY, sell full position on SELL) into the Alpaca paper
  account.

## Policy alignment (must reconcile)

This routine implements a **different strategy** (congressional trade
copying) not covered by the approved policy. It auto-executes buys and
sells with no Controller approval step, which conflicts with D-0003
(entries and discretionary trades require Controller approval).

Also:
- Scraping HTML from capitoltrades.com is fragile and may break silently.
- One-share sizing is a stub that will not represent real portfolio
  exposure but does consume real (paper) buying power and produces
  reporting noise.

Should be treated as an **experiment** pending Controller review, or
disabled until reconciled with the approved policy.
