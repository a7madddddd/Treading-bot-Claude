"""D-0026 Stage F (Ranking) — Option 3 boundary scaffolding.

Approved 2026-09-15 (Controller: Option 3 F->G->H->I boundary
architecture + INV-FGH-ORDER, Phase 1 implementation authorization).
This module implements ONLY the inert type-level contract and plumbing
for Stage F. It contains no ranking metric, weight, threshold,
direction-of-goodness, or calibration value of any kind, and it is not
wired into pipeline.py — it has zero runtime call sites from the
production pipeline in this phase.

Design (per the approved Option 3 boundary):

    - StageEvaluator / StageResult (pipeline.py) remain completely
      unchanged. Stage F communicates ranking ONLY through the order of
      the UniverseCandidate tuple it returns as StageResult.survivors —
      no new field, no new stage-loop type.
    - SelectedCandidateEntry is constructed exactly once, at the
      Stage H -> Stage I boundary, by build_selected_candidate_entries()
      below — never anywhere else.
    - rank is the candidate's 1-indexed position in the FINAL ordered
      tuple this function receives (i.e. after Stage G and Stage H have
      run) — it is not Stage F's original ranking position.
    - score_summary is recomputed fresh at that same boundary by
      compute_ranking_score_summary(), a pure, deterministic function
      with no Evidence/Confidence, history/reputation, market-data
      source, randomness, external I/O, or mutable global state
      dependency.

INV-FGH-ORDER (recorded 2026-09-15): Stage G and Stage H must preserve
the relative ordering Stage F establishes, for every candidate that
survives. This cannot be enforced at the type level without modifying
StageResult (out of scope, not authorized) — enforcement is
behavioral/test-only, documented here and exercised once real G/H
evaluators exist.

Duplicate candidates (e.g. two RawCandidateRef entries resolving to the
same security_id) are an explicit, out-of-scope, upstream concern
(identity resolution / provider layer) — this module performs no
deduplication and will assign distinct rank values to distinct
UniverseCandidate entries regardless of whether they share an identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Tuple

from .models import RegimeState, SelectedCandidateEntry, UniverseCandidate

RANKING_METRIC_DEFINITION_VERSION = "D0026-RANK-METRICDEF-001"
"""Identifies the exact set of declared ranking metrics in
RANKING_METRIC_DEFINITIONS below. Any future change to that set is a
NEW version string, recorded as its own dated docs/trading/decisions.md
entry — never a silent edit in place. Mirrors
FEATURE_DEFINITION_VERSION's role in evidence.py."""


@dataclass(frozen=True)
class RankingMetricDefinition:
    """A single ranking metric's static, versioned declaration.

    Deliberately carries no weight, threshold, direction-of-goodness,
    priority, score, regime adjustment, or any other calibration-related
    field — those are calibration-adjacent decisions this module is not
    authorized to make. Only a metric's identity is declared here.
    """

    metric_id: str


RankingMetricId = str
"""Type-level contract only — a plain string alias, not a populated
Enum. An Enum with declared members would itself assert that specific
metrics exist, which is not approved. See RANKING_METRIC_DEFINITIONS."""


RANKING_METRIC_DEFINITIONS: Tuple[RankingMetricDefinition, ...] = ()
"""Deliberately EMPTY — mirrors FEATURE_DEFINITIONS in evidence.py.
No entry may be added here except as its own, separately dated
Controller decision, under a new RANKING_METRIC_DEFINITION_VERSION.
Never derived from configuration, environment variables, database data,
files, APIs, or any other source — this is a literal, static tuple."""


def compute_ranking_score_summary(
    candidate: UniverseCandidate,
    as_of_date: date,
    regime_state: RegimeState,
    *,
    metric_definitions: Tuple[RankingMetricDefinition, ...] = RANKING_METRIC_DEFINITIONS,
) -> Tuple[Tuple[str, float], ...]:
    """Pure, deterministic, stateless. No Evidence/Confidence input, no
    history/reputation input, no external I/O, no randomness, no
    mutable global state.

    With the default (and, in this phase, only ever supplied) empty
    metric_definitions registry, there is nothing to compute — returns
    () unconditionally. A non-empty metric_definitions is reserved for
    a future, separately approved calibrated implementation; this
    function deliberately refuses to guess at scoring logic for it
    rather than silently returning an empty or fabricated result.
    """

    if metric_definitions:
        raise NotImplementedError(
            "compute_ranking_score_summary has no calibrated scoring "
            "logic for a non-empty metric_definitions registry; "
            "D-0026 CALIBRATION = BLOCKED "
            "(docs/trading/historical-data-calibration-plan.md §22)"
        )
    return ()


def build_selected_candidate_entries(
    ranked_survivors: Tuple[UniverseCandidate, ...],
    as_of_date: date,
    regime_state: RegimeState,
) -> Tuple[SelectedCandidateEntry, ...]:
    """The ONLY place SelectedCandidateEntry is ever constructed.

    ``ranked_survivors`` is assumed to already represent the final
    Stage H survivor ordering (Stage F's ranking order, preserved
    through Stage G and Stage H per INV-FGH-ORDER). This function does
    not sort, reorder, or deduplicate it — it only assigns a 1-indexed
    rank from tuple position and attaches a freshly computed
    score_summary per candidate.

    Pure, deterministic, stateless — same discipline as
    compute_ranking_score_summary. Not called from pipeline.py in this
    phase; that wiring is a separate, not-yet-authorized change.
    """

    return tuple(
        SelectedCandidateEntry(
            candidate=candidate,
            rank=index,
            score_summary=compute_ranking_score_summary(candidate, as_of_date, regime_state),
        )
        for index, candidate in enumerate(ranked_survivors, start=1)
    )
