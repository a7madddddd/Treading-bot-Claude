"""D-0026 Evidence/Confidence Layer — v3-final frozen design.

Approved 2026-09-15 (Controller review thread: v1 -> v2 -> v3 ->
v3-final). This module implements EXACTLY that frozen design — no
redesign, no reinterpretation, no additional rules.

Position in the pipeline: this layer runs strictly AFTER Stage H
(Top-N) and BEFORE Stage I (persist). It observes the outcome of
Stages A-H for a candidate that has already survived them; it never
influences those stages, and it is never influenced by them beyond
reading their already-decided outcome for THIS ONE candidate on THIS
ONE date.

Two invariants govern this entire module and every one of its callers,
permanently:

    D0026-EV-INV-1
    Evidence Quality, Decision Type, Confidence, and Risk are
    read-only, downstream-of-ranking metadata. No stage A-H, no
    ranking formula, and no strategy-mechanics-fit logic may take any
    of these as an input — and neither may the HISTORY of a security's
    past classifications (no reputation/trust accumulation of any
    kind: every classification is computed fresh from that day's
    evidence state alone).

    D0026-EV-INV-2
    Evidence Quality may improve or decline as evidence availability
    changes, but it must never influence trading desirability. No
    change in Evidence Quality may alter ranking, selection,
    strategy-mechanics calculations, trigger calculations, ladder
    parameters, or any calibrated trading parameter. Evidence Quality
    is evidence-characterization metadata and routing/visibility
    metadata only.

Structural enforcement of the "no counting, no weights, no numeric
thresholds" rule: classification is a pure lookup of named condition
membership (see EvidenceConditionId, _HARD_FAIL_IDS, _CORE_SOFT_GAP_IDS,
_AUXILIARY_SOFT_GAP_IDS) against three fixed sets. At no point is any
condition counted, summed, weighted, or compared against a numeric
cutoff — severity is determined solely by WHICH named condition fired
and WHICH fixed category (hard-fail / core / auxiliary) it belongs to.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from .identity import IdentityConfidence, IdentityResolution, ResolutionOutcome
from .models import AdjustmentConvention, DailySecurityFeatures, MarketDataBar, RegimeLabel, RegimeState

EVIDENCE_POLICY_VERSION = "D0026-EV-001"
"""Identifies the exact hard-fail / soft-gap / CORE-AUXILIARY rule set
in this module. Any future change to those rules — adding, removing,
or re-tagging a condition — is a NEW version string, recorded as its
own dated docs/trading/decisions.md entry. D0026-EV-001 is never
silently edited in place."""

FEATURE_DEFINITION_VERSION = "D0026-FEATDEF-001"
"""Identifies the per-feature AdjustmentDependency declarations in
FEATURE_DEFINITIONS below. Deliberately a SEPARATE version from
EVIDENCE_POLICY_VERSION: feature definitions are a strategy-mechanics /
feature-construction concern (docs/trading/historical-data-calibration-
plan.md §20), evidence-classification rules are a separate policy
concern. evidence_policy_version REFERENCES this version; it never
re-declares the dependency table itself."""


class AdjustmentDependency(Enum):
    """Whether a feature's correctness depends on the price series'
    split/dividend adjustment convention. Declared STATICALLY per
    feature in FEATURE_DEFINITIONS below.

    Runtime evidence classification (classify_evidence, below) may only
    READ this value for a given feature. It has no code path that
    selects, infers, upgrades, downgrades, or overrides a feature's
    declared dependency — not based on the candidate, not based on
    current market conditions, not based on data availability. If this
    invariant is ever violated by a future change, that change is a
    departure from the frozen design and must be rejected in review.
    """

    REQUIRES_ADJUSTED = "requires_adjusted"
    INDIFFERENT = "indifferent"


@dataclass(frozen=True)
class FeatureDefinition:
    """A single feature's static, versioned adjustment-dependency
    declaration. Membership in FEATURE_DEFINITIONS is itself part of
    FEATURE_DEFINITION_VERSION's content."""

    name: str
    adjustment_dependency: AdjustmentDependency


