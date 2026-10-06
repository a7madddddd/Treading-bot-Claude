# Why the rules in `.claude/rules/` exist

Each rule in this project was written after something actually went
wrong. The rules themselves are short and imperative; this file keeps
the incidents, so the rules stay readable without losing the reasoning.

This file is **reference material, not an instruction**. Nothing here
overrides or adds to a rule.

---

## Behind rule 0.b — "understand before suggesting"

**2026-10-03.** Claude spent about two hours building and backtesting
four separate "research" rankers — Momentum, Mean Reversion, Pullback,
Breakout — without checking `decisions.md`.

The entire time, **D-0048 (approved 2026-09-27)** already specified the
production ranker — 40% Momentum + 30% Quality + 30% Liquidity — with a
full 8-stage pipeline, an implementation in `src/d0026/stages/`, and 233
passing tests.

All of that time was wasted, and worse: the alternative rankers were
tested on a hardcoded 12-symbol universe instead of the real dynamic
universe D-0048 operates on, so the results were not even comparable.

**2026-10-05.** The same failure in a smaller form, repeatedly: Claude
proposed first and searched afterwards. In one case the search, run
after the proposal, found that `Trade` already carried four
protective-order fields and an `update_protective_order` method with
cancel-and-replace lineage for a feature being discussed as if it were
new.

---

## Behind rule 0.c — teacher-grade recommendations

**2026-10-02 and 2026-10-03.** Three blocking items went unsurfaced for
multiple sessions:

- **P-001** — the D-0026 Universe was not approved
- **P-002** — the ranker scorer was still Momentum in production code
- **P-003** — legacy TSLA Routines were still running

The Controller spent time testing a ranker without knowing the
production pipeline was not connected to it.

---

## Behind rule 0.d — re-challenge approved decisions

**2026-10-03.** The fixed 10/10/20 share quantity (Controller-approved,
D-0004 §1) was never flagged by Claude, even though under the new
dynamic Universe (D-0026) the same strategy applied to WBD (~$12) and
QQQ (~$750) produces a **60× difference in dollar exposure for identical
nominal risk** — a real-money loss waiting to happen.

The Controller had to ask directly before Claude surfaced it. This
became D-0051 (percentage sizing).

---

## Behind the "never commit an experiment" rule

**2026-10-05.** The first version of this rule said a local commit "is
not a push". True in isolation, and wrong in practice.

Claude proved it within minutes: it committed an experimental
measurement tool locally, then committed an unrelated docs change on
top, then pushed — and `git push` sends the branch's whole history, so
the experiment went with it. The Controller got exactly the thing he had
asked to be kept out.

The rule was rewritten the same day: an experiment stays **uncommitted
in the working tree**, or on a **separate throwaway branch that is never
pushed**.

---

## Behind the "prove it before it enters the repo" rule

**2026-10-05, in the Controller's words:** pushing something unproven
means that when the result is bad, the cost is a whole cycle — pull the
repo, remove the change, restore the old code, push again, then pull
again on the VM. The way to avoid that cycle is not to enter it.

The same day the rule paid for itself twice:

1. A first implementation of a share-count derivation was run before
   being committed and produced **2 shares instead of 22** — it would
   have made the bug it was fixing ten times worse.
2. Extending that fix to Ladder 2 made a confirmed partial fill count
   **30 shares instead of 20** — a double count, caught by the existing
   tests before the change left the working tree.

---

## Behind the precision rule in the communication protocol

**2026-10-05.** A series of claims stated before verification, each
corrected afterwards:

- "The universe run is still running now" — it had been dead for 1h50m.
- "It was killed by an out-of-memory condition" — the VM had 19 GB free;
  it was a SIGTERM we ourselves had sent.
- "The missing preflight line means the engine runs old code" — that
  line is sent to Telegram, not to stdout, so its absence proved
  nothing.
- "A partial INITIAL entry strands shares the same way" — it does not;
  `freeze_initial_reference` branches on `filled_shares`, not on the
  order status label, so the position is protected.
- "Every later percentage is computed from a stale base" — overbroad.
  The ladder triggers and the original floor are frozen and unaffected;
  only the trailing-floor activation threshold is affected.

---

## Behind the change-tracking rule

**2026-10-05.** The Controller asked why no proposal had arrived all
day. Tracing it took most of a session and uncovered that a capped
40-candidate test run had written the day's production universe snapshot
with one symbol, and the engine had traded that one-symbol universe for
an entire session with nothing reporting it (P-035).

Nothing in the written record would have told a fresh session any of
this. The three files named in rule 5 exist so that it would.

## 2026-10-06 — Described the system's behavior without reading the code, and cost the Controller ten minutes

**What happened.** The Controller asked what changes between a
5,000-symbol market and a 14,000-symbol one. Claude answered with a
table whose last column read `trades/day: 3 | 3 | 3 | 3`, presented as
the system's behavior. The Controller then spent ten minutes reasoning
that a fixed 3 was illogical and that the count should respond to market
size, and asked for Claude's opinion on fixing it.

**The truth, already in the code.** `src/engine/engine.py::_check_watchlist`
filters to `soft_score >= self._MIN_SCORE` and then slices `[:top_n]`.
`top_n` is a CEILING. A day where nothing clears the score bar produces
zero proposals and a "nothing to trade" notification; one symbol
produces one; two produce two. The behavior the Controller was asking
for on the downside had been implemented and approved long before.

**What the table should have said.** `up to 3`.

**Why it mattered.** Not the wrong cell. The Controller designed a
solution to a solved problem because Claude's description of the current
system was asserted from memory rather than read from the code. Claude
only verified the real behavior after being told something was missing.

**The Controller's words:** *"the instruction of this project is to read
the all related code and logic for any part you want to update but you
didn't do that before anything."*

**Rule added:** `.claude/rules/00-session-start.md` §0.b.1 — never
describe, tabulate or diagram system behavior without reading that code
path in the same turn; a table cell is a claim about code; a cap is
never written as a fixed value.
