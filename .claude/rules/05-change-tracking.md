# Change tracking — MANDATORY

**Controller instruction, 2026-10-05:** every time anything is created,
updated, fixed, deleted, enabled, or disabled, the written record MUST
be updated in the SAME working session as the change — never "later",
never "at the end of the day". The purpose: at any moment the Controller
can open the repo and know exactly where we stopped, what is done, what
is broken, and what is next.

## The three files that must stay current

1. **`docs/trading/decisions.md`** — append a new `D-NNNN` entry for
   anything that changes trading behavior, execution, risk, schema,
   architecture, or an approved contract. **Append-only:** never rewrite
   or delete an earlier entry; supersede it with a new numbered entry
   that names the one it replaces.
2. **`CLAUDE.md`** and **`.claude/rules/`** — update when a *rule about
   how Claude works* changes: a new mandatory behavior, a new
   session-start step, a new process requirement, a new prohibition.
3. **`docs/trading/pending-approvals.md`** — the live "where did we
   stop" board. Every open item gets a `P-NNN` row with its status. When
   an item closes, mark it RESOLVED with the date and the
   decision/commit that closed it; do not delete the row.

## Minimum content of an update

- WHAT changed — file paths and the concrete behavior.
- WHY — the problem it solves, with a number or a concrete example.
- STATUS — done / partially done / blocked, and if blocked, on what.
- TESTS — what was run and the result.
- NEXT — the single next step, so a fresh session resumes with no
  guessing.

## Ordering rule

A commit that changes code but leaves these files stale is an
**incomplete commit**. Code and record go in the same commit wherever
possible; when the record is a separate commit, it is pushed in the same
session, never deferred to the next one.

## Push policy (Controller-approved, 2026-10-05)

**Push WITHOUT asking** — commands the Controller gave, decision-log
entries, `pending-approvals.md` updates, documentation, and test-only
changes. Recorded and pushed in the same session.

**NEVER push before explicit Controller approval** — anything that
changes, or could change, trading strategy or trading behavior: entries,
exits, sizing, ladder levels, floor, trailing, risk limits, execution
behavior, candidate selection, scorer weights, filters, or schedules
that gate trading. Present the design in Arabic, wait, then implement.

When a change is partly both, **the trading-behavior part decides**:
hold the whole commit until approved.

## Prove it before it enters the repo (Controller rule, 2026-10-05)

**The Controller's reasoning, in his words:** pushing something unproven
means that when the result is bad, the cost is a whole cycle — pull the
repo, remove the change, restore the old code, push again, then pull
again on the VM. The way to avoid that cycle is not to enter it.

So: **experimental and exploratory work is tested FIRST, in Claude's own
environment, and reaches the repository only after its result is seen
and the Controller decides it is worth keeping.**

Applies to measurement scripts, research tooling, spike implementations,
and anything built to answer a question rather than to serve the running
system.

Does NOT apply to:
- a fix for a defect already diagnosed and agreed,
- the decision log and `pending-approvals.md`, which are the record and
  must stay current,
- anything the Controller explicitly asked to be pushed.

**Never commit an experiment to the working branch — not even
locally.** An experiment stays EITHER uncommitted in the working tree,
OR on a separate throwaway branch that is never pushed. A commit on the
working branch is a push waiting to happen, because the next legitimate
push carries it. Treat "I will just commit it locally" as the trap it
is.

**What matters is the finding, not the tool.** When an experiment
answers its question, the ANSWER belongs in the docs even if the code
that produced it is discarded.

## Git and branches

- Development branches are specified per session (currently
  `claude/youthful-goodall-4cr0ei`)
- Commit with clear messages; push to the designated branch
- Do not open a pull request unless the Controller asks
- Do not modify `docs/trading/strategy.md`, `docs/trading/execution.md`,
  or any risk/execution behavior without Controller approval recorded in
  `docs/trading/decisions.md`

Why the "prove it first" and "never commit an experiment" rules exist,
with the real incident behind them: `docs/claude/failure-history.md`
