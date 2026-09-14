# Pre-APPLY Consistency Review — 2026-09-14

Cross-doc review before the Controller's APPLY, per Controller request.
Scope: approved strategy, decisions.md, routine prompts (live + proposed),
state-management contract, research architecture, timezone/scheduler
design, credential migration, verification plan.

**Status of the codebase:** consistent on the substantive policy — every
proposed artifact respects the approved rule set. Remaining items are
**wording ambiguities** and **stale examples** in older docs that were
not rewritten when later decisions superseded them. None change trading
behavior. None require a policy change. I flag each so Controller can
decide whether to clarify in a followup edit before APPLY, or leave and
trust the newer decision as controlling.

Findings are labelled:
- **AMBIGUITY** — the wording could be read two ways; a later decision
  already resolves it.
- **STALE** — the doc still uses an early example or list that was
  overtaken by a later decision.
- **GAP** — something not yet documented that a reader would reasonably
  expect.
- **OK** — checked, consistent.

---

## 1. Approved strategy ↔ decisions ↔ proposed routine prompt

| Item | strategy.md | decisions.md | prompt-proposed.md | Verdict |
|---|---|---|---|---|
| Ladder levels −5% / −8% and Floor −10% | ✅ | D-0001 ✅ | ✅ | OK |
| Reference frozen from initial fill | ✅ §2 | D-0001, D-0009 | ✅ §2 | OK |
| Partial-fill + cancel handled | ✅ §2 | D-0010 | ✅ §2 | OK |
| Original Floor never moves on ladder fill | ✅ §7 | D-0001, D-0015 | ✅ §7, §9, §10 | OK |
| Trailing compounded from prior threshold | ✅ §5 Ratchet | D-0004 | ✅ §8 | OK |
| Trailing floor = threshold × 0.95 | ✅ §5 Ratchet | D-0008 | ✅ §8 | **AMBIGUITY** — see §A1 |
| Ladder 1 / Ladder 2 need Controller approval | (via `execution.md`) | D-0003 | ✅ §9, §10 | OK |
| Approval 5 min AND ±0.5% | (via `execution.md`) | D-0007 | ✅ §9 | OK |
| Trigger source = Alpaca Last Trade | (implied) | D-0012 | ✅ §3 | OK |
| Active floor priority never violated | ✅ §4 | D-0001 | ✅ §5, §7 | OK |

### A1 — AMBIGUITY: strategy.md §5 "On activation the trailing floor = 5% below the current market price"

The activation clause says "5% below the current market price". Read
literally, that is tick-based. D-0008 supersedes: threshold-based
(threshold × 0.95). The very next sub-section (Ratchet, lines 91–101)
correctly says "threshold-based, NOT the actual tick price".

At the activation instant they numerically converge only if `p_last ==
weighted_avg × 1.10` exactly; if the market gaps through activation
(`p_last > weighted_avg × 1.10`), the two interpretations diverge on the
initial trailing floor value.

**Recommended clarification** (not applied — approved-policy file):
change the Activation clause to read
"On activation, the initial trailing threshold = weighted-average entry × 1.10, and the trailing floor = threshold × 0.95 (D-0008)."
Requires a Controller decision to edit `strategy.md`. The proposed
prompt already implements the D-0008 interpretation, so if Controller
prefers to leave `strategy.md` untouched, the running behavior is still
correct.

## 2. execution.md ↔ state-management.md ↔ proposed prompt

| Item | execution.md | state-management.md | prompt-proposed.md | Verdict |
|---|---|---|---|---|
| Semi-automatic approval workflow | ✅ §1–3 | ✅ Approval fields | ✅ §9, §10 | OK |
| Ladder approval expiration 5 min + ±0.5% | ✅ §3 | ✅ Invariant 5 | ✅ §9 | OK |
| Active protective floor priority | ✅ §4 | ✅ Invariants 3, 6 | ✅ §5–7 | OK |
| One authoritative protective exit | ✅ §5 | ✅ Invariant 7 | ✅ §7 | OK |
| Order-management: replace-then-cancel? | §5 says "not longer than technically unavoidable" | §Invariants 7 says "successor ready before cancel" | Prompt §7 says "submit new stop first, then cancel old" | Consistent, tighter each layer; OK. |
| Environment guard | ✅ §8 | (not explicit) | ✅ Credentials section | **GAP** — see §A2 |
| Proposal contents (minimum) | ✅ §3 lists 7 fields | ✅ Approval workflow lists ~15 fields | ✅ prompt uses state-management list | **AMBIGUITY** — execution.md's shorter list is a subset; wording could suggest 7 is the ceiling. Clarify in execution.md as "at minimum, and see state-management for the full record." Non-policy change. |

