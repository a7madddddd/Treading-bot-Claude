# D-0026 Stage F — Ranking Architecture (Approved Boundary, No Metrics Approved)

**Status: Stage F BOUNDARY ARCHITECTURE APPROVED. No ranking metric,
weight, or numeric parameter is approved. No production implementation
exists. Stage F is NOT wired into the pipeline and NOT production
ready. A CONTROL-only, non-production calibration exploration has since
occurred (`stage-f-control-calibration-results-2026-09.md`, D-0030)
and was CLOSED as exploratory-only, with two protocol violations
preserved in that record — it does not change any status on this page
and authorizes no production metric.**

Approved: 2026-09-15 (Controller decision, following a multi-round
design/red-team/consistency-review process conducted entirely in
conversation — no design artifacts were created until this
documentation-only authorization).

Cross-references: `docs/architecture/universe.md` (9-stage pipeline,
engine boundary), `docs/trading/universe-selection-analysis.md`
(third-pass mechanism proposal, §1.F "Opportunity ranking", §3.5
ranking parameter catalog — this document narrows and supersedes
*only* the boundary/shape decisions listed below within that broader,
still-PROPOSED mechanism), `docs/trading/decisions.md` D-0029 (decision
log entry for this approval) and D-0030 (CONTROL calibration exploration,
closed as exploratory-only), `docs/trading/stage-f-control-calibration-results-2026-09.md`
(full CONTROL calibration results and preserved protocol violations),
`src/d0026/ranking.py` (dormant Phase 1
scaffolding — `RankingMetricDefinition`, `RankingMetricId`,
`RANKING_METRIC_DEFINITION_VERSION`, empty
`RANKING_METRIC_DEFINITIONS`, `compute_ranking_score_summary()`,
`build_selected_candidate_entries()` — committed but not wired into
`pipeline.py`), `src/d0026/pipeline.py` (`NotCalibratedStageEvaluator`
remains the sole evaluator for `PipelineStage.RANKING`, unchanged).

---

## 1. Stage F objective — APPROVED NOW

> Stage F assigns a deterministic display/order position to Stage E's
> survivors, based only on a fixed, versioned set of non-eligibility,
> non-strategy secondary characteristics. This order is descriptive
> metadata for Controller review only; it asserts no claim about
> expected return, trade quality, lower risk, execution quality, or
> likelihood of success. The Controller remains the sole
> decision-maker.

Stage F is explicitly **not**: an alpha model, an expected-return
predictor, a risk model, a trading signal, a position-sizing model, an
execution decision, or a strategy-mechanics layer.

## 2. Permanent Stage F exclusions — APPROVED NOW

Stage F must never use as a ranking input: Evidence Quality, Decision
Type, Confidence, Risk, Telegram approval state, Controller approval
state, prior trading outcomes, portfolio performance, prior ranking
history, Stage G outputs, Stage H outputs, or any strategy/ladder
parameter (entry reference, floor, trailing mechanics).

## 3. Regime isolation — `INV-F-REGIME-BLIND` — APPROVED NOW

- Stage F receives `regime_state` only because the generic
  `StageEvaluator.evaluate()` contract (shared unchanged by every
  stage) provides it — receipt is incidental to the interface, not a
  Stage F design choice.
- Stage F must **never** use `regime_state` as a ranking input.
- Stage F must **never** create regime-conditioned ranking logic.
- Stage F must **never** duplicate Stage E's regime-adaptation
  responsibility.
- Calendar/timing logic uses `as_of_date` and the project's existing
  market-calendar/time infrastructure (D-0006, `timezone-audit.md`) —
  never `regime_state`.

## 4. Survivor-pool size — APPROVED NOW

No minimum-N threshold exists or is planned as a fixed architectural
parameter. N=0 is the existing empty-passthrough behavior, unchanged.
N≥1 always produces a deterministic order. No new EMPTY-like condition
is introduced. Pool size may later be disclosed through a future audit
artifact (§19).

## 5. Ranking representation — APPROVED NOW

Stage F's internal mathematical core uses **plain ordinal ranking**
only. It does **not** use CDF percentile, percentile rescaling,
z-score, or robust z-score. No percentage representation is part of
Stage F's core computation (a percentage-style *presentation* layer,
if ever built, would be a separate, later, unapproved concern).

## 6. Stage boundaries — ARCHITECTURAL DIRECTION (reaffirms existing D-0026 design)

| Stage | Owns |
|---|---|
| C | absolute liquidity, spread/execution-quality |
| D | strategy-mechanics fit, candidate volatility compatibility |
| E | regime adaptation |
| **F** | **secondary cross-sectional ordering only** |
| G | concentration/diversification |
| H | Top-N |
| (downstream of H) | Evidence/Confidence — never a ranking input |

## 7. Ranking metrics — PROPOSED / NOT APPROVED

The following remain explicitly unapproved. None may be treated as a
production decision:

- RVOL / relative activity
- momentum / trend
- RVOL + momentum as a final production metric set
- equal-weighting as a validated weighting choice

## 8. Base momentum boundary — ARCHITECTURAL DIRECTION

The base architecture excludes, from the default/initial design:
benchmark-relative momentum, idiosyncratic/benchmark-adjusted
momentum, volatility-scaled ("risk-adjusted") momentum, and
regime-conditioned momentum. These remain **future calibration
experiments only** (§17), never silently merged into the base design.

