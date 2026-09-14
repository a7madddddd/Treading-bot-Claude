# Routine ↔ Approved Policy Alignment

Snapshot of how each **live** Routine matches the **approved policy**
(`strategy.md`, `execution.md`, and the decision log).

Status codes:
- **ALIGNED** — matches approved policy.
- **DIVERGES** — does something the approved policy does not permit.
- **NEW STRATEGY** — implements a strategy that is not the approved policy at all.
- **READ-ONLY** — no orders placed; safe to run as-is.

---

## 1. `tsla-paper-trading-monitor` — DIVERGES (multiple items)

Intent matches the approved policy (initial + Ladder 1/2 + Floor +
Trailing Floor on TSLA). Implementation deviates in ways that materially
change trading behavior.

| # | Approved policy | Live routine | Delta |
|---|---|---|---|
| 1 | Original Ladder/Floor references calculated from **ORIGINAL initial fill price**, frozen after initial reconciliation (D-0001, D-0009) | Uses `avg_entry_price` from Alpaca `/v2/positions` — the **running weighted-average** after every fill. | After Ladder 1 fills, Ladder 2 and Floor **shift down**, contradicting the frozen-reference rule. |
| 2 | Original Floor never moves (D-0001) | Cancels and re-places the stop at `new_avg * 0.90` after each ladder fill | Original Floor is being reset — the actual stop wanders with the average. |
| 3 | Trailing thresholds are **compounded 5% steps** from previous threshold (D-0004) | "Every additional +5% gain" — linear from the current price | Different threshold sequence in a rising market. |
| 4 | Trailing floor at each threshold = **threshold_price × 0.95** (D-0008) | `new_floor = current_price * 0.95` — tick-based | Non-deterministic under gap-ups; higher floor than policy on a spike. |
| 5 | Ladder 1 and Ladder 2 require **Controller approval** per trigger (D-0003) | Both auto-execute | No approval step at all. |
| 6 | Ladder approval expires after **5 min AND price within 0.5%** of trigger (D-0007) | N/A (no approval workflow) | Cannot enforce because #5 is missing. |
| 7 | One authoritative active protective floor, priority over ladders | Partial — has "block ladder below floor" check | Direction correct but combined with #1/#2/#4, the floor value itself is unreliable. |
| 8 | Trigger source deferred (D-0011) | Uses `/v2/stocks/TSLA/trades/latest` (last trade) with no debounce | The user resolved trigger source to Alpaca **Last Trade** (see below). No debounce policy yet — needs Controller confirmation. |

### Reconciliation options for the Controller

- **(a) Update the live routine to match approved policy.** I can draft a
  rewritten prompt and, once you approve, apply it via `update_trigger`.
- **(b) Change the approved policy to match the running routine.** Requires
  explicit decisions superseding D-0001, D-0003, D-0004, D-0008, D-0009.
- **(c) Disable this routine** until the reconciliation is complete.

I recommend **(a) or (c)**. The bot is currently trading a different
policy than the one on paper; whichever direction we go, we should stop
running one policy while documenting another.

---

## 2. `tsla-wheel-hourly-monitor` — NEW STRATEGY (options wheel)

The approved policy is a laddered equity strategy. The Wheel Strategy is
a different beast (sell puts / covered calls, 50% profit close, ~10%
strike, 2–4 weeks DTE) that runs on options. It also **auto-executes**
option sells with no approval step, violating D-0003.

Recommended: log an **experiment** in `docs/trading/experiments.md`, and
either promote to approved policy via a Controller decision or disable
the routine until then.

## 3. `tsla-wheel-daily-summary` — READ-ONLY

Safe. Reports only. Belongs to the same experiment as #2.

## 4. `capitol-trades-copy-ro-khanna` — NEW STRATEGY (congressional mirror)

Also not the approved policy. Auto-buys 1 share and auto-sells full
positions based on scraped disclosures. Auto-execution violates D-0003.
Fragile scrape (HTML parsing on a third-party site).

Recommended: same as #2. Experiment or disable.

---

## Cross-cutting issues

- **Timezone.** Cron expressions on the live triggers are stored as UTC.
  Approved timezone is America/Chicago (D-0005). Not necessarily wrong
  (a UTC cron can encode CT with the correct hour), but should be
  audited: convert intended CT wall-clock times to the correct UTC hours
  for CST vs. CDT — or move to a TZ-aware scheduler.
- **Hardcoded Alpaca API keys** in every live prompt (key id and secret both). Must be rotated in the Alpaca dashboard; live triggers must then be edited via `update_trigger` (or the Routines UI) to reference env vars instead of literal values. Do NOT commit the keys anywhere in this repo.
- **No shared state or lock** between the four routines. Two hitting Alpaca simultaneously is unlikely but not impossible; a mutual-exclusion pattern is on the roadmap.