### A2 — GAP: paper-endpoint guard mentioned in prompt, not in state-management

state-management.md doesn't repeat the paper-endpoint startup guard from
execution.md §8 or prompt-proposed §Credentials. Not a contradiction;
the guard belongs to the runtime, not the state store. Suggest adding a
one-line pointer in state-management to `execution.md §8` for clarity.
Non-policy change.

## 3. Trailing example numbers

strategy.md §5 example shows compounded thresholds `100 × 1.10 = 110.00;
110 × 1.05 = 115.50; 115.50 × 1.05 = 121.275; 121.275 × 1.05 = 127.34.`
Table then lists threshold `$127.339` (three decimals). Value is
`115.50 × 1.05 × 1.05 = 127.33875` → 127.339 to three decimals; the
prose "127.34" is the same value to two decimals.

**Verdict:** OK; text and table are the same number to different
precision. Non-issue.

verification-plan.md §2 asserts floors within ±0.001, which is stricter
than the two-decimal prose but consistent with the three-decimal table.
OK.

## 4. Timezone & scheduler

| Item | timezone-audit.md | decisions.md | architecture/overview.md | Verdict |
|---|---|---|---|---|
| TZ = America/Chicago, DST-aware | ✅ | D-0005, D-0020 | §5 ✅ | OK |
| Market open anchor 08:30 CT | ✅ | D-0021 | §5 conceptual list mentions 8:30 CT | OK |
| Pre-market anchor 07:00 CT | ✅ | D-0021 | §5 conceptual list mentions 7:00 CT | OK |
| Midday 11:00 conceptual routine | not present | none | §5 says "midday 11:00 CT" | **STALE** — see §A3 |
| End-of-day routine | daily-summary at 14:55 CT | — | §5 says "End of day" without a time | **STALE** — see §A3 |
| Trigger runtime TZ support | ✅ §4 (no TZ field; UTC-only) | D-0020 | (silent) | OK; audit is the authoritative source |

### A3 — STALE: architecture/overview.md §5 conceptual routine list

The overview's §5 lists "Pre-market 7:00 CT / Market open 8:30 CT /
Midday 11:00 CT / End of day" as *conceptual, subject to Controller
approval*. The live set is different: pre-market 07:00 (Capitol
Trades), monitor 08:30 hourly through 14:30, daily summary 14:55. No
midday-11:00 routine is planned or live; midday reassessment happens
via the 11:30 monitor pass. No dedicated EOD ladder routine exists;
the wheel-daily-summary is TSLA-wheel-specific and read-only.

**Not a contradiction** — the section explicitly labels it "conceptual,
subject to Controller approval" — but a reader who skims this file
might expect a midday routine to be planned. Suggest replacing the
conceptual list with a pointer to `timezone-audit.md` §3 for the
Controller-approved schedule. Non-policy change.

## 5. Research architecture

| Item | research-sources.md | Perplexity design in overview.md | Capitol Trades prompt-proposed | Verdict |
|---|---|---|---|---|
| Independent sources, no auto-merge | ✅ | (silent) | ✅ | OK; overview could cite research-sources.md by name |
| Typed findings FACT/SOURCE/INFERENCE/HYPOTHESIS/RECOMMENDATION | ✅ §1, §3 | (silent in overview) | ✅ §4, §6 | OK |
| Never submits trades | ✅ §4 | ✅ §3 | ✅ §Hard prohibitions | OK |
| Broken-parser CRITICAL alarm | ✅ §5 | (silent) | ✅ §2 | OK |
| Perplexity operations named | (implied) | ✅ §3 (`researchMarket`, etc.) | ✅ §6 (`researchStock`) | OK |
| Research log entry schema | (implicit) | `docs/trading/research-log.md` header | ✅ §4 emits log entries | OK |