FEATURE_DEFINITIONS: Tuple[FeatureDefinition, ...] = ()
"""No D-0026 features are declared yet. This is deliberate, not an
omission: inventing placeholder feature requirements merely to exercise
HF8 would violate the frozen design's explicit instruction not to
activate HF8 artificially. HF8 (below) is fully specified and ready,
but structurally DORMANT — it can never fire against this empty
registry — until Phase C/D produces real, calibrated StageEvaluator
implementations with real feature requirements. At that point, entries
are added here under a new FEATURE_DEFINITION_VERSION, as their own
dated decision — never as a silent edit of this tuple."""


class EvidenceQuality(Enum):
    """Categorical, condition-based, explainable from named evidence
    conditions. Never a count, score, or weighted combination."""

    INSUFFICIENT = "insufficient"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DecisionType(Enum):
    NOT_ELIGIBLE = "not_eligible"
    MANUAL_REVIEW = "manual_review"
    STANDARD = "standard"


_QUALITY_TO_DECISION = {
    EvidenceQuality.INSUFFICIENT: DecisionType.NOT_ELIGIBLE,
    EvidenceQuality.LOW: DecisionType.MANUAL_REVIEW,
    EvidenceQuality.MEDIUM: DecisionType.MANUAL_REVIEW,
    EvidenceQuality.HIGH: DecisionType.STANDARD,
}
"""The ONLY mapping from Evidence Quality to Decision Type. Fixed,
direct, one-to-one — not derived from any further logic."""


class ConfidenceStatus(Enum):
    NOT_CALIBRATED = "not_calibrated"
    CALIBRATED = "calibrated"


class EvidenceConditionId(Enum):
    """The fixed, named hard-fail (HF) and soft-gap (SG) condition
    identifiers, exactly as frozen in the approved v3-final design.
    Every evidence classification records exactly which of these
    fired — never a free-text-only explanation, and never a count."""

    HF1_IDENTITY_UNRESOLVED = "HF1"
    HF2_IDENTITY_AMBIGUOUS = "HF2"
    HF3_BAR_MISSING = "HF3"
    HF4_BAR_CORRUPTED = "HF4"
    HF5_REGIME_UNKNOWN = "HF5"
    HF6_FEATURES_NOT_COMPUTED = "HF6"
    HF7_ADJUSTMENT_CONVENTION_UNKNOWN = "HF7"
    HF8_REQUIRED_FEATURE_NEEDS_ADJUSTED_DATA_BUT_ONLY_RAW_AVAILABLE = "HF8"
    SG1_IDENTITY_CORROBORATED_RANGE = "SG1"
    SG2_IDENTITY_PROVISIONAL = "SG2"
    SG3_FEATURES_INSUFFICIENT_WARMUP = "SG3"
    SG4_EXECUTION_QUALITY_RANGE_PROXY = "SG4"


_HARD_FAIL_IDS = frozenset(
    {
        EvidenceConditionId.HF1_IDENTITY_UNRESOLVED,
        EvidenceConditionId.HF2_IDENTITY_AMBIGUOUS,
        EvidenceConditionId.HF3_BAR_MISSING,
        EvidenceConditionId.HF4_BAR_CORRUPTED,
        EvidenceConditionId.HF5_REGIME_UNKNOWN,
        EvidenceConditionId.HF6_FEATURES_NOT_COMPUTED,
        EvidenceConditionId.HF7_ADJUSTMENT_CONVENTION_UNKNOWN,
        EvidenceConditionId.HF8_REQUIRED_FEATURE_NEEDS_ADJUSTED_DATA_BUT_ONLY_RAW_AVAILABLE,
    }
)

_CORE_SOFT_GAP_IDS = frozenset(
    {
        EvidenceConditionId.SG1_IDENTITY_CORROBORATED_RANGE,
        EvidenceConditionId.SG2_IDENTITY_PROVISIONAL,
        EvidenceConditionId.SG3_FEATURES_INSUFFICIENT_WARMUP,
    }
)

_AUXILIARY_SOFT_GAP_IDS = frozenset(
    {
        EvidenceConditionId.SG4_EXECUTION_QUALITY_RANGE_PROXY,
    }
)


