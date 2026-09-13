# Development Workflow

Every significant task follows these phases. Referenced from `CLAUDE.md` §3.

---

## Phase 1 — Inspect

- Read the repo before proposing changes.
- Identify existing patterns, files, integrations.
- Do not scaffold a new application on top of existing conventions.

## Phase 2 — Research

- Use Perplexity when current web data is relevant.
- Prefer primary sources (official docs, papers).
- Record findings in `docs/trading/research-log.md` when material.

## Phase 3 — Plan

Present:

- Objective
- Architecture
- Files to change
- Dependencies (only what's needed)
- Data flow
- Risks
- Testing strategy

## Phase 4 — Approval

For any change that affects trading behavior — entries, exits, sizing,
ladders, floor, trailing, risk limits, execution — wait for Controller
approval and record it in `docs/trading/decisions.md`.

Bug fixes that do **not** change trading behavior may proceed under normal
project conventions.

### Change-control brief (required for trading-behavior changes)

```
CHANGE:
WHY:
EXPECTED BENEFIT:
RISK:
FILES AFFECTED:
TEST PLAN:
BACKTEST PLAN:
```

## Phase 5 — Implement

- Use existing project patterns.
- Small, reviewable commits.
- No dead code, no speculative abstractions.

## Phase 6 — Verify

- Unit tests
- Lint
- Type check
- Build
- Relevant integration tests
- Smoke tests

## Phase 7 — Report

- What changed
- Why
- Files changed
- Tests run and results
- Known limitations
- Next recommendation