Nothing contradictory. Minor GAP: `architecture/overview.md` still
doesn't list `research-sources.md` in its docs pointer list. Cosmetic.

## 6. Credential migration

| Item | credential-migration-plan.md | CLAUDE.md | .env.example | Live prompts (redacted) | Verdict |
|---|---|---|---|---|---|
| Env-only, never committed | ✅ | §8 ✅ | ✅ | ✅ (placeholders) | OK |
| Rotation of Alpaca / Perplexity / Telegram | ✅ | (implied) | ✅ names | (n/a) | OK |
| Live-trigger cleanup patterns | ✅ (2 acceptable, 1 unacceptable) | (silent) | (n/a) | (waiting on rotation) | OK |
| Repo grep sweep | ✅ | (implied by §6) | (n/a) | (n/a) | OK |

Ran the sweep again at consistency-review time:
`git grep -E 'PKKV|SWeJa|pplx-|8892400776'` — no matches. Confirmed
clean.

## 7. Verification plan

| Item | verification-plan.md | Elsewhere | Verdict |
|---|---|---|---|
| Static prompt review | ✅ §1 | — | OK |
| Grep for secrets | ✅ §1 | credential-migration §4 | Consistent |
| Deterministic engine assertions | ✅ §2 | strategy.md §5 example numbers | Consistent (±0.001 tolerance) |
| Broker dry run | ✅ §3 | execution.md §6 | Consistent |
| Approval loop | ✅ §4 | execution.md §3, prompt §9 | Consistent |
| Research-only checks | ✅ §5 | research-sources.md §4–5 | Consistent |
| Timezone tests | ✅ §6 | timezone-audit.md §4 | Consistent |
| Rollback | ✅ §7 | — | Consistent |
| Success criteria | ✅ §8 | — | Consistent |

## 8. CLAUDE.md ↔ docs tree

CLAUDE.md §7 lists the persistent-knowledge files. It was written before
these newer docs were added:

- `docs/architecture/state-management.md` (new)
- `docs/architecture/research-sources.md` (new)
- `docs/trading/timezone-audit.md` (new)
- `docs/trading/credential-migration-plan.md` (new)
- `docs/trading/verification-plan.md` (new)
- `docs/trading/routine-policy-alignment.md` (new)
- `docs/trading/consistency-review-2026-09-14.md` (this file)

**Not a contradiction** (CLAUDE.md tells Claude where to look; the tree
is discoverable), but the pointer list is out of date. Suggest adding
the new files to the list in a followup edit. Non-policy change.

## 9. TBDs — still deferred (per Controller directive)

- **D-0011** trigger debounce — TBD, do not invent
- **D-0013** trading universe — TBD, do not invent
- Language / runtime + persistent state medium + scheduler — implicit
  in D-0018 / D-0020 / this doc's §4, still TBD

All three are surfaced but not filled in. OK.

---

## Summary — is APPLY safe?

- The **substantive policy is internally consistent** across strategy,
  execution, state-management, the proposed prompt, research
  architecture, the timezone audit, credential migration, and the
  verification plan. Every approved decision has one and only one
  authoritative implementation instruction.
- The **remaining findings** are wording ambiguities and stale examples
  in older docs. None change what the corrected routine will do. None
  create risk under APPLY.
- Recommend the Controller either
  - (a) issue small clarification edits to `strategy.md` §5 Activation
    wording, `execution.md` §3 Proposal-contents "current price"
    wording, `architecture/overview.md` §5 stale routines list, and
    `CLAUDE.md` §7 file list, before APPLY; OR
  - (b) accept these findings as documented ambiguities, treat the
    later decisions as controlling, and proceed to APPLY.

I recommend (a) for cleanliness, but neither blocks safety. No policy
change is required either way.