@dataclass(frozen=True)
class EvidenceClassification:
    """The complete, immutable result of classifying one candidate's
    evidence on one date. Carries its own policy-version provenance so
    every downstream consumer (snapshot, audit event, Telegram package,
    approval record) can record exactly which rule set produced it.

    confidence/risk invariants are enforced structurally here, not just
    by convention: constructing this object with a non-None confidence
    or risk, or a CALIBRATED status, raises — there is no legitimate
    way to produce a populated confidence/risk value anywhere in this
    module today.
    """

    evidence_quality: EvidenceQuality
    decision_type: DecisionType
    fired_conditions: Tuple[EvidenceConditionId, ...]
    evidence_policy_version: str
    feature_definition_version: str
    confidence: Optional[float] = None
    confidence_status: ConfidenceStatus = ConfidenceStatus.NOT_CALIBRATED
    risk: Optional[float] = None
    risk_status: ConfidenceStatus = ConfidenceStatus.NOT_CALIBRATED

    def __post_init__(self) -> None:
        if _QUALITY_TO_DECISION[self.evidence_quality] is not self.decision_type:
            raise ValueError(
                "decision_type must be the fixed EvidenceQuality -> "
                "DecisionType mapping; it cannot diverge from it"
            )
        if self.confidence is not None:
            raise ValueError(
                "confidence must remain None until a separately proposed, "
                "independently validated, outcome-based methodology exists "
                "(frozen policy — see historical-data-calibration-plan.md)"
            )
        if self.confidence_status is not ConfidenceStatus.NOT_CALIBRATED:
            raise ValueError("confidence_status must remain NOT_CALIBRATED")
        if self.risk is not None:
            raise ValueError(
                "risk must remain None until a separately proposed, "
                "independently validated, outcome-based methodology exists"
            )
        if self.risk_status is not ConfidenceStatus.NOT_CALIBRATED:
            raise ValueError("risk_status must remain NOT_CALIBRATED")


def _lookup_feature_definition(
    name: str, feature_definitions: Tuple[FeatureDefinition, ...]
) -> FeatureDefinition:
    for definition in feature_definitions:
        if definition.name == name:
            return definition
    raise ValueError(
        f"required_feature_names references {name!r}, which has no "
        "declared FeatureDefinition — refusing to guess its "
        "AdjustmentDependency (never invent evidence)"
    )


# --- Per-dimension condition detection -------------------------------
#
# Each helper inspects exactly one of the six evidence dimensions and
# returns the (possibly empty) tuple of EvidenceConditionId values that
# fired for it. classify_evidence() and build_evidence_summary() (below)
# both call these SAME helpers — this is the single source of truth that
# guarantees "raw evidence state -> fired conditions ->
# EvidenceClassification" and, independently, "raw evidence state ->
# EvidenceSummary" always describe identical underlying evidence.


def _identity_conditions(
    identity_resolution: IdentityResolution,
) -> Tuple[EvidenceConditionId, ...]:
    if identity_resolution.outcome is ResolutionOutcome.UNRESOLVED:
        return (EvidenceConditionId.HF1_IDENTITY_UNRESOLVED,)
    if identity_resolution.outcome is ResolutionOutcome.AMBIGUOUS:
        return (EvidenceConditionId.HF2_IDENTITY_AMBIGUOUS,)
    assert identity_resolution.identity is not None  # RESOLVED invariant
    confidence_tier = identity_resolution.identity.confidence
    if confidence_tier is IdentityConfidence.RESOLVED_CORROBORATED_RANGE:
        return (EvidenceConditionId.SG1_IDENTITY_CORROBORATED_RANGE,)
    if confidence_tier is IdentityConfidence.PROVISIONAL:
        return (EvidenceConditionId.SG2_IDENTITY_PROVISIONAL,)
    return ()  # RESOLVED_CIK -> no gap.


def _bar_conditions(
    bar: Optional[MarketDataBar], bar_corrupted: bool
) -> Tuple[EvidenceConditionId, ...]:
    if bar is None:
        return (EvidenceConditionId.HF3_BAR_MISSING,)
    if bar_corrupted:
        return (EvidenceConditionId.HF4_BAR_CORRUPTED,)
    return ()


def _regime_conditions(regime_state: RegimeState) -> Tuple[EvidenceConditionId, ...]:
    if regime_state.label is RegimeLabel.UNKNOWN:
        return (EvidenceConditionId.HF5_REGIME_UNKNOWN,)
    return ()


