# Second Pre-APPLY Consistency Review — 2026-09-14

Run after the cosmetic clarification edits requested by the Controller.
Scope: same as the first review (`consistency-review-2026-09-14.md`)
plus the newly added scheduler design.

**Nothing on live routines has changed.**

---

## 1. What changed since the first review

| Where | Before | After |
|---|---|---|
| `strategy.md` §5 Activation | Wording "5% below the current market price" (could read as tick-based) | `activation_threshold = weighted_avg_entry × 1.10` and floor = `activation_threshold × 0.95`; explicit "NOT the current tick price"; explicit note that a tick print above threshold at activation does not raise the initial floor. Cites D-0008 and D-0012. |
| `state-management.md` | No mention of the paper-endpoint guard | New §3a cross-references `execution.md §8`; state manager depends on the runtime having asserted paper-only + valid env before any write. |
| `architecture/overview.md` §5 | Conceptual routines list (pre-market 07:00, open 08:30, midday 11:00, generic EOD) — mostly stale | Replaced with a table of the currently approved schedule (Capitol Trades 07:00, monitor 08:30-14:30, wheel summary 14:55, wheel hourly disabled). Pointer to `timezone-audit.md` and `scheduler-design.md`. Notes there is no dedicated midday or EOD ladder routine today. |
| `execution.md` §3 Proposal contents | Bare "Current market price"; short list of 7 fields | "Current price" is now defined as the Alpaca Last Trade (D-0012). Explicit note that this list is the minimum; full persistent proposal + approval record lives in `state-management.md §Approval workflow state`. |
| `CLAUDE.md` §7 | Pointer list preceded the newer docs | Added state-management.md, research-sources.md, timezone-audit.md, credential-migration-plan.md, verification-plan.md, scheduler-design.md, routine-policy-alignment.md, both consistency reviews, and the `routines/` layout. |
| new: `scheduler-design.md` | — | Evidence-based Options A/B/C/D for DST-safe scheduling. Recommends A (persistent TZ-aware host, language TBD) with B as an acceptable interim on the existing UTC-only trigger runtime. Names the APPLY blocker. |

Zero policy-level rules were changed. Zero decisions were superseded.
Every edit is a clarification of what already-approved decisions
implied.

## 2. Do contradictions remain?

Following the changes above, the answer to each finding in the first
review:

| # | First-review finding | Resolution |
|---|---|---|
| A1 | strategy.md §5 activation ambiguity | **Resolved.** Wording is now explicit `threshold × 0.95`. |
| A2 | state-management gap on paper-endpoint guard | **Resolved.** §3a added. |
| A3 (part 1) | architecture/overview §5 stale conceptual list | **Resolved.** Table now matches approved schedule; stale midday-11:00 and generic EOD removed. |
| A3 (part 2) | architecture/overview §5 timezone hint | **Resolved.** Explicit CT + pointer to timezone-audit.md and scheduler-design.md. |
| — | execution.md §3 "Current market price" wording | **Resolved.** Names Alpaca Last Trade (D-0012). |
| — | execution.md §3 minimum-vs-full field list | **Resolved.** Explicit note points to state-management.md. |
| — | CLAUDE.md §7 stale file list | **Resolved.** Newer docs listed. |
| — | Scheduler design missing | **Resolved (design).** `scheduler-design.md` added. **Implementation still TBD.** |

No remaining wording contradictions I can find between:

- `strategy.md` and `decisions.md` (D-0001, D-0004, D-0007, D-0008, D-0009, D-0010, D-0015 all consistent with the strategy text)
- `execution.md` and `state-management.md` (protective-order rules and approval-workflow fields consistent; execution.md is the "at minimum" view, state-management is the full record — link now in place)
- `strategy.md` and the proposed prompt (`prompt-proposed.md`) — the prompt implements the threshold-based trailing math exactly as strategy.md §5 now describes it
- `research-sources.md` and the proposed Capitol Trades prompt — both prohibit Alpaca calls; both require typed findings; both alarm CRITICAL on parser breakage
- `credential-migration-plan.md` and CLAUDE.md §8 — same env-var-only rule
- `verification-plan.md` and every doc it references — targets match the current wording

