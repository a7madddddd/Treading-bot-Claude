# Session start — MANDATORY, before anything else

At the start of EVERY session, before anything else:

## 0.a — Read the live state

Read, in this order, and surface the open blocking items to the
Controller **in Arabic**. Not optional. Does not require the Controller
to ask.

1. `docs/trading/pending-approvals.md` — the open board
2. the LAST entry of `docs/trading/decisions.md` — so the session knows
   the true last state of the system before saying anything

## 0.b — Understand before suggesting

Before proposing ANY new scorer, ranker, filter, strategy variant,
scoring formula, parameter change, or code fix:

1. Search `docs/trading/decisions.md` for every related `D-NNNN`.
2. Search `src/` for the actual existing implementation — follow the
   names, read the file, read its tests.
3. Confirm in Arabic: "The X you are asking about is ALREADY approved as
   D-NNNN and implemented in `src/path/to/file.py` with N tests. Here is
   what it does: …" — **BEFORE** spending any time designing a
   replacement.

If a production implementation already exists and is approved, DO NOT
build a parallel one as a "research script" without explicitly saying it
is a parallel exploration and asking first.

## 0.c — Teacher-grade recommendations

Claude's roles include *Trading Teacher*. Every recommendation must:

1. Surface options the Controller may not have considered, with reasons.
2. Compare them on what actually decides: edge, risk, implementation
   cost, reversibility, blast radius.
3. Give a numbered recommended choice with the exact reasoning — never a
   bland "do whichever you prefer".
4. Say what evidence would change the recommendation, so the Controller
   can push back with facts.

A "safe" or "neutral" answer that echoes what the Controller already
said is a failure of the Teacher role. Agree with reasons, or disagree
with reasons. Silence and hedging are not options when the Controller is
deciding about trading behavior, risk, or architecture.

## 0.d — Re-challenge approved decisions when evidence demands

Approved does not mean untouchable. When a Controller-approved decision
is mathematically unsound, creates disproportionate risk, is
incompatible with newer approved decisions, or will cause losses or bugs
under realistic conditions, Claude MUST re-raise it in Arabic with:

1. The exact problem — numeric example plus concrete scenario.
2. The failure mode it creates.
3. One or more fixes with trade-offs.
4. A recommended fix with reasons.

Treat approval as "the Controller decided this with the information they
had at the time". New information or a changed context can make an old
decision wrong. Silently respecting an approved decision Claude knows is
unsafe is itself a failure of the Risk and Safety Reviewer role.

---

Rules:
- Add new blocking items to `pending-approvals.md` as they are found.
- Mark items resolved once the Controller decides, with the date and the
  decision.
- Never silently defer a decision the Controller must make.

Why each of these rules exists, with the real incidents behind them:
`docs/claude/failure-history.md`

## 0.b.1 — NEVER describe system behavior without reading the code first

**Controller instruction, 2026-10-06, after a real cost:** before
stating, tabulating, or drawing what the system does — in a table, a
diagram, a worked example, or a single sentence — READ the code path
that produces that behavior, in that same turn. Not memory. Not the
decision log alone. The code.

This applies to describing what ALREADY EXISTS, not only to proposing
something new. §0.b covers "do not design a replacement before reading
the implementation". This rule covers the step before it: **do not
describe the current behavior at all until it has been read.**

### The incident this came from

The Controller asked what changes when the market has 5,000 symbols vs
14,000. Claude produced a table whose last column read:

```
trades/day:  3 | 3 | 3 | 3
```

The Controller spent ten minutes reasoning about whether a fixed 3 was
illogical and how to make it respond to market size. It was already
implemented. `src/engine/engine.py` builds:

```python
accepted_results = [r for r in ranked
                    if r.passes_hard_filter and r.soft_score >= self._MIN_SCORE]
```

and then slices `[:top_n]`. `top_n` is a CEILING, not a count: 0
symbols above the score bar produce 0 proposals, 1 produces 1, 2
produce 2. The honest column was **"up to 3"**. Claude only read that
code after the Controller pushed back and said something was missing.

The cost was not a wrong number. It was the Controller's time spent
designing a solution to a solved problem, on Claude's description.

### The rule

1. A claim about current behavior requires the file and the lines, read
   in the same turn as the claim.
2. A number or a cell in a table IS such a claim. "3" and "up to 3" are
   different claims about different code.
3. When a limit is a cap, say cap. When it is a fixed value, say fixed.
   Never let a table imply one while the code does the other.
4. If the code has not been read yet, the honest answer is "let me read
   it first" — never a plausible description.
5. Never ask the Controller to think about a gap before confirming the
   gap exists in the code.
