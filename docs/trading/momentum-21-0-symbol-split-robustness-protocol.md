# Momentum(21,0) — Symbol-Split Robustness Protocol

**Status: FRAMEWORK APPROVED / EXECUTION NOT APPROVED.** The Controller
has approved this protocol's overall structure and governance (§15). The
concrete split parameters (hash algorithm, modulus, split ratio, ticker
normalization rule) remain NOT APPROVED / TBD, and no split generation,
metric computation, or statistical test execution is authorized. No
computation of any kind has occurred under this protocol. No symbol split
has been generated. This document does not modify, supersede, or reopen
`docs/trading/stage-f-ranking-architecture.md`'s approved boundary, does
not populate `RANKING_METRIC_DEFINITIONS`, does not wire `ranking.py`
into `pipeline.py`, and authorizes no production implementation.

Cross-references: `docs/trading/stage-f-ranking-architecture.md` (approved
Stage F boundary, unaffected), `docs/trading/decisions.md` D-0029, D-0030
(Stage F architecture approval; CONTROL calibration closure — this
protocol's historical predecessor), `docs/trading/stage-f-control-calibration-results-2026-09.md`
(the closed, exploratory CONTROL calibration this protocol builds on
without repairing).

**Terminology note:** this protocol's symbol split operates on the
CONTROL dataset's own ticker-string identity (`pystock-data`'s `symbols`
array, as used throughout the original CONTROL calibration run) — this is
**not** the production `security_id` field defined in `src/d0026/models.py`
and referenced in `stage-f-ranking-architecture.md` §10/§11. No production
identity model is touched, tested, or implied by this protocol.

---

## 1. Purpose

Test only whether the already-fixed Momentum(21,0) rank-vs-forward-return
relationship behaves similarly when computed separately on two newly
frozen, disjoint subsets of the CONTROL dataset's symbol population, over
the same historical dates already used in the original CONTROL
calibration. **This is not a new candidate-discovery exercise.** No
candidate grid is scanned, no calibration stage is run, and no new
candidate can emerge from this protocol — Momentum(21,0) is the sole and
entire subject.

## 2. Historical context

- Momentum(21,0)'s original selection over Momentum(63,0) remains
  **contaminated by the documented post-hoc tie-break**
  (`stage-f-control-calibration-results-2026-09.md` §8). This protocol
  does not re-run, replace, or retroactively legitimize that selection.
- The original validation (2013-02-18–2015-03-10) and holdout
  (2015-03-11–2017-03-31) windows are **consumed** — their results remain
  as recorded, labeled EXPLORATORY-ONLY LEAD, and are not reused as input
  to this protocol's design or interpretation.
- **No old result may be treated as new evidence.** Any number produced
  under this protocol is new evidence only for the narrow question this
  protocol asks (§1); it is not additional confirmation of the original
  calibration/validation/holdout chain.
- **This protocol cannot retroactively validate the original
  post-hoc selection process**, regardless of its own outcome. See §12.

## 3. Data boundary

- `pystock-data` (CC BY-SA 4.0) remains the only dataset currently
  verified as reachable from this environment containing real historical
  OHLCV (`github-native-data-sources.md` §1.3).
- Its full date coverage, **2009-01-01 through 2017-03-31 (2,150 trading
  days)**, is **fully and exhaustively consumed** by the original
  calibration (1,075 days) + validation (537 days) + holdout (538 days)
  split — confirmed by direct artifact inspection (`/tmp/date_splits.pkl`),
  with zero remaining unobserved trading date anywhere in the archive.
- **Therefore this protocol deliberately and only tests a symbol-axis
  generalization question** — it cannot, and does not attempt to, test
  generalization across time, regime, or data vendor.
- Explicitly unresolved by this protocol, regardless of outcome:
  **survivorship bias** (confirmed NOT solved — `free-data-verification-pass.md`
  §3D), **point-in-time universe construction** (confirmed NOT solved —
  current-snapshot-only reference data, `github-native-data-sources.md`
  §1.2), **vendor effects** (single-vendor dataset; no cross-vendor
  comparison performed), and **historical-regime limitations** (2009–2017
  only; no coverage of 2018 onward).

## 4. Fixed hypothesis

- **Momentum(21,0) only.** No other candidate is in scope.
- No candidate grid.
- No calibration stage.
- No candidate selection.
- No tie-break of any kind — none is needed, since there is exactly one
  candidate.

