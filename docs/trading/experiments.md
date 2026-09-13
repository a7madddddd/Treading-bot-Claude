# Experiments

Ideas do not become policy directly. They become experiments. Only the
Controller promotes an experiment to policy (via a new APPROVED entry in
`decisions.md`).

---

## Experiment template

```
ID:            E-YYYY-NNN
Title:         short name
Status:        PROPOSED / APPROVED / RUNNING / PASSED / FAILED / REJECTED / ARCHIVED
Hypothesis:    what we expect to be true and why
Baseline:      what we compare against
Change:        what is being varied
Dataset:       universe + source
Time period:   in-sample / out-of-sample split
Metrics:       which ones from backtesting.md matter here
Expected:      what result would count as success
Actual:        filled in when run
Conclusion:    filled in when analyzed
Next action:   promote / iterate / discard
Owner:         Controller / Claude / joint
```

## Rules

- A new idea starts as `PROPOSED`. It runs only after Controller sets it
  `APPROVED`.
- `PASSED` in a backtest does **not** change policy. Only a Controller
  decision in `decisions.md` does that.
- Never edit history — supersede.
- Every non-trivial change to trading behavior must go through an
  experiment first, unless the Controller explicitly waives it.

## Log

_(none yet)_