Grep sweep (excluding the review docs' own grep-pattern strings): no
committed file contains the old Alpaca / Perplexity / Telegram keys.

## 3. Scheduler limitation and proposed solution

### Limitation (evidence)

Current trigger runtime stores plain 5-field UTC cron; no timezone
field on the record. `next_run_at` values align with the cron hour
interpreted as UTC. Env-var slot exists (`environment_variables: {}`)
but is empty on all four triggers.

### Proposed solution

Documented in full at `docs/trading/scheduler-design.md`:

- **Target (Option A):** move scheduling to a persistent TZ-aware host
  process using an IANA `America/Chicago` timezone-aware scheduler.
  Language deferred; every mainstream ecosystem has an equivalent
  primitive. Also gives the ladder engine a natural home for the
  D-0018 state store.
- **Interim (Option B) if we must ship on the existing runtime:** two
  triggers per routine (`-cdt` / `-cst`) with a small DST-toggle job
  that enables/disables the correct one at each transition weekend.
  Deterministic and safe, but adds two toggles per year per routine
  and no native home for shared state — so the state store must live
  on separate infrastructure.
- **Option C** (single UTC cron manually reissued) is off the table.
- **Option D** (one dispatch trigger firing every N minutes and gating
  on CT wall-clock) is not recommended (wasted runs, duplicated gating
  logic).

## 4. What is still TBD

- **D-0011** — Ladder trigger debounce. Untouched. Do not invent.
- **D-0013** — Trading universe. Untouched. Do not invent.
- **Language / runtime.** Repository evidence still does not force a
  choice. Recommend deferring until Controller is ready.
- **State-store medium.** Deferred to language/runtime choice.
- **Option A vs Option B (interim) for the scheduler.** Neither has
  Controller approval yet.
- **Approval-loop transport.** The proposed prompt calls
  `notify_controller(...)`; whether that is a Telegram bot with
  inline-keyboard buttons, a shared web page, or a REST endpoint is
  not decided. Design principle from D-0007 is fixed; delivery
  mechanism is not.

## 5. What must happen before APPLY

Hard blockers (must be resolved or explicitly accepted by Controller):

1. **Scheduler decision.** Controller picks between Option A and
   Option B (or approves an alternative). Nothing runs on the correct
   CT time under DST until this is resolved.
2. **Credential rotation.** Alpaca paper keys, Perplexity key, Telegram
   bot token rotated; `.env` populated; not committed. Live triggers
   cleaned of raw keys before or as part of APPLY.
3. **State-store target.** Under Option A: language/runtime + storage
   medium picked (SQLite file / JSONL / KV). Under Option B: an
   external state store is chosen (broker-note has size limits; a
   small managed KV or DB is likely required).
4. **Approval-loop delivery.** A concrete mechanism the corrected
   routine can call — even if it is a Telegram bot with two inline
   buttons, we need the wiring specified before APPLY, not after.
5. **Verification pass.** Sections 1, 2, 4, 6 of `verification-plan.md`
   completed successfully (static review, deterministic engine math,
   approval-loop dry-run, timezone tests).

Soft blockers (nice-to-have before APPLY, not strictly required):

- Section 3 broker dry-run — requires (2) and a working runtime.
- Section 5 research-only checks for Capitol Trades — same.

## 6. Bottom line

- Documentation is now internally consistent. Every ambiguity flagged
  in the first review has been resolved with wording changes only. No
  policy-level rule changed.
- The **scheduler / state / runtime blockers are NOT resolved**. Only
  a design has been produced. Controller has not yet chosen A vs B
  and no host or language has been picked.
- Credentials still need rotation.
- Therefore this is **not** the moment to APPLY. Awaiting Controller
  input on the blockers in §5.