## 5. Symbol split

- **Proposed method:** deterministic hash-based split on the CONTROL
  dataset's ticker-string symbol identity (see terminology note above).
- **Proposed ratio:** approximately 50/50, as the default absent a
  specific reason otherwise. **Not derived from any calibration or
  tuning process.**
- **Concrete split parameters — APPROVED (Controller Gate 0, 2026-09-16;
  see §17). Split generation is authorized under these parameters;
  statistical computation of any kind remains NOT authorized.**
  - Symbol identity: CONTROL dataset ticker identity only (see
    terminology note above) — never the production `security_id` field.
  - Normalization: `symbol.strip().upper()`, followed by NFC Unicode
    normalization.
  - Invalid identity (empty/null/whitespace-only after normalization):
    **PROTOCOL ERROR / STOP** — see §16. Not silently excluded.
  - Duplicate normalized identity (two or more distinct source entries
    normalizing to the same identity): **PROTOCOL ERROR / STOP** — see
    §16. Not resolved by first/last occurrence.
  - Hash algorithm: SHA-256, applied to the UTF-8 encoding of the
    normalized identity.
  - Digest-to-integer conversion: the **full** 32-byte SHA-256 digest,
    interpreted as a big-endian unsigned integer (no truncation).
  - Modulus: 2.
  - Bucket rule: even → Subset A; odd → Subset B.
  - Resulting split ratio: approximately 50/50, **emergent** from the
    hash distribution — no rebalancing is applied.
  - No automatic retries of any kind.
- **Process requirements (fixed regardless of whether the parameters
  above are approved as-is or modified):**
  - The split is generated **exactly once**, from the frozen rule, before
    any new statistic is computed.
  - The generated symbol→subset mapping is preserved as an **auditable
    artifact** (e.g., a logged file listing every symbol's subset
    assignment plus subset counts) before computation starts, so the
    freeze can be independently verified after the fact.
  - **No seed shopping** — inapplicable to a pure hash-based rule with no
    seed parameter, which is part of why this method was recommended over
    seeded-random splitting in the prior design review.
  - **No regenerated split after seeing results**, under any
    circumstance, including an unfavorable or ambiguous outcome.
  - **STOP-condition handling for invalid/duplicate identities is
    governed entirely by §16** and takes precedence over any other
    process step in this section if triggered.

## 6. Fresh recomputation

- Fresh metric (Momentum(21,0)) and forward-return computation, from the
  raw `pystock-data` archive, restricted to each frozen symbol subset, is
  **REQUIRED**.
- **Existing `/tmp/momentum.pkl`, `/tmp/rvol.pkl`, `/tmp/fwd.pkl`, and
  `/tmp/dense_arrays.pkl` must NOT be sliced or reused as the basis of
  this test.**
- **Why this is a methodological cleanliness requirement, not merely a
  technical preference:** those arrays were built under one specific
  prior run's data-loading and normalization choices. Reusing them would
  make the new test's independence rest on an unverifiable claim ("no
  per-symbol value was read from this cached array before the split was
  chosen") rather than a structurally guaranteed one. A fresh computation,
  performed only after the split is frozen and logged, removes that
  entire category of doubt — a reviewer does not need to trust that a
  cached artifact was handled correctly; the computation's own inputs are
  the frozen split plus the raw archive, nothing else.

## 7. Test structure

- **One single fixed-hypothesis robustness exercise.**
- The existing frozen Momentum(21,0) statistic (rank-vs-forward-return,
  within-day permutation) is computed **separately** for each of the two
  frozen symbol subsets, over the same historical dates as the original
  CONTROL calibration/validation/holdout combined range.
- No k-fold.
- No repeated splits.
- No retries.
- No new calibration stage.
- No interim interpretation between the two subsets' computations — both
  are computed and then both are reported together; neither is examined
  in isolation before the other is available.

## 8. Statistical machinery

- Reuses **only** the already-frozen statistic and permutation machinery
  from the CONTROL calibration (mean-of-daily within-day fractional-rank
  correlation, sign-flipped per existing convention; N_PERM=1000;
  within-day label permutation).
- **No new heterogeneity/interaction test is introduced by this
  protocol** — see §10.
- Existing **α=0.05** may be reported for each subset's own within-subset
  significance test, exactly as already defined and used throughout
  `stage-f-control-calibration-results-2026-09.md` — but **is not
  converted into a new robustness pass/fail rule**. See §9.