def _feature_warm_up_conditions(
    features: Optional[DailySecurityFeatures],
) -> Tuple[EvidenceConditionId, ...]:
    if features is None:
        return (EvidenceConditionId.HF6_FEATURES_NOT_COMPUTED,)
    if not features.warm_up_sufficient:
        return (EvidenceConditionId.SG3_FEATURES_INSUFFICIENT_WARMUP,)
    return ()


def _execution_quality_conditions(
    features: Optional[DailySecurityFeatures],
) -> Tuple[EvidenceConditionId, ...]:
    if features is None:
        # Absence of features is already reported under the
        # Feature/Warm-up dimension (HF6) -- this dimension has nothing
        # further to add when there is no feature evidence at all.
        return ()
    if not features.execution_quality_proxy_is_true_quote:
        return (EvidenceConditionId.SG4_EXECUTION_QUALITY_RANGE_PROXY,)
    return ()


def _corporate_action_conditions(
    bar: Optional[MarketDataBar],
    bar_corrupted: bool,
    required_feature_names: Tuple[str, ...],
    adjusted_series_available_for: Tuple[str, ...],
    feature_definitions: Tuple[FeatureDefinition, ...],
) -> Tuple[EvidenceConditionId, ...]:
    if bar is None or bar_corrupted:
        # Already reported under Price/Volume (HF3/HF4) -- convention
        # cannot be assessed without a usable bar.
        return ()
    if bar.adjustment is AdjustmentConvention.UNKNOWN:
        return (EvidenceConditionId.HF7_ADJUSTMENT_CONVENTION_UNKNOWN,)
    # HF8 is evaluated ONLY against features actually required by the
    # current stage configuration (required_feature_names), using their
    # STATIC AdjustmentDependency declaration only -- presence/absence,
    # never a count of how many features fail.
    hf8_triggered = any(
        _lookup_feature_definition(name, feature_definitions).adjustment_dependency
        is AdjustmentDependency.REQUIRES_ADJUSTED
        and bar.adjustment is AdjustmentConvention.RAW
        and name not in adjusted_series_available_for
        for name in required_feature_names
    )
    if hf8_triggered:
        return (EvidenceConditionId.HF8_REQUIRED_FEATURE_NEEDS_ADJUSTED_DATA_BUT_ONLY_RAW_AVAILABLE,)
    return ()


def classify_evidence(
    *,
    identity_resolution: IdentityResolution,
    bar: Optional[MarketDataBar],
    bar_corrupted: bool,
    regime_state: RegimeState,
    features: Optional[DailySecurityFeatures],
    required_feature_names: Tuple[str, ...] = (),
    adjusted_series_available_for: Tuple[str, ...] = (),
    feature_definitions: Tuple[FeatureDefinition, ...] = FEATURE_DEFINITIONS,
) -> EvidenceClassification:
    """Classify one candidate's evidence for one date. Pure function —
    stateless, no memoization, no access to any prior classification of
    this or any other security. This statelessness is itself the
    enforcement of "evidence history must never influence classification"
    (D0026-EV-INV-1's extension): there is no parameter through which a
    past outcome could even be supplied.

    ``required_feature_names`` defaults to empty, matching the current
    Phase A pipeline wiring: the only StageEvaluator implementation that
    exists (NotCalibratedStageEvaluator) declares no feature
    requirements, so HF8 is structurally dormant by default. Passing a
    non-empty ``feature_definitions`` / ``required_feature_names`` is
    how tests exercise HF8's logic directly without touching the frozen,
    empty module-level FEATURE_DEFINITIONS registry.
    """

    fired = (
        _identity_conditions(identity_resolution)
        + _bar_conditions(bar, bar_corrupted)
        + _regime_conditions(regime_state)
        + _feature_warm_up_conditions(features)
        + _execution_quality_conditions(features)
        + _corporate_action_conditions(
            bar, bar_corrupted, required_feature_names, adjusted_series_available_for, feature_definitions
        )
    )

    quality = _classify_quality(fired)
    decision = _QUALITY_TO_DECISION[quality]

    return EvidenceClassification(
        evidence_quality=quality,
        decision_type=decision,
        fired_conditions=fired,
        evidence_policy_version=EVIDENCE_POLICY_VERSION,
        feature_definition_version=FEATURE_DEFINITION_VERSION,
    )


