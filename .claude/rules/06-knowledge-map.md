# Where the project's knowledge lives

Claude does not permanently "learn" between sessions. Persistent
knowledge lives in the repo. When new research materially changes
understanding, add a dated entry to `research-log.md` with source,
conclusion, whether it is approved or experimental, and what changed.
**Never silently rewrite approved decisions — append to `decisions.md`
instead.**

## Trading policy and process

- `docs/trading/strategy.md` — approved trading policy (frozen; changes need approval)
- `docs/trading/execution.md` — execution and approval workflow
- `docs/trading/risk-management.md` — risk calculation approach
- `docs/trading/backtesting.md` — backtesting standards
- `docs/trading/decisions.md` — decision log (append-only history)
- `docs/trading/pending-approvals.md` — the live open board
- `docs/trading/experiments.md` — experiment template and log
- `docs/trading/research-log.md` — research summaries with sources
- `docs/trading/glossary.md` — key terms
- `docs/development-workflow.md` — the 7-phase workflow
- `docs/trading/pre-apply-checklist.md` — what must happen before APPLY
- `docs/trading/verification-plan.md` — pre-APPLY test plan

## Architecture

- `docs/architecture/overview.md` — skills, pipeline, routines, notifications, logging
- `docs/architecture/state-management.md` — deterministic recoverable state contract (D-0018)
- `docs/architecture/research-sources.md` — Perplexity + Capitol Trades research architecture (D-0019)
- `docs/architecture/universe.md` — dynamic, symbol-agnostic universe boundary (D-0026)
- `docs/architecture/telegram-approval.md` — Telegram approval transport (D-0025)
- `deploy/README.md` — the Oracle VM units, timers and their non-default settings

## Scheduling, timezones and credentials

- `docs/trading/routine-policy-alignment.md` — per-routine status vs approved policy
- `docs/trading/timezone-audit.md` — approved CT schedule and UTC-drift audit
- `docs/trading/scheduler-design.md` — TZ-aware scheduling design (DST-safe)
- `docs/trading/credential-migration-plan.md` — rotation and cleanup for leaked keys

## Analyses behind specific decisions

- `docs/trading/debounce-analysis.md` — D-0011 (APPROVED policy)
- `docs/trading/consistency-review-2026-09-14.md` — pre-APPLY cross-doc review
- `docs/trading/universe-selection-analysis.md` — D-0026 mechanism, first pass (PROPOSED)
- `docs/trading/universe-parameter-validation.md` — D-0026 parameters, second pass (PROPOSED)
- `docs/trading/historical-data-calibration-plan.md` — D-0026 data acquisition + calibration (PROPOSED)

## Data-source research (D-0026 Phase 2)

- `docs/trading/root-data-source-recommendation.md` — Norgate Data — **REJECTED/SUPERSEDED**, no paid provider will be used; retained for its research/design-pattern value only
- `docs/trading/free-data-verification-pass.md` — final legal/data-gap verification; Stooq terms UNCLEAR, Yahoo downgraded; survivorship-bias decomposition (PROPOSED)
- `docs/trading/free-root-data-source-recommendation.md` — free-only recommendation, coverage matrix, execution plan (PROPOSED; Controller-approved direction)
- `docs/trading/data-acquisition-pilot.md` — pilot design; **could not execute** — this environment's egress policy blocks every approved source (verified by real curl tests)
- `docs/trading/github-native-data-sources.md` — GitHub-reachable sources, empirically verified by real clones and fetches

## Live account routines

- `routines/README.md` — index of live Routines snapshotted into the repo
- `routines/<slug>/prompt.md` — redacted snapshot of the live prompt
- `routines/<slug>/prompt-proposed.md` — draft corrected prompt for review
- `routines/<slug>/metadata.md` — trigger metadata and policy-alignment notes

## Claude's own working record

- `docs/claude/failure-history.md` — the real incidents behind the rules
- `docs/claude-md-ORIGINAL-2026-10-05.md` — the single-file CLAUDE.md as it stood before the split, kept verbatim