- Raw-close (never adjusted-close) momentum handling is preserved
  unchanged.
- **ForwardReturn target definition — APPROVED (Controller decision,
  2026-09-16; see §18):** `ForwardReturn(i) = AdjustedClose(i) /
  AdjustedClose(i-1) - 1`. This is an **explicit new methodological
  reconstruction, not historically recovered** — see §18 for full status
  and required disclosure wording.
- Existing missing-data convention (incomplete candidates ranked after
  fully-scored candidates, per `stage-f-ranking-architecture.md` §13) is
  preserved unchanged.
- **p-value reporting:** report `p<0.001` rather than a literal `p=0.000`
  wherever N_PERM=1000 cannot resolve a smaller value — a
  reporting-precision matter, not a new statistical threshold.

## 9. Interpretation

**This protocol is descriptive only.** No categorical pass/fail
robustness verdict is issued by this document or by any future execution
under it.

For each of the two symbol subsets, report separately:
- statistic (mean-of-daily rank correlation)
- sign
- p-value (per §8's `p<0.001` convention where applicable)
- sample size / number of usable daily observations
- relevant data-coverage information (e.g., symbol count in the subset,
  any days excluded for insufficient usable observations under the
  existing, already-frozen `n<2` / degenerate-variance exclusion rule
  already present in the calibration machinery — no new exclusion
  threshold is introduced)

Use only these descriptive labels:

- **A. "Directionally consistent — descriptive only"** — the two point
  estimates (subset A's statistic, subset B's statistic) have the same
  sign.
- **B. "Directionally inconsistent — descriptive only"** — the point
  estimates have opposite signs.
- **C. "Inconclusive — descriptive only"** — the available result is
  insufficient to make even a meaningful directional comparison (e.g.,
  one or both subsets produce no usable daily observations under the
  existing exclusion rule, or a degenerate/undefined statistic).

**Explicitly prohibited in any report produced under this protocol:**
"confirmed," "validated," "robustness proven," "success," "rejected," or
any other language implying a formal pass/fail criterion was applied. No
binary pass/fail criterion is defined by this protocol, and none may be
introduced when reporting results.

**The following statement must be included verbatim in any results
document produced under this protocol:**

> "A non-significant result in one subset does not establish that the
> underlying effects differ, and separate within-subset significance
> tests are not a formal test of between-subset heterogeneity."

## 10. Future heterogeneity test — TBD, explicitly out of scope

- A formal between-subset heterogeneity/interaction test (testing
  whether subset A's and subset B's statistics differ from each other,
  rather than each merely differing from zero) may be valuable as a
  **future, separate research protocol.**
- **It is NOT part of this protocol.**
- Designing it would require its own methodology review — a new test
  statistic, a new resampling/permutation scheme appropriate to daily
  panel data with cross-symbol correlation, and explicit Controller
  approval of that design — none of which exists today.
- **No such test is to be improvised during execution of this
  protocol,** regardless of how the descriptive results look.

## 11. Governance

**Gate 0 — Design lock.** Controller explicitly approves, in one written
authorization referencing this protocol by name:
- Fixed candidate: Momentum(21,0), sole subject.
- Symbol identity field, hash algorithm, and modulus (currently TBD, §5).
- Split ratio (currently proposed ~50/50, §5).
- Fresh-recomputation requirement (§6) — not array reuse.
- Descriptive-only interpretation framework (§9) — no pass/fail rule.

**Gate 1 — Split confirmation.** After split generation only:
- The frozen symbol→subset mapping summary (counts per subset,
  deterministic generation details: field, algorithm, modulus used) is
  shown to the Controller.
- **No statistics are computed or shown at this gate.**
- Controller confirms the split is frozen before any computation begins.

**Execution.**
- Fresh computation, exactly once, for both subsets.
- No retry under any circumstance.
- No split regeneration under any circumstance.

**Final.**
- Results reported exactly as computed, using only the descriptive labels
  in §9.
- No post-hoc interpretation rule introduced at reporting time.
- No follow-up experiment performed under this same protocol — any
  further work (including §10's heterogeneity test) requires a new,
  separately authorized protocol.

## 12. Evidentiary limit

Even a "Directionally consistent — descriptive only" result does **NOT**:

- remove or repair the original post-hoc candidate-selection violation
  (`stage-f-control-calibration-results-2026-09.md` §8);
- establish time-period generalization (only 2009–2017 is tested, on
  both subsets identically);
- establish vendor independence (single vendor, `pystock-data`, on both
  subsets identically);
- solve survivorship bias (unresolved on both subsets identically);
- solve point-in-time universe construction (unresolved on both subsets
  identically);
- establish production readiness of Momentum(21,0) or any Stage F metric;
- authorize Stage F ranking implementation, `RANKING_METRIC_DEFINITIONS`
  population, or wiring `ranking.py` into `pipeline.py`.

## 13. Approval table

| Element | Status |
|---|---|
| Momentum(21,0) as sole fixed candidate | PROPOSED / NOT APPROVED |
| No new calibration / no candidate re-selection | PROPOSED / NOT APPROVED (consistent with prior design-review consensus, not yet formally approved as protocol text) |
| Existing statistic + permutation machinery (N_PERM=1000, within-day shuffle) | APPROVED (already frozen, D-0030) — reuse only, unchanged |
| Existing α=0.05 | APPROVED (already frozen, D-0030) — reused for within-subset reporting only, not as a new pass/fail rule |
| Raw-close momentum convention | APPROVED (already frozen, D-0030 / `stage-f-ranking-architecture.md`) |
| Missing-data / exclusion conventions | APPROVED (already frozen) — reused unchanged |
| Hash-based symbol split as method | PROPOSED / NOT APPROVED |
| Exact identity field, hash algorithm, modulus | TBD |
| Split ratio (~50/50) | PROPOSED / NOT APPROVED |
| Fresh-recomputation requirement | PROPOSED / NOT APPROVED |
| Descriptive-only interpretation (no pass/fail rule) | PROPOSED / NOT APPROVED |
| Future heterogeneity/interaction test | TBD — explicitly out of scope of this protocol |
| Any production Stage F change | NOT PROPOSED — out of scope entirely |

## 14. Controller decisions required

1. Approve or modify the symbol split method, identity field, hash
   algorithm, and modulus (§5).
2. Approve or modify the proposed ~50/50 split ratio (§5).
3. Approve the fresh-recomputation requirement (§6).
4. Approve the descriptive-only interpretation framework and label set
   (§9), including the prohibited-terminology list.
5. Approve the two-gate governance sequence (§11), or specify changes.
6. Decide whether/when to authorize a separate future methodology-design
   cycle for the heterogeneity test described in §10 (no action needed
   now; listed for awareness only).

No other decisions are required to move this protocol from PROPOSED to
APPROVED.

## 15. Controller approval record

- **Date:** 2026-09-16
- **Approved:** framework structure and governance only, specifically:
  overall protocol structure and governance sequence (§11); Momentum(21,0)
  as the fixed single candidate (§4); no calibration; no candidate
  re-selection; no tie-break; the symbol-split robustness framing (§1,
  §7); descriptive-only interpretation, no pass/fail rule (§9); no new
  heterogeneity test (§10); the fresh-recomputation requirement (§6); no
  reuse of the previous validation/holdout as evidence (§2); permanent
  disclosure of the original post-hoc selection violation (§2, §12);
  Stage F remains dormant (§12).
- **NOT approved / still TBD:** exact hash algorithm; exact modulus;
  exact split ratio (the ~50/50 default remains proposed, not adopted);
  exact ticker normalization rule; actual split generation; any metric
  computation; any statistical test execution.
- **Explicit limit:** this is approval of the protocol framework only. It
  is NOT authorization to execute the experiment, generate the symbol
  split, compute Momentum(21,0), or run any calibration, validation,
  holdout, or statistical test. A further, separate Controller
  authorization is required — covering the still-TBD parameters above —
  before any computation under this protocol may begin.

## 16. STOP-condition governance (data-quality anomalies during split generation)

This section governs what happens if, during split generation, the
normalized CONTROL symbol list contains an **invalid identity**
(empty/null/whitespace-only after normalization) or a **duplicate
normalized identity** (two or more distinct source entries normalizing to
the same identity). This section applies whenever split generation is
eventually authorized (§11 Gate 0/Gate 1) — it is part of the frozen
protocol, not a decision made at generation time.

### 16.1 Detection and hard STOP

- Normalization (§5) is applied to every source identity **first**.
- Anomaly detection (invalid identity; duplicate identity) happens
  **before any hashing**.
- If either anomaly type is present, split generation **STOPS
  unconditionally before any subset assignment is produced.**
- The STOP report must state the exact anomaly type(s) and count(s), and
  identify the specific offending raw source entries.
- **No silent exclusion.**
- **No first/last-occurrence rule.**
- **No automatic retry.**
- **No automatic continuation.**

### 16.2 Closed resolution model

A STOP condition may be resolved **only** by one of the following three
paths. **Any resolution outside A/B/C is not a resolution under this
protocol and requires a new, separate methodology/protocol decision
before anything proceeds — it may not be improvised at the point of the
anomaly.**

**A. Verified source/extraction correction, under the existing frozen
rules.**
- Allowed **only** when the anomaly is objectively attributable to a
  demonstrable source/extraction/ingestion error, independent of any
  experimental result.
- The evidence for the correction must be independently auditable (e.g.,
  traceable to the archive's own documentation, a verifiable byte-level
  encoding issue, or another objectively checkable fact about the source
  data).
- **Must NOT** rely on Momentum(21,0), any p-value, historical
  performance, or any other experimental result as justification.
- **Must NOT** be a subjective preference about which ticker identity
  "should" win.
- The corrected symbol universe receives a **new, explicit
  version/identity** (§16.4) — it is never treated as the same,
  unversioned population the STOP was raised against.

**B. Explicit new exclusion/disambiguation rule.**
- Requires a **protocol amendment** to this document (the existing §5
  rules did not anticipate this specific rule) **plus** explicit
  Controller approval of that amendment, before regeneration may be
  authorized.

**C. Abandon this protocol run.**
- No regeneration. Momentum(21,0)'s status remains unchanged from its
  current EXPLORATORY-ONLY LEAD.

### 16.3 Recording a resolution

Every STOP resolution, regardless of path, must be:

- **Explicitly written** — stating which of A/B/C was chosen and why.
- **Dated.**
- **Recorded in the audit artifact** (§5's auditable symbol→subset
  mapping artifact, extended with a STOP/resolution history section).
- **Linked to the exact anomaly** it resolves (type, count, specific
  entries from §16.1).
- **Completed before any new split-generation attempt.**

### 16.4 Universe versioning after resolution

Any resolution that changes the eligible symbol population (paths A or
B) produces a **different, newly versioned symbol universe** — never
silently equated with the raw, unadjusted archive extraction that
triggered the STOP. The audit artifact's dataset-identity/version field
must be extended to state: the base extraction identity, **plus** the
dated resolution reference, **plus** what changed (count
excluded/corrected) as a result.

### 16.5 Governance chain by resolution path

| Path | Required steps before regeneration |
|---|---|
| **A** — verified source correction | audit addendum → corrected universe/version recorded (§16.4) → fresh Gate-1-style confirmation → separate explicit Controller authorization before regeneration |
| **B** — new exclusion/disambiguation rule | protocol amendment → audit addendum → explicit Controller approval of the amendment → separate explicit Controller authorization before regeneration |
| **C** — abandonment | audit addendum only; no regeneration |

### 16.6 STOP resolution is not a retry

- Resolving a STOP is a **governance action**, not a computation retry.
- It does not itself regenerate anything.
- Regeneration is permitted **only** after the governance steps in §16.5
  for the chosen path are complete, including the separate explicit
  Controller authorization.
- When generation does proceed under newly authorized conditions, it
  still happens **exactly once** — the "generate once, no retries" rule
  (§5, §7, §11) is not reset or weakened by having gone through a
  STOP/resolution cycle.

## 17. Gate 0 execution record — split parameters approved, split generated

- **Date:** 2026-09-16.
- **Controller action:** formal Gate 0 approval of the concrete symbol-split
  parameters listed in §5, and authorization to generate the frozen split
  artifact. **Statistical computation was explicitly NOT authorized by
  this action** and none occurred.
- **Pre-generation validation:** the CONTROL dataset's symbol identity
  list was normalized per §5's rule (NFC, `strip()`, `upper()`) and
  checked for invalid and duplicate identities per §16.1, **before** any
  hashing. Source: `/tmp/dense_arrays.pkl` (key `symbols`) — the symbol
  identity list as extracted during the original CONTROL calibration run
  (D-0030). The original `eliangcs/pystock-data` archive clone had
  already been deleted from local disk after inspection, per standing
  repo-hygiene practice recorded in `decisions.md` (D-0026 GitHub-native
  entry) — this is a disclosed provenance limitation for the *identity
  list* only; no price, metric, or statistic value from any cached `.pkl`
  was read or used in generating this split.
- **Result: no STOP condition.** 7,077 raw symbol entries; 0 invalid
  identities after normalization; 0 duplicate normalized identities; 7,077
  normalized symbols proceeded to split generation.
- **Split generated exactly once**, per §5's approved rule (SHA-256, full
  digest, big-endian unsigned integer, modulus 2, even→A/odd→B, no
  rebalancing, no retry).
- **Result:** Subset A = 3,603 symbols; Subset B = 3,474 symbols; total =
  7,077 (matches the pre-generation validated count exactly).
- **Artifact:** `momentum_21_0_symbol_split_artifact.json`, written to
  this session's scratchpad directory (outside the git repository, per
  this project's standing practice of keeping calibration/split artifacts
  out of version control — only this documentation record is committed).
  Contains, at minimum: dataset identity/provenance, source file identity,
  normalization rule, hash algorithm, hash input encoding, digest
  conversion rule, modulus, bucket rule, total/subset counts, the full
  symbol→subset mapping, generation timestamp (UTC), Python/runtime
  version, and a reference to this protocol document.
