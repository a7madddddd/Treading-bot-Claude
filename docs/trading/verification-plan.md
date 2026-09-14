# Verification / Test Plan — for the pending changes

Applies to the proposed changes recorded in D-0015..D-0020. Nothing on
live routines will be exercised until Controller says APPLY.

---

## 1. Static verification (before APPLY)

- **Prompt review.** Controller reads `routines/tsla-paper-trading-monitor/prompt-proposed.md` and `routines/capitol-trades-copy-ro-khanna/prompt-proposed.md`. Line-by-line, the policy invariants must match `strategy.md`, `execution.md`, and `state-management.md`.
- **Grep sweep for secrets.** `git grep -E 'PKKV|SWeJa|pplx-|8892400776'` returns nothing. Automated as pre-commit later.
- **Decision-log completeness.** Every referenced decision id (D-0001..D-0020) exists in `decisions.md` with a matching status.
- **Cross-doc consistency.** Any place `strategy.md` or `execution.md` states an approved rule, the proposed prompt implements it word-for-word.

## 2. Deterministic-engine simulation (no orders, no broker calls)

Small simulator (language TBD) that consumes a canned price series and asserts:

- **Reference freeze.** `original_initial_entry_fill_price` is written once and never changes.
- **Original Floor invariance.** After Ladder 1 and Ladder 2 fills, `original_floor_price` is unchanged.
- **Trailing math.**
  - Activation at `entry × 1.10`.
  - Thresholds compound: t₁ = a × 1.10; tₙ = tₙ₋₁ × 1.05.
  - Floor at each threshold = threshold × 0.95.
  - Given entry = 100 and price crossing 110, 115.5, 121.275, 127.339…, floors are 104.5, 109.725, 115.211, 120.972 within ±0.001.
- **Monotonicity.** For any random walk, `trailing_floor_price` and `active_floor_price` are monotonically non-decreasing.
- **Ladder-below-floor block.** Contrive a series where trailing floor rises past the ladder trigger; verify the ladder cannot fire.
- **Approval expiration.** Submit only when age ≤ 5 min AND |current − trigger|/trigger ≤ 0.005. Both boundary cases tested.
- **Partial-fill freeze.** Initial order fills 6/10 then cancels; freeze uses 6 shares and their weighted-average.

## 3. Broker-integration dry run (paper endpoint, no state mutation)

Once credentials are rotated, run the routine end-to-end against Alpaca paper but with a **flag that suppresses order submission**. Verify:

- `/v2/orders/{id}`, `/v2/positions/TSLA`, `/v2/orders?status=open&symbols=TSLA`, `/v2/stocks/TSLA/trades/latest` all reachable and parsed correctly.
- Base-URL guard trips when pointed at anything but `paper-api.alpaca.markets`.
- Missing env var aborts before any call.
- Reconciliation detects a synthetic mismatch (temporarily point at a state file that disagrees with broker).

## 4. Approval-loop dry run

- Simulated proposal is emitted with all required fields.
- Simulated Controller approve/reject responses land in the state store.
- Re-check at submit time fails as expected for:
  - approval > 5 min old,
  - price outside ±0.5%,
  - active floor moved above the trigger.

## 5. Capitol Trades research-only verification

- Parser emits records with all six mandatory fields OR emits `parser BROKEN` — never a partial record.
- Deduplication by disclosure signature works across two consecutive runs.
- Under a simulated failed fetch, notification is CRITICAL and no records are emitted.
- Zero calls to `paper-api.alpaca.markets` or `data.alpaca.markets` (verified by network capture or code review).

## 6. Timezone verification

- Under a TZ-aware scheduler with `TZ=America/Chicago`, spring-forward and fall-back test dates fire routines at the intended CT wall-clock times (`09:00-15:00 CT` for monitor, `07:00 CT` for research, `14:55 CT` for daily summary).
- The same schedules under fixed-UTC cron are demonstrated to drift, confirming (a) is the correct implementation.

## 7. Rollback plan

If the APPLY leads to unexpected behavior in the first two trading sessions:

- **Immediate:** disable the corrected routine via `update_trigger { enabled: false }`. State store is preserved. Positions remain protected by the last-known active floor stop.
- **Recovery:** re-enable the previous prompt from the `routines/tsla-paper-trading-monitor/prompt.md` snapshot (with credentials handled via the new env-var approach). Only after Controller review of the incident and an entry in `decisions.md`.

## 8. Success criteria for APPLY

- All Section 2 assertions pass.
- Section 3 dry run passes end-to-end.
- Section 4 approval loop passes with both approve and reject paths.
- Section 6 timezone tests pass.
- Credential migration checklist in `credential-migration-plan.md` complete.
- Controller explicitly writes "APPLY THE CHANGES".