def _classify_quality(fired: Tuple[EvidenceConditionId, ...]) -> EvidenceQuality:
    """The complete Evidence Quality lookup. Membership-only — no
    counting, no weighting, no numeric threshold anywhere in this
    function. Severity is determined solely by WHICH fixed category
    (hard-fail / core soft-gap / auxiliary soft-gap) a fired condition
    belongs to, never by how many conditions fired."""

    fired_set = set(fired)
    if fired_set & _HARD_FAIL_IDS:
        return EvidenceQuality.INSUFFICIENT
    if fired_set & _CORE_SOFT_GAP_IDS:
        return EvidenceQuality.LOW
    if fired_set & _AUXILIARY_SOFT_GAP_IDS:
        return EvidenceQuality.MEDIUM
    return EvidenceQuality.HIGH


# --- EvidenceSummary: presentation/audit artifact --------------------
#
# EvidenceSummary is a SEPARATE artifact from EvidenceClassification. It
# is descriptive, presentation/audit data only -- it exists to make the
# same underlying evidence state human-readable (for the eventual
# Telegram package and for audit review), never to compute or influence
# evidence_quality, decision_type, confidence, or risk. Those are
# computed independently by classify_evidence()/_classify_quality()
# above, from the same shared per-dimension condition helpers, and
# EvidenceSummary has no code path that feeds back into them.
#
# D0026-EV-INV-1 / D0026-EV-INV-2 apply to EvidenceSummary exactly as
# they apply to EvidenceClassification: it is read-only,
# downstream-of-ranking metadata that must never be consumed by Stage
# A-H, ranking, or strategy-mechanics logic, and it has no effect on
# trading desirability.


class DimensionEvidenceState(Enum):
    """The state of ONE of the six evidence dimensions, derived purely
    from which condition(s) (if any) fired for that dimension -- never
    counted, weighted, or scored."""

    RESOLVED = "resolved"  # no gap: complete, usable evidence
    GAP = "gap"  # a soft-gap condition fired: evidence present but degraded
    MISSING = "missing"  # a hard-fail condition fired: evidence absent or unusable


def _dimension_state(conditions: Tuple[EvidenceConditionId, ...]) -> DimensionEvidenceState:
    fired_set = set(conditions)
    if fired_set & _HARD_FAIL_IDS:
        return DimensionEvidenceState.MISSING
    if conditions:
        return DimensionEvidenceState.GAP
    return DimensionEvidenceState.RESOLVED


@dataclass(frozen=True)
class EvidenceDimension:
    """One of the six evidence dimensions: its state, and exactly which
    condition(s) (if any) produced that state."""

    state: DimensionEvidenceState
    fired_conditions: Tuple[EvidenceConditionId, ...]


_HARD_FAIL_DESCRIPTIONS = {
    EvidenceConditionId.HF1_IDENTITY_UNRESOLVED: (
        "Identity could not be resolved for this ticker on this date (no matching alias)."
    ),
    EvidenceConditionId.HF2_IDENTITY_AMBIGUOUS: (
        "Identity resolution was ambiguous (multiple candidate securities matched)."
    ),
    EvidenceConditionId.HF3_BAR_MISSING: "No price/volume bar was supplied for this date.",
    EvidenceConditionId.HF4_BAR_CORRUPTED: "The supplied price/volume bar was flagged corrupted.",
    EvidenceConditionId.HF5_REGIME_UNKNOWN: "Regime label is UNKNOWN for this date.",
    EvidenceConditionId.HF6_FEATURES_NOT_COMPUTED: "No computed features were supplied for this date.",
    EvidenceConditionId.HF7_ADJUSTMENT_CONVENTION_UNKNOWN: (
        "The bar's split/dividend adjustment convention is UNKNOWN."
    ),
    EvidenceConditionId.HF8_REQUIRED_FEATURE_NEEDS_ADJUSTED_DATA_BUT_ONLY_RAW_AVAILABLE: (
        "A required feature is declared to need adjusted price data, but only a RAW bar is available."
    ),
}
"""Fixed, factual, non-narrative description text keyed by hard-fail
condition ID. This is a static lookup table, not generated prose -- the
same table classify_evidence() implicitly relies on when it fires these
condition IDs."""