- **Artifact checksum (SHA-256 of the artifact file itself):**
  `dfa1697cb3863fed2af25c73b770798fbc5c58622330047f9a4ffcf35425e33f`
- **Explicit limit, restated:** this record documents that the symbol
  split now exists as a frozen, auditable artifact. **It does not
  authorize, and is not, computation of Momentum(21,0), RVOL, forward
  returns, or any permutation/statistical test.** A separate, explicit
  Controller authorization (Gate 1, per §11) is required before any
  statistic is computed against either subset.

## 18. ForwardReturn target convention — Controller decision (Gate 2 methodology, separate from Gate 2 execution)

- **Date:** 2026-09-16.
- **Decision:** the ForwardReturn target for this protocol's Momentum(21,0)
  robustness computation is defined as:

  > `ForwardReturn(i) = AdjustedClose(i) / AdjustedClose(i-1) - 1`

- **Status: APPROVED as an explicit new methodological reconstruction.**
  This closes the one previously open parameter blocking Gate 2 (per the
  historical-definition recovery investigation, 2026-09-16). It does
  **not** itself authorize any computation — see below.
- **Historical status — preserved, not overwritten:** the original
  CONTROL calibration's exact ForwardReturn arithmetic **remains
  UNKNOWN**. No surviving project evidence (scripts, documentation, git
  history) established what price convention the original `fwd.pkl` used.
  This decision does **not** claim to have recovered it. This adjusted-close
  definition is a **new choice for this protocol only**, not a
  historically verified fact.
