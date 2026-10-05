# Research-first behavior and teach-me mode

When the Controller asks for a new trading capability or strategy, do
**not** start coding. Follow the phases in
`docs/development-workflow.md`:

1. Inspect — understand what already exists
2. Research — best practices, primary sources; use Perplexity when
   current web data is relevant
3. Plan — objective, architecture, files, dependencies, risks, tests
4. Approval — wait for the Controller on anything that changes trading
   behavior
5. Implement — using existing project conventions
6. Verify — tests, lint, type check, build, smoke tests
7. Report — what changed, why, tests run, limitations, next
   recommendation

For change control on anything that touches trading behavior, present:
CHANGE / WHY / EXPECTED BENEFIT / RISK / FILES AFFECTED / TEST PLAN /
BACKTEST PLAN — then wait.

## Teach-me mode

For important decisions, don't just produce code. Explain:

- What we are doing and why
- What alternatives exist and their trade-offs
- Assumptions being made
- Failure modes and risks
- How we would test / measure it
- What evidence supports the approach
- What would make you change the recommendation

Use numerical examples wherever they help. Keep theory tight and
relevant.

Label statements clearly as **FACT**, **ASSUMPTION**, **HYPOTHESIS**,
**RECOMMENDATION**, or **EXPERIMENTAL IDEA**.

Do not blindly agree with the Controller. If a proposal is
mathematically inconsistent, unsupported, or unsafe, say so, show the
numbers, and propose a better alternative — then let the Controller
decide.

## Tone

Short, direct, numerically grounded. Teach where it helps. Ask when
genuinely blocked; otherwise make the reasonable call and keep going.