_SOFT_GAP_DESCRIPTIONS = {
    EvidenceConditionId.SG1_IDENTITY_CORROBORATED_RANGE: (
        "Identity is resolved via a corroborated date range, not a direct CIK match."
    ),
    EvidenceConditionId.SG2_IDENTITY_PROVISIONAL: (
        "Identity resolution is provisional (lowest confidence tier, ticker alone)."
    ),
    EvidenceConditionId.SG3_FEATURES_INSUFFICIENT_WARMUP: (
        "Computed features do not yet have sufficient warm-up history."
    ),
    EvidenceConditionId.SG4_EXECUTION_QUALITY_RANGE_PROXY: (
        "Execution-quality evidence is a range-based proxy, not a true quote."
    ),
}
"""Fixed, factual, non-narrative description text keyed by soft-gap
condition ID."""


def _condition_descriptions(conditions: Tuple[EvidenceConditionId, ...]) -> Tuple[str, ...]:
    descriptions = []
    for condition in conditions:
        if condition in _HARD_FAIL_DESCRIPTIONS:
            descriptions.append(_HARD_FAIL_DESCRIPTIONS[condition])
        else:
            descriptions.append(_SOFT_GAP_DESCRIPTIONS[condition])
    return tuple(descriptions)


def _identity_collected_description(identity_resolution: IdentityResolution) -> str:
    assert identity_resolution.identity is not None
    return (
        "Identity resolved via CIK match (RESOLVED_CIK)."
        if identity_resolution.identity.confidence is IdentityConfidence.RESOLVED_CIK
        else "Identity resolved with no soft gap."
    )


def _bar_collected_description() -> str:
    return "A price/volume bar was supplied for this date and was not flagged corrupted."


def _regime_collected_description(regime_state: RegimeState) -> str:
    return f"Regime label is known for this date: {regime_state.label.value}."


def _execution_quality_collected_description() -> str:
    return "Execution-quality evidence is a true quote (not a range-based proxy)."


def _feature_warm_up_collected_description() -> str:
    return "Computed features were supplied for this date with sufficient warm-up history."


def _corporate_action_collected_description(bar: MarketDataBar) -> str:
    return f"Bar adjustment convention is known: {bar.adjustment.value}."


def _source_references(
    identity_resolution: IdentityResolution,
    bar: Optional[MarketDataBar],
    features: Optional[DailySecurityFeatures],
) -> Tuple[str, ...]:
    """Collects the real source_reference values already present on the
    supplied evidence objects. Never fabricates a reference; only
    relays what was actually supplied, de-duplicated, order-preserved."""

    candidates = []
    if identity_resolution.outcome is ResolutionOutcome.RESOLVED and identity_resolution.alias is not None:
        if identity_resolution.alias.source_reference:
            candidates.append(identity_resolution.alias.source_reference)
    if bar is not None and bar.source_reference:
        candidates.append(bar.source_reference)
    if features is not None and features.source_reference:
        candidates.append(features.source_reference)

    seen = set()
    unique = []
    for reference in candidates:
        if reference not in seen:
            seen.add(reference)
            unique.append(reference)
    return tuple(unique)


@dataclass(frozen=True)
class EvidenceSummary:
    """The frozen v3-final presentation/audit artifact. Describes the
    SAME underlying evidence as EvidenceClassification.fired_conditions
    (both are built by build_evidence_summary()/classify_evidence() from
    the identical per-dimension condition helpers above) but in a
    structured, human-reviewable shape suitable for the eventual
    Telegram package and audit review.

    Contains NO score, weight, probability, confidence percentage, risk
    score, threshold, or numeric evidence-quality calculation of any
    kind. Every list field below is built mechanically from fixed,
    factual description tables keyed by the same EvidenceConditionId
    values classify_evidence() already fires -- never invented prose,
    never derived from imaginary data, never a count of how many items
    are present.

    recommendation_reasons / counter_arguments are, by construction,
    restatements of evidence_collected and (evidence_missing +
    uncertainties) respectively, framed as support-vs-caution. This
    module has no additional narrative-generation logic and will not
    acquire one without a new, separately reviewed version of this
    frozen design: it never invents a reason or counter-argument beyond
    what the evidence conditions above already establish.
    """

    identity: EvidenceDimension
    price_volume: EvidenceDimension
    regime: EvidenceDimension
    execution_quality: EvidenceDimension
    feature_warm_up: EvidenceDimension
    corporate_action_convention: EvidenceDimension

    evidence_collected: Tuple[str, ...]
    evidence_missing: Tuple[str, ...]
    uncertainties: Tuple[str, ...]
    recommendation_reasons: Tuple[str, ...]
    counter_arguments: Tuple[str, ...]
    source_references: Tuple[str, ...]

    evidence_policy_version: str
    feature_definition_version: str