## 9. Permanent exclusions from Stage F — EXCLUDED FROM STAGE F

Absolute liquidity, standalone volatility/ATR as a ranking factor,
drawdown, time-underwater, downside/path-quality metrics, concentration
logic, sector constraints, Evidence/Confidence, and ML/learning-to-rank
as a standalone approach for the current architecture.

## 10. Tie-break — APPROVED NOW

`security_id` is the deterministic final tie-break, applied only after
aggregation, if candidates still share an equal final ranking value. It
is an identity key, not a ranking metric. Documented caveat: no
identifier can be mathematically proven to carry zero informational
correlation with every conceivable future dataset, but no plausible
Stage F mechanism assigns `security_id` economic meaning.

## 11. Null baseline — ARCHITECTURAL DIRECTION

The mandatory future calibration baseline is **`security_id`-sorted
order**. Stage E's survivor order is explicitly rejected as the
scientific null (its neutrality has never been proven — it may reflect
unverified upstream/provider structure). Random order is also rejected
(violates this project's determinism/reproducibility discipline).

## 12. `score_summary` contract — APPROVED NOW

The existing model contract is preserved exactly:
`SelectedCandidateEntry.score_summary: Tuple[Tuple[str, float], ...]`
(`src/d0026/models.py`, unchanged). No `models.py` change is approved
or planned. Content stays numeric-only — no string, boolean, or
version metadata is encoded into it. A richer audit representation is
deferred to a separate, future, not-yet-designed artifact (§19).

## 13. Missing ranking metric — ARCHITECTURAL DIRECTION

Approved conservative rule: do not reject the candidate; do not
renormalize upward using only available metrics; do not impute
fabricated values; do not use Evidence/Confidence (structurally
unavailable at Stage F's point in the pipeline in any case — Evidence
runs strictly after Stage H). Incomplete candidates are ordered after
all fully-scored candidates. Missing-data disclosure is deferred to a
future audit artifact (§19). Implementation-level detail and
calibration impact remain testable during calibration — this rule's
*shape* is approved; its *implementation* is not authorized.

## 14. Per-metric ties — ARCHITECTURAL DIRECTION

Fractional rank is the approved treatment for equal metric values at
the per-metric level, prior to final aggregation. The `security_id`
tie-break (§10) applies only after aggregation, to the final composite
value.

## 15. Rank aggregation — ARCHITECTURAL DIRECTION

Rank-based aggregation is approved as the architectural direction for
combining per-metric ranks into a single ordering. Equal-weighting
remains explicitly **PROPOSED / NOT APPROVED** — it must never be
presented as a validated decision; it is a testable economic hypothesis
about relative metric importance, not settled architecture.

## 16. Preferred calibration starting point — PROPOSED / NOT APPROVED

The preferred calibration hypothesis is **RVOL + Momentum**, tested
against three mandatory comparators: (1) `security_id` neutral
baseline, (2) RVOL-only, (3) momentum-only. Choosing this as the
starting *hypothesis to test* is itself only a plan, not a production
metric-set approval — it carries the same PROPOSED / NOT APPROVED
status as the RVOL and momentum candidates in §7.

## 17. Planned calibration sequence — ARCHITECTURAL DIRECTION (the sequence/order is agreed; no step has been executed and no numeric value is approved)

1. Neutral `security_id` baseline.
2. RVOL-only vs. baseline.
3. Momentum-only vs. baseline.
4. RVOL/momentum correlation check.
5. Combined RVOL + Momentum vs. both single factors and baseline.
6. Aggregation-method sensitivity.
7. Missing-data sensitivity.
8. Regime/stability analysis.
9. Transaction-cost realism.
10. Benchmark-relative / risk-adjusted momentum variants (optional,
    late-stage).

## 18. BLOCKED BY CALIBRATION

RVOL lookback; momentum lookback; RVOL/momentum correlation outcome;
ranking vs. neutral baseline outcome; single- vs. multi-factor outcome;
equal-weight vs. alternative-aggregation outcome; risk-adjusted
momentum adoption; benchmark-relative momentum adoption; empirical
factor pruning; any numeric threshold of any kind. Historical D-0026
calibration itself remains BLOCKED overall (`pre-apply-checklist.md`
B16; `historical-data-calibration-plan.md` §22) — nothing in this
document changes that.

## 19. Future audit artifact — NEEDS DESIGN CLARIFICATION

Not created, named, or implemented. Expected (not yet approved) to
carry richer structured ranking metadata separate from `score_summary`:
ranking-metric-definition version, per-candidate metric
availability/missing status, survivor-pool size, aggregation-method
label, and source references. Exact schema remains open — mirrors the
precedent of `EvidenceSummary` existing as a separate artifact
alongside `EvidenceClassification.fired_conditions` (`src/d0026/evidence.py`).

---

## What this document does NOT authorize

- No Python code change of any kind.
- No population of `RANKING_METRIC_DEFINITIONS`.
- No wiring of `ranking.py` into `pipeline.py`.
- No change to `NotCalibratedStageEvaluator`, which remains the sole,
  unmodified evaluator for `PipelineStage.RANKING`.
- No change to Stage G, Stage H, or the Evidence/Confidence Layer.
- No historical-data acquisition or calibration execution.
- No change to frozen strategy mechanics (entry, ladder, floor,
  trailing).
- No claim that Stage F is implemented, tested against real data, or
  production-ready. It is not.
