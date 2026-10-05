# Communication protocol — applies to every session

**Language:** respond to the Controller in Arabic unless he explicitly
asks for English. Keep technical identifiers — class/method/field/file
names, SQL, commands, code — in their original form.

## Formatting rule (bidi safety) — checked on EVERY response

Never mix an Arabic sentence with an inline English/code term on the
same line. This breaks right-to-left rendering in some terminals. Write
each line either fully in Arabic or fully in English/code; when a
technical identifier is needed to explain an Arabic point, list it
separately — its own line or bullet, right after the Arabic explanation,
never embedded inside it.

This applies to every line of output, including inline mentions of a
`variable_name`, a file path, an env var, or a short code fragment
inside an otherwise-Arabic sentence. **No exception for "just one
word."**

- ✗ WRONG: "قيمة `ALPACA_BASE_URL` يجب أن تكون بدون `/v2` في النهاية."
- ✓ CORRECT, two lines, each fully one script:
  "القيمة يجب أن تكون بدون `/v2` في النهاية."
  `ALPACA_BASE_URL = https://paper-api.alpaca.markets`

Before sending any response, re-scan every line for this specific
violation and split any offending line. Mandatory on every response, not
only long ones.

## Structure for any substantial response

1. Start with a short, plain-Arabic summary of the overall result before
   the technical detail.
2. Then explain: what happened, what changed, why, what it means for the
   trading system, what is important, what could go wrong, what remains
   unresolved, and what decision (if any) is needed.
3. Highlight only what materially affects trading behavior, risk/safety,
   data correctness, restart/recovery, broker execution, strategy
   behavior, Controller approval, or architectural boundaries — not
   every minor implementation detail.
4. Every explanation of a technical change, a bug, or a new concept MUST
   include at least one concrete, worked example — an actual value, an
   actual before/after, an actual number. A description with no example
   is **incomplete, not done**. "This could cause an error" is not
   sufficient; show the specific input/output or old value vs new value.

## Precision when describing code behavior

Before stating what a piece of code does — what a function is called
with, what it returns, what a test asserts, what a check verifies —
re-read the exact code path. Never conflate an argument with a return
value, an input type with an output type, or a caller's expectation with
a callee's guarantee.

If a claim about behavior is worth making in a report or a review, it is
worth verifying against the actual code first, even when the answer
feels obvious. Vague or hedged phrasing a reader could reasonably
misinterpret is a **factual error, not a stylistic one**, and must be
corrected explicitly when caught.

## Facts vs recommendations — label distinctly

- **FACT** — directly verified from code, docs, tests, or an
  authoritative source.
- **ASSUMPTION** — something the current design assumes.
- **UNKNOWN** — not yet verified.
- **RECOMMENDATION** — the recommended choice, with a stated reason, its
  trade-offs, and whether it changes the approved trading strategy.
- **CONTROLLER DECISION** — only genuinely new points that materially
  change strategy, execution behavior, architecture, or safety. Anything
  answerable from existing approved strategy, architecture, domain
  invariants, or documented convention is an implementation detail, not
  an escalation.

## Reporting order

**After implementing a change:** what was done, why, the important
points, tests (targeted + full-suite result + regressions + notable edge
cases), problems discovered (including ones already fixed), what was
intentionally deferred, one recommended next step with its reason, and a
Controller decision only if one is genuinely still open.

**Before implementing:** inspect the current code first, re-check
whether a previously approved design still matches it, flag any drift,
present the recommended design in Arabic, name any genuinely new
Controller decision explicitly, and wait for approval before writing
code.

**When relaying a long external/technical report:** summarize its
meaning in Arabic, explain the important parts, state what changed and
what is genuinely still open, give a recommendation with reasons, and
surface only the decisions the Controller actually needs to make — never
paste the raw report and leave him to parse it.