- **Must NOT be described as formula-for-formula replication** of the
  original CONTROL calibration/validation/holdout chain. Any results
  produced under this definition are **not** numerically comparable,
  metric-for-metric, to the original calibration's stat/p-value figures.
- **Terminology constraint:** `AdjustedClose` must be described as a
  **vendor/dataset corporate-action-adjusted return measure** (per
  `pystock-data`'s own adjustment methodology), **not** as verified,
  literal investor-realized total return — the adjustment methodology
  itself has not been independently audited at the individual-event level
  by this project.
- **Predictor unchanged:** Momentum(21,0) remains defined exactly as
  previously approved — raw close only, never adjusted close. This
  decision applies to the target (ForwardReturn) only and creates an
  intentional, explicitly justified asymmetry between predictor and
  target price conventions (see the decision-analysis discussion this
  approval is based on — predictor look-ahead risk from global
  back-adjustment does not apply to a retrospective, already-realized
  target).
- **Evidentiary weakening, explicitly preserved:** any future result
  produced under this protocol carries this additional disclosed
  limitation on top of every limitation already recorded in §3 and §12
  (survivorship bias, point-in-time universe, vendor effects, historical-
  regime limitations, the original post-hoc selection violation). This
  decision adds "reconstructed rather than recovered target definition"
  as a further, separate, named limitation — it does not replace or
  subsume any of the others.
- **No other change authorized by this decision:** no change to the
  symbol split, split artifact, or checksum; no new exclusion; no
  sensitivity analysis; no heterogeneity test; no new statistic; no
  change to the permutation machinery (N_PERM=1000, α=0.05); no change to
  the descriptive-only interpretation framework (§9); no change to the
  Gate structure (§11).
- **This decision does NOT authorize any statistical computation.** Gate 2
  execution (actually running the computation) remains a separate,
  standalone Controller authorization, not implied or triggered by this
  methodology approval.

## 19. Gate 1 audit record

- **Date:** 2026-09-16.
- **Total normalized symbols:** 7,077. **Subset A:** 3,603. **Subset B:**
  3,474. **Split ratio:** 50.91% / 49.09%.
- **Invalid identities found:** 0. **Duplicate normalized identities
  found:** 0.
- **Normalization verified:** NFC → `strip()` → `upper()`, matching §5.
- **Hash verified:** SHA-256; full 32-byte digest interpreted as a
  big-endian unsigned integer (no truncation); modulus 2; even→Subset A,
  odd→Subset B — matching §5 exactly.
- **No rebalancing** applied to the emergent split. **No retry or
  regeneration** occurred.
- **Split artifact checksum, independently recomputed and matched:**
  `dfa1697cb3863fed2af25c73b770798fbc5c58622330047f9a4ffcf35425e33f`.
- **The split was frozen (generated and checksummed) before any
  statistical computation occurred** — confirmed by artifact timestamp
  continuity across the Gate 1 audit and the later Gate 2 execution.
- **Gate 1 audit itself was read-only:** no statistical computation, no
  Momentum/ForwardReturn value, and no new split generation occurred
  during the audit — only inspection and checksum verification of the
  already-frozen artifact from §17.

## 20. Gate 2 execution record

- **Date:** 2026-09-16.
- **Candidate:** Momentum(21,0) only.
- **Predictor:** `RawClose(i-1) / RawClose(i-22) - 1` (raw close, per §8,
  unchanged from the approved architecture).
- **ForwardReturn:** `AdjustedClose(i) / AdjustedClose(i-1) - 1`.
  **This ForwardReturn convention was a new reconstruction for this
  symbol-split robustness test (§18) — it was NOT historically
  recovered**, and this result is not a formula-for-formula replication
  of the original D-0030 CONTROL calibration.
- **Dataset:** `eliangcs/pystock-data` CONTROL dataset.
- **Fresh computation source:** `/tmp/full_panel.pkl` only.
  `momentum.pkl` was not used. `rvol.pkl` was not used. `fwd.pkl` was not
  used. `dense_arrays.pkl` was not used as a metric/statistic input
  (its `symbols` key was used only earlier, at Gate 0, for identity/split
  membership — never for any computed value in Gate 2).
- **Split artifact checksum verified immediately before use:**
  `dfa1697cb3863fed2af25c73b770798fbc5c58622330047f9a4ffcf35425e33f`.
- **N_PERM:** 1000. **α:** 0.05, two-sided.
- **Corporate-action handling — precise wording:** no corporate-action
  exclusion was applied in this symbol-split robustness test. This is a
  disclosed methodological difference from the earlier D-0030 CONTROL
  calibration. The Gate 2 test did not apply the earlier calibration's
  corporate-action exclusion rule. (A per-symbol raw-vs-adjusted
  divergence count was computed as an informational-only diagnostic
  during this run and is not directly comparable to D-0030 §2's earlier,
  differently-defined flag count — the two use different detection
  methodologies and are not interchangeable figures.)

**Subset A result:**
- 3,603 symbols; 2,013 usable days
- mean statistic = **+0.014668**
- p **< 0.001**
- sign: **positive**
- interpretation: descriptive only, no pass/fail rule applied

**Subset B result:**
- 3,474 symbols; 2,094 usable days
- mean statistic = **+0.021189**
- p **< 0.001**
- sign: **positive**
- interpretation: descriptive only, no pass/fail rule applied

**Final classification: "Directionally consistent — descriptive only."**

**Evidence boundaries, explicitly preserved and restated:** this result
does **not** repair D-0030's post-hoc candidate-selection violation
(§8 of `stage-f-control-calibration-results-2026-09.md`); does **not**
establish time-period independence (same 2009–2017 dates as the original
CONTROL work); does **not** establish vendor independence (same single
vendor, `pystock-data`); does **not** solve survivorship bias; does
**not** solve point-in-time-universe construction; does **not** authorize
production Stage F implementation; does **not** approve Momentum(21,0)
as a production metric; does **not** make the original ForwardReturn
formula historically recovered; and does **not** prove profitability.
`RANKING_METRIC_DEFINITIONS` remains empty; `ranking.py` remains dormant;
`NotCalibratedStageEvaluator` remains the sole production evaluator for
`PipelineStage.RANKING`.
