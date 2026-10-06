# CLAUDE.md — Persistent Project Instructions

Durable rules for how Claude works on this project, in every session.
Task-specific instructions belong in the message you send; this file and
`.claude/rules/` are only for what holds across all tasks.

Trading policy, architecture and research notes live under `docs/` and
are indexed from `.claude/rules/06-knowledge-map.md` — never duplicated
into the rules.

---

## How these instructions are organized (restructured 2026-10-05)

This file was a single 511-line document. Claude Code's own guidance is
to target **under 200 lines per instruction file**, because longer files
reduce how consistently the instructions are followed.

So the rules now live in `.claude/rules/`, one topic per file. **Every
file there loads automatically into every session** — none has a `paths`
field, and the documented behavior is that rules without one "are loaded
unconditionally and apply to all files". **Nothing was dropped, and
nothing became optional.**

| file | what it governs |
|---|---|
| `.claude/rules/00-session-start.md` | the MANDATORY first steps of every session |
| `.claude/rules/01-roles-and-authority.md` | who decides what; the approval gates |
| `.claude/rules/02-safety-guardrails.md` | the never-negotiable limits; AI vs deterministic; secrets |
| `.claude/rules/03-research-and-workflow.md` | research-first, the 7 phases, teach-me mode, tone |
| `.claude/rules/04-communication.md` | Arabic, the bidi line rule, FACT/ASSUMPTION labels, reporting order |
| `.claude/rules/05-change-tracking.md` | the record, the push policy, prove-it-first, git |
| `.claude/rules/06-knowledge-map.md` | where every document in the repo lives |

Reference material, read on demand and **never** a substitute for a
rule:

- `docs/claude/failure-history.md` — the real incidents each rule came from
- `docs/claude-md-ORIGINAL-2026-10-05.md` — this file as it stood before the split, verbatim

**Where a new rule goes.** A rule that must apply in every session goes
in `.claude/rules/` or here — never in `docs/`, which is not loaded
automatically. Putting a mandatory rule in `docs/` makes it optional in
practice, which is worse than a long file.

---

## The six things that override everything else

If a session reads nothing but this section, these still hold.

**1. The Controller decides. Claude never decides trading behavior.**
No entry, ladder, exit, sizing, risk limit or execution change without
his explicit approval. AI output is advisory, always.

**2. Paper trading only.** No live orders, no real-money execution,
until the Controller changes this in writing.

**3. Start every session by reading the live state.**
`docs/trading/pending-approvals.md`, then the LAST entry of
`docs/trading/decisions.md`. Surface the open blocking items in Arabic,
unasked. Full steps: `.claude/rules/00-session-start.md`.

**4. Read the code before saying what the code does.** This covers two
things, and both have cost real time.

Before SUGGESTING any scorer, filter, strategy variant, parameter or
fix: grep `decisions.md` for the related `D-NNNN`, read the actual
implementation in `src/` and its tests, and say what already exists —
BEFORE designing a replacement.

Before DESCRIBING current behavior — in a sentence, a table cell, a
diagram or a worked example — read that code path in the same turn. A
table cell is a claim about code. A cap is never written as a fixed
value. If it has not been read, the only honest answer is "let me read
it first". Full rule: `.claude/rules/00-session-start.md` §0.b.1.

This rule has been broken more than any other in this project; see
`docs/claude/failure-history.md`.

**5. Record the change in the same session.** `decisions.md` for
anything that changes behavior, `pending-approvals.md` for anything left
open, these instruction files for anything that changes how Claude
works. A commit that changes code and leaves the record stale is an
incomplete commit.

**6. Answer in Arabic, and never mix scripts on one line.** Each line is
either fully Arabic or fully English/code. A technical identifier goes
on its own line right after the Arabic sentence that needs it. Re-scan
every response for this before sending — there is no exception for a
single word.

---

## Push policy, in one place

**Push without asking:** documentation, decision-log entries,
`pending-approvals.md`, commands the Controller gave, and test-only
changes.

**Never push before explicit approval:** anything that changes, or could
change, trading behavior — entries, exits, sizing, ladder levels, floor,
trailing, risk limits, execution, candidate selection, scorer weights,
filters, or schedules that gate trading.

Mixed change → the trading-behavior part decides; hold the whole commit.

**Experiments never enter the repo unproven**, and are never committed
to the working branch even locally — the next legitimate push would
carry them. Details and the incident behind the rule:
`.claude/rules/05-change-tracking.md`.

---

## Current working branch

`claude/youthful-goodall-4cr0ei`

Do not open a pull request unless the Controller asks.
