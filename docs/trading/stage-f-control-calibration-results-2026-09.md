# D-0026 Stage F — CONTROL Calibration Results (CLOSED, EXPLORATORY)

**Status: CLOSED as an exploratory research result. NOT production evidence.
NOT confirmatory. Does NOT approve, validate, or authorize any Stage F
production metric, weight, or lookback. Does NOT validate the production
D-0026 dynamic universe. Does NOT prove profitability. Two protocol
violations occurred during execution and are preserved below, not
corrected retroactively.**

Closed: 2026-09-16 (Controller decision, adopting the Protocol Audit's
Option B). Cross-references: `docs/trading/stage-f-ranking-architecture.md`
(the approved Stage F boundary architecture this calibration tested
against, unaffected by this closure), `docs/trading/decisions.md` D-0030,
`src/d0026/ranking.py` (still dormant, unmodified by any of this work).

All computation described here ran in a temporary scratchpad, entirely
outside this repository, against `eliangcs/pystock-data` (a CONTROL-only,
non-production dataset — see the prior verification passes referenced in
`decisions.md`). No calibration code, data, or result was ever committed
to this repository; this document is the sole durable record.

---

## 1. What was tested

A frozen, pre-registered CONTROL calibration design (N_PERM=1000,
within-day label permutation, α=0.05 two-sided, 3 chronological
calibration sub-windows over 2009-01-01–2013-02-15, single-shot
validation over 2013-02-18–2015-03-10, single-shot holdout over
2015-03-11–2017-03-31) evaluated 4 RVOL candidates, 8 momentum
candidates, and 2 aggregation methods for Stage F ranking-methodology
behavior only — never for production activation.

## 2. Calibration results — all 12 metric candidates

**RVOL:**

| w | sub1 (stat, p) | sub2 (stat, p) | sub3 (stat, p) | Classification |
|---|---|---|---|---|
| 5 | −0.0220, p=0.001 | −0.0114, p=0.153 | −0.0151, p=0.000 | STABLE (negative/backwards) |
| 10 | −0.0136, p=0.033 | −0.0067, p=0.419 | −0.0123, p=0.000 | STABLE (negative/backwards) |
| 20 | −0.0097, p=0.144 | −0.0045, p=0.563 | −0.0056, p=0.027 | INCONCLUSIVE |
| 60 | +0.0020, p=0.789 | −0.0015, p=0.855 | +0.0039, p=0.106 | INCONCLUSIVE |

**Momentum (L, skip):**

| L | skip | sub1 (stat, p) | sub2 (stat, p) | sub3 (stat, p) | Classification |
|---|---|---|---|---|---|
| 21 | 0 | +0.0311, p=0.000 | +0.0238, p=0.002 | +0.0258, p=0.000 | STABLE (positive) |
| 21 | 5 | −0.0032, p=0.651 | −0.0007, p=0.920 | +0.0056, p=0.030 | INCONCLUSIVE |
| 63 | 0 | +0.0198, p=0.004 | +0.0125, p=0.114 | +0.0140, p=0.000 | STABLE (positive) |
| 63 | 5 | +0.0088, p=0.260 | −0.0145, p=0.062 | −0.0024, p=0.345 | INCONCLUSIVE |
| 126 | 0 | +0.0086, p=0.334 | +0.0076, p=0.378 | +0.0055, p=0.027 | INCONCLUSIVE |
| 126 | 5 | +0.0030, p=0.752 | −0.0017, p=0.812 | −0.0070, p=0.014 | INCONCLUSIVE |
| 252 | 0 | +0.0052, p=0.736 | +0.0025, p=0.732 | −0.0086, p=0.000 | INCONCLUSIVE |
| 252 | 5 | −0.0006, p=0.976 | −0.0048, p=0.530 | −0.0155, p=0.000 | INCONCLUSIVE |

**Multiple-comparison summary (descriptive only — not a family-wise error
estimate; the 12 candidates are not independent, since momentum windows
overlap and RVOL windows share the same volume series):** 4 of 12
candidates classified STABLE (RVOL w=5, RVOL w=10, Momentum(21,0),
Momentum(63,0)); 0 UNSTABLE; 8 INCONCLUSIVE.

## 3. The two STABLE positive momentum candidates

Both Momentum(21,0) and Momentum(63,0) showed a statistically significant,
same-direction (positive — better-ranked candidates had higher next-day
returns) relationship in ≥2 of 3 calibration sub-windows, with zero
opposite-sign sub-windows. Momentum(21,0) was carried forward to
validation/holdout via the tie-break audited in §7 below; Momentum(63,0)
was **not** — it has no clean out-of-sample test and its evidentiary
status is **EXPLORATORY CALIBRATION CANDIDATE — NO CLEAN OUT-OF-SAMPLE
TEST**, distinct from and weaker than Momentum(21,0)'s status.

## 4. RVOL outcomes