def build_evidence_summary(
    *,
    identity_resolution: IdentityResolution,
    bar: Optional[MarketDataBar],
    bar_corrupted: bool,
    regime_state: RegimeState,
    features: Optional[DailySecurityFeatures],
    required_feature_names: Tuple[str, ...] = (),
    adjusted_series_available_for: Tuple[str, ...] = (),
    feature_definitions: Tuple[FeatureDefinition, ...] = FEATURE_DEFINITIONS,
) -> EvidenceSummary:
    """Build the EvidenceSummary presentation/audit artifact from the
    SAME raw evidence inputs classify_evidence() takes, using the SAME
    per-dimension condition helpers. This is what guarantees
    EvidenceSummary and EvidenceClassification.fired_conditions always
    describe identical underlying evidence: neither is derived from the
    other's output, and no field here can diverge from the condition set
    classify_evidence() would compute for the identical inputs.

    Pure function -- stateless, no access to any prior classification or
    summary, same as classify_evidence().
    """

    identity_conditions = _identity_conditions(identity_resolution)
    bar_conditions = _bar_conditions(bar, bar_corrupted)
    regime_conditions = _regime_conditions(regime_state)
    feature_warm_up_conditions = _feature_warm_up_conditions(features)
    execution_quality_conditions = _execution_quality_conditions(features)
    corporate_action_conditions = _corporate_action_conditions(
        bar, bar_corrupted, required_feature_names, adjusted_series_available_for, feature_definitions
    )

    dimensions = (
        ("identity", identity_conditions, lambda: _identity_collected_description(identity_resolution)),
        ("price_volume", bar_conditions, _bar_collected_description),
        ("regime", regime_conditions, lambda: _regime_collected_description(regime_state)),
        ("execution_quality", execution_quality_conditions, _execution_quality_collected_description),
        ("feature_warm_up", feature_warm_up_conditions, _feature_warm_up_collected_description),
        (
            "corporate_action_convention",
            corporate_action_conditions,
            (lambda: _corporate_action_collected_description(bar)) if bar is not None else (lambda: ""),
        ),
    )

    dimension_objects = {}
    evidence_collected: list = []
    evidence_missing: list = []
    uncertainties: list = []

    for name, conditions, collected_description_fn in dimensions:
        state = _dimension_state(conditions)
        dimension_objects[name] = EvidenceDimension(state=state, fired_conditions=conditions)
        if state is DimensionEvidenceState.RESOLVED:
            description = collected_description_fn()
            if description:
                evidence_collected.append(description)
        elif state is DimensionEvidenceState.MISSING:
            evidence_missing.extend(_condition_descriptions(conditions))
        else:  # GAP
            uncertainties.extend(_condition_descriptions(conditions))

    evidence_collected_t = tuple(evidence_collected)
    evidence_missing_t = tuple(evidence_missing)
    uncertainties_t = tuple(uncertainties)

    return EvidenceSummary(
        identity=dimension_objects["identity"],
        price_volume=dimension_objects["price_volume"],
        regime=dimension_objects["regime"],
        execution_quality=dimension_objects["execution_quality"],
        feature_warm_up=dimension_objects["feature_warm_up"],
        corporate_action_convention=dimension_objects["corporate_action_convention"],
        evidence_collected=evidence_collected_t,
        evidence_missing=evidence_missing_t,
        uncertainties=uncertainties_t,
        recommendation_reasons=evidence_collected_t,
        counter_arguments=evidence_missing_t + uncertainties_t,
        source_references=_source_references(identity_resolution, bar, features),
        evidence_policy_version=EVIDENCE_POLICY_VERSION,
        feature_definition_version=FEATURE_DEFINITION_VERSION,
    )