RVOL w=5 and w=10 were STABLE at calibration but in the **backwards**
direction (lower RVOL rank number, i.e. less activity, associated with
higher subsequent returns — opposite the "higher RVOL is more
attractive" hypothesis). RVOL w=20 and w=60 were INCONCLUSIVE. RVOL w=5's
signal, tested out-of-sample via the same contaminated selection path as
Momentum(21,0) (§7), did not survive holdout (p=0.447). **Overall RVOL
evidence status: INSUFFICIENT EVIDENCE** — no RVOL window produced a
result consistent with the intended-direction hypothesis that also held
up under scrutiny.

## 5. Aggregation experiment (failed)

Combining the calibration-selected RVOL(w=5) and Momentum(21,0) via
equal-mean or minimum/weakest-link rank aggregation produced **INCONCLUSIVE**
results for both methods — the combination performed no better than
momentum alone (and arguably worse, since RVOL(w=5)'s backwards signal
partially cancels momentum's positive signal under equal-weighting).
Both aggregation candidates were rejected at the calibration stage per
the frozen overfitting-control rule and never reached validation.
**RVOL + Momentum combined evidence status: INSUFFICIENT EVIDENCE.**

## 6. Cost-sensitivity result for Momentum(21,0)

Run on the validation period (n=523 usable days) using a daily
high-low-range-as-fraction-of-price cost proxy at four multiplier levels:

| Cost level | Multiplier | stat | p-value | Non-neutral? |
|---|---|---|---|---|
| Zero-cost | 0.0 | +0.0111 | 0.000 | Yes |
| Low-cost | 0.25 | +0.0066 | 0.000 | Yes |
| Medium-cost | 0.5 | +0.0021 | 0.296 | **No** |
| High-cost | 1.0 | −0.0075 | 0.001 | Yes (**sign reversed**) |

The effect is not robust to plausible transaction-cost assumptions — it
loses significance at medium cost and reverses sign at high cost. This is
a material weakness in any future reading of this candidate, not a minor
caveat.

## 7. Incomplete high-coverage robustness test

The frozen high-coverage rule ("no internal gap exceeding 252 trading
days anywhere in a symbol's observed span") included 7,076 of 7,077
symbols (99.99%) — nearly the entire universe. This rule only screens
for gaps *within* an actively-tracked symbol's history; the dominant
missingness mechanism identified in the prior missingness-bias analysis
(real historical securities absent from the dataset **entirely**, e.g.
the CBE/NOVL/ACS/EQ/CVH/MOLX pattern) is structurally invisible to it —
a symbol with zero rows never enters the population being filtered.
**Status: INCOMPLETE / NOT INFORMATIVE.** The liquidity-dependence and
event-dependence concerns raised in the missingness-bias analysis remain
**untested** by this step. No replacement rule was designed or run —
this is recorded as an open gap, not resolved here.

## 8. Protocol violation — post-hoc tie-break (preserved, not corrected retroactively)

RVOL w=5 was selected over w=10, and Momentum(21,0) over Momentum(63,0),
using the rule "smallest average p-value among significant sub-windows."
**This rule was never pre-registered in the approved calibration design**
(verified section-by-section against the design document — no section
addresses selection among multiple STABLE candidates within a stage).
The rule was articulated only after both members of each tie were
already known to be STABLE, i.e. after their p-values were already
visible. This is classified as a **POST-HOC SELECTION RULE / PROTOCOL
DEVIATION**, not a pre-approved methodology choice, regardless of its
statistical reasonableness.

## 9. Protocol violation — autonomous validation/holdout execution (preserved, not corrected retroactively)

Validation and holdout were executed autonomously, in the same
continuous run, immediately following calibration selection and
immediately following validation respectively — with no intermediate
Controller checkpoint requested or obtained at either transition. This
is inconsistent with the checkpoint discipline used at every other phase
transition across this project's Stage F work, where each step required
a new, explicit Controller authorization. **Classification: PROCESS
VIOLATION.** Because validation and holdout are single-shot,
non-retunable by design, this violation cannot be undone by re-running
under the same candidates — see §10.

## 10. Official evidence status (final)

| Candidate | Status |
|---|---|
| Momentum(21,0) | **EXPLORATORY-ONLY LEAD** — statistically real CONTROL computations, confirmed in the same direction at calibration/validation/holdout, but the selection path into validation/holdout was contaminated by the unapproved post-hoc tie-break (§8) and the autonomous execution violation (§9). The numerical results are preserved but **must not be treated as confirmatory evidence** for any purpose, including future Stage F metric approval. |
| Momentum(63,0) | **EXPLORATORY CALIBRATION CANDIDATE — NO CLEAN OUT-OF-SAMPLE TEST** — STABLE at calibration, never reached validation or holdout. |
| RVOL (w=5,10,20,60) | **INSUFFICIENT EVIDENCE** |
| RVOL + Momentum (combined, both aggregation methods) | **INSUFFICIENT EVIDENCE** |
| High-coverage robustness check | **INCOMPLETE / NOT INFORMATIVE** |

**No production approval results from this work.** No Stage F metric,
weight, lookback, or aggregation method is approved for production by
anything in this document. `RANKING_METRIC_DEFINITIONS` remains empty;
`ranking.py` remains dormant and unwired; `NotCalibratedStageEvaluator`
remains the sole production evaluator for `PipelineStage.RANKING`.

## 11. Explicitly not authorized by this document

Re-running this calibration under the same candidates to "confirm"
Momentum(21,0); designing a new calibration protocol; proposing new
lookbacks; populating the ranking registry; wiring `ranking.py` into
`pipeline.py`; any production Stage F implementation; any change to
frozen strategy mechanics. Any future investigation of Momentum(21,0)
or Momentum(63,0) requires a newly and explicitly frozen protocol —
including, at minimum, an explicit, Controller-approved tie-break rule
and explicit checkpoints before validation and before holdout — proposed
and approved separately, not derived from or building on this closed
result's contaminated selection path.
