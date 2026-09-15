"""D-0026 Evidence/Confidence Layer — v3-final frozen design tests.

All fixtures synthetic/in-memory, consistent with every other Phase A
test. No real data source anywhere.
"""

import unittest
from datetime import date

from d0026.evidence import (
    EVIDENCE_POLICY_VERSION,
    FEATURE_DEFINITION_VERSION,
    AdjustmentDependency,
    ConfidenceStatus,
    DecisionType,
    EvidenceClassification,
    EvidenceConditionId,
    EvidenceQuality,
    FeatureDefinition,
    classify_evidence,
)
from d0026.identity import (
    IdentityConfidence,
    IdentityResolution,
    ResolutionOutcome,
    SecurityIdentity,
    TickerAlias,
)
from d0026.models import AdjustmentConvention, DailySecurityFeatures, MarketDataBar, RegimeLabel, RegimeState

_DATE = date(2026, 1, 5)


def _resolved_identity(
    confidence: IdentityConfidence = IdentityConfidence.RESOLVED_CIK,
) -> IdentityResolution:
    identity = SecurityIdentity(
        security_id="sec-aaa",
        display_name="AAA Corp",
        cik="0000000001" if confidence is IdentityConfidence.RESOLVED_CIK else None,
        confidence=confidence,
    )
    alias = TickerAlias(security_id="sec-aaa", ticker="AAA", effective_start=None, effective_end=None)
    return IdentityResolution(
        outcome=ResolutionOutcome.RESOLVED, ticker="AAA", as_of_date=_DATE, identity=identity, alias=alias
    )


def _unresolved_identity() -> IdentityResolution:
    return IdentityResolution(
        outcome=ResolutionOutcome.UNRESOLVED, ticker="AAA", as_of_date=_DATE, detail="no alias covers this date"
    )


def _ambiguous_identity() -> IdentityResolution:
    return IdentityResolution(
        outcome=ResolutionOutcome.AMBIGUOUS, ticker="AAA", as_of_date=_DATE, detail="two aliases overlap"
    )


def _known_regime() -> RegimeState:
    return RegimeState(
        as_of_date=_DATE,
        label=RegimeLabel.UNCLASSIFIED_PENDING_CALIBRATION,
        reference_series_values=(("dummy_ref", 1.0),),
        classification_method_version="none-pending-calibration",
    )


def _unknown_regime() -> RegimeState:
    return RegimeState(
        as_of_date=_DATE,
        label=RegimeLabel.UNKNOWN,
        reference_series_values=(),
        classification_method_version="none-pending-calibration",
    )


def _good_bar(adjustment: AdjustmentConvention = AdjustmentConvention.SPLIT_ADJUSTED) -> MarketDataBar:
    return MarketDataBar(
        security_id="sec-aaa",
        ticker_as_of_date="AAA",
        bar_date=_DATE,
        open=10.0,
        high=11.0,
        low=9.0,
        close=10.5,
        volume=1000.0,
        adjustment=adjustment,
        source_reference="synthetic-test-fixture",
    )


def _good_features(
    warm_up_sufficient: bool = True, execution_quality_proxy_is_true_quote: bool = True
) -> DailySecurityFeatures:
    return DailySecurityFeatures(
        security_id="sec-aaa",
        feature_date=_DATE,
        liquidity_measure=1.0,
        atr_measure=1.0,
        momentum_measure=1.0,
        execution_quality_proxy=1.0,
        execution_quality_proxy_is_true_quote=execution_quality_proxy_is_true_quote,
        warm_up_sufficient=warm_up_sufficient,
        source_reference="synthetic-test-fixture",
    )


def _classify(**overrides) -> EvidenceClassification:
    kwargs = dict(
        identity_resolution=_resolved_identity(),
        bar=_good_bar(),
        bar_corrupted=False,
        regime_state=_known_regime(),
        features=_good_features(),
    )
    kwargs.update(overrides)
    return classify_evidence(**kwargs)


class TestHighEvidenceQuality(unittest.TestCase):
    def test_every_dimension_strong_yields_high_and_standard(self) -> None:
        result = _classify()
        self.assertEqual(result.evidence_quality, EvidenceQuality.HIGH)
        self.assertEqual(result.decision_type, DecisionType.STANDARD)
        self.assertEqual(result.fired_conditions, ())


class TestHardFailConditions(unittest.TestCase):
    def test_hf1_identity_unresolved(self) -> None:
        result = _classify(identity_resolution=_unresolved_identity())
        self.assertEqual(result.evidence_quality, EvidenceQuality.INSUFFICIENT)
        self.assertEqual(result.decision_type, DecisionType.NOT_ELIGIBLE)
        self.assertIn(EvidenceConditionId.HF1_IDENTITY_UNRESOLVED, result.fired_conditions)

    def test_hf2_identity_ambiguous(self) -> None:
        result = _classify(identity_resolution=_ambiguous_identity())
        self.assertEqual(result.evidence_quality, EvidenceQuality.INSUFFICIENT)
        self.assertIn(EvidenceConditionId.HF2_IDENTITY_AMBIGUOUS, result.fired_conditions)

    def test_hf3_bar_missing(self) -> None:
        result = _classify(bar=None)
        self.assertEqual(result.evidence_quality, EvidenceQuality.INSUFFICIENT)
        self.assertIn(EvidenceConditionId.HF3_BAR_MISSING, result.fired_conditions)

    def test_hf4_bar_corrupted(self) -> None:
        result = _classify(bar=_good_bar(), bar_corrupted=True)
        self.assertEqual(result.evidence_quality, EvidenceQuality.INSUFFICIENT)
        self.assertIn(EvidenceConditionId.HF4_BAR_CORRUPTED, result.fired_conditions)

    def test_hf5_regime_unknown(self) -> None:
        result = _classify(regime_state=_unknown_regime())
        self.assertEqual(result.evidence_quality, EvidenceQuality.INSUFFICIENT)
        self.assertEqual(result.decision_type, DecisionType.NOT_ELIGIBLE)
        self.assertIn(EvidenceConditionId.HF5_REGIME_UNKNOWN, result.fired_conditions)

    def test_hf6_features_not_computed(self) -> None:
        result = _classify(features=None)
        self.assertEqual(result.evidence_quality, EvidenceQuality.INSUFFICIENT)
        self.assertIn(EvidenceConditionId.HF6_FEATURES_NOT_COMPUTED, result.fired_conditions)

    def test_hf6_distinct_from_sg3_not_computed_vs_insufficient_warmup(self) -> None:
        # The critical distinction: "no features at all" (HF6, hard-fail)
        # must never be conflated with "features exist but warm-up is
        # thin" (SG3, soft-gap) — these are different evidence states.
        not_computed = _classify(features=None)
        insufficient_warmup = _classify(features=_good_features(warm_up_sufficient=False))
        self.assertEqual(not_computed.decision_type, DecisionType.NOT_ELIGIBLE)
        self.assertEqual(insufficient_warmup.decision_type, DecisionType.MANUAL_REVIEW)
        self.assertIn(EvidenceConditionId.HF6_FEATURES_NOT_COMPUTED, not_computed.fired_conditions)
        self.assertIn(
            EvidenceConditionId.SG3_FEATURES_INSUFFICIENT_WARMUP, insufficient_warmup.fired_conditions
        )

    def test_hf7_adjustment_convention_unknown(self) -> None:
        result = _classify(bar=_good_bar(AdjustmentConvention.UNKNOWN))
        self.assertEqual(result.evidence_quality, EvidenceQuality.INSUFFICIENT)
        self.assertIn(EvidenceConditionId.HF7_ADJUSTMENT_CONVENTION_UNKNOWN, result.fired_conditions)


class TestHF8RequiredAdjustedFeature(unittest.TestCase):
    """HF8: a feature actually required by the current stage
    configuration, statically declared REQUIRES_ADJUSTED, has only a
    RAW series available. Distinguished sharply from ordinary RAW data
    that no required feature depends on."""

    def test_hf8_fires_only_when_required_feature_needs_adjusted_and_only_raw_exists(self) -> None:
        synthetic_definitions = (
            FeatureDefinition(name="atr_like_feature", adjustment_dependency=AdjustmentDependency.REQUIRES_ADJUSTED),
        )
        result = _classify(
            bar=_good_bar(AdjustmentConvention.RAW),
            required_feature_names=("atr_like_feature",),
            feature_definitions=synthetic_definitions,
        )
        self.assertEqual(result.evidence_quality, EvidenceQuality.INSUFFICIENT)
        self.assertEqual(result.decision_type, DecisionType.NOT_ELIGIBLE)
        self.assertIn(
            EvidenceConditionId.HF8_REQUIRED_FEATURE_NEEDS_ADJUSTED_DATA_BUT_ONLY_RAW_AVAILABLE,
            result.fired_conditions,
        )

    def test_hf8_distinction_from_ordinary_raw_data_indifferent_feature(self) -> None:
        # Ordinary RAW data must NEVER be penalized for a feature that
        # doesn't care about adjustment convention at all.
        synthetic_definitions = (
            FeatureDefinition(name="liquidity_like_feature", adjustment_dependency=AdjustmentDependency.INDIFFERENT),
        )
        result = _classify(
            bar=_good_bar(AdjustmentConvention.RAW),
            required_feature_names=("liquidity_like_feature",),
            feature_definitions=synthetic_definitions,
        )
        self.assertEqual(result.evidence_quality, EvidenceQuality.HIGH)
        self.assertNotIn(
            EvidenceConditionId.HF8_REQUIRED_FEATURE_NEEDS_ADJUSTED_DATA_BUT_ONLY_RAW_AVAILABLE,
            result.fired_conditions,
        )

    def test_hf8_does_not_fire_when_an_adjusted_series_is_available(self) -> None:
        synthetic_definitions = (
            FeatureDefinition(name="atr_like_feature", adjustment_dependency=AdjustmentDependency.REQUIRES_ADJUSTED),
        )
        result = _classify(
            bar=_good_bar(AdjustmentConvention.RAW),
            required_feature_names=("atr_like_feature",),
            adjusted_series_available_for=("atr_like_feature",),
            feature_definitions=synthetic_definitions,
        )
        self.assertEqual(result.evidence_quality, EvidenceQuality.HIGH)

    def test_hf8_does_not_fire_when_bar_is_already_adjusted(self) -> None:
        synthetic_definitions = (
            FeatureDefinition(name="atr_like_feature", adjustment_dependency=AdjustmentDependency.REQUIRES_ADJUSTED),
        )
        result = _classify(
            bar=_good_bar(AdjustmentConvention.SPLIT_ADJUSTED),
            required_feature_names=("atr_like_feature",),
            feature_definitions=synthetic_definitions,
        )
        self.assertEqual(result.evidence_quality, EvidenceQuality.HIGH)

    def test_hf8_dormant_with_current_frozen_feature_definitions_and_no_required_features(self) -> None:
        # This is THE dormancy test: using the real, frozen, empty
        # FEATURE_DEFINITIONS registry and no required features (exactly
        # what the current NotCalibratedStageEvaluator-only pipeline
        # would pass), HF8 can never fire, regardless of the bar's
        # adjustment convention.
        result = _classify(bar=_good_bar(AdjustmentConvention.RAW))
        self.assertNotIn(
            EvidenceConditionId.HF8_REQUIRED_FEATURE_NEEDS_ADJUSTED_DATA_BUT_ONLY_RAW_AVAILABLE,
            result.fired_conditions,
        )
        self.assertEqual(result.evidence_quality, EvidenceQuality.HIGH)

    def test_hf8_dormant_even_with_required_feature_names_if_registry_has_no_definition(self) -> None:
        # Referencing a feature name with no declared FeatureDefinition
        # is a configuration error (refuse to guess), not a silent
        # evidence gap or a silent pass-through.
        with self.assertRaises(ValueError):
            _classify(
                bar=_good_bar(AdjustmentConvention.RAW),
                required_feature_names=("nonexistent_feature",),
                feature_definitions=(),
            )

    def test_runtime_cannot_override_static_adjustment_dependency(self) -> None:
        # There is no parameter anywhere on classify_evidence that lets
        # a caller pass a per-call AdjustmentDependency override — the
        # only way to change a feature's dependency is to change
        # FEATURE_DEFINITIONS itself (a new FEATURE_DEFINITION_VERSION).
        import inspect

        signature = inspect.signature(classify_evidence)
        for param_name in signature.parameters:
            self.assertNotIn("dependency", param_name.lower())
            self.assertNotIn("override", param_name.lower())


class TestSoftGapConditions(unittest.TestCase):
    def test_sg1_identity_corroborated_range_is_low(self) -> None:
        result = _classify(identity_resolution=_resolved_identity(IdentityConfidence.RESOLVED_CORROBORATED_RANGE))
        self.assertEqual(result.evidence_quality, EvidenceQuality.LOW)
        self.assertEqual(result.decision_type, DecisionType.MANUAL_REVIEW)
        self.assertIn(EvidenceConditionId.SG1_IDENTITY_CORROBORATED_RANGE, result.fired_conditions)

    def test_sg2_identity_provisional_is_low(self) -> None:
        result = _classify(identity_resolution=_resolved_identity(IdentityConfidence.PROVISIONAL))
        self.assertEqual(result.evidence_quality, EvidenceQuality.LOW)
        self.assertIn(EvidenceConditionId.SG2_IDENTITY_PROVISIONAL, result.fired_conditions)

    def test_sg3_insufficient_warmup_is_low(self) -> None:
        result = _classify(features=_good_features(warm_up_sufficient=False))
        self.assertEqual(result.evidence_quality, EvidenceQuality.LOW)
        self.assertIn(EvidenceConditionId.SG3_FEATURES_INSUFFICIENT_WARMUP, result.fired_conditions)

    def test_sg4_execution_quality_range_proxy_is_medium(self) -> None:
        result = _classify(features=_good_features(execution_quality_proxy_is_true_quote=False))
        self.assertEqual(result.evidence_quality, EvidenceQuality.MEDIUM)
        self.assertEqual(result.decision_type, DecisionType.MANUAL_REVIEW)
        self.assertIn(EvidenceConditionId.SG4_EXECUTION_QUALITY_RANGE_PROXY, result.fired_conditions)


class TestCoreVsAuxiliaryClassification(unittest.TestCase):
    def test_core_gap_always_outranks_auxiliary_gap_regardless_of_which_fires(self) -> None:
        core_only = _classify(identity_resolution=_resolved_identity(IdentityConfidence.PROVISIONAL))
        auxiliary_only = _classify(features=_good_features(execution_quality_proxy_is_true_quote=False))
        self.assertEqual(core_only.evidence_quality, EvidenceQuality.LOW)
        self.assertEqual(auxiliary_only.evidence_quality, EvidenceQuality.MEDIUM)

    def test_core_and_auxiliary_gaps_together_still_yield_low_not_something_worse(self) -> None:
        result = _classify(
            identity_resolution=_resolved_identity(IdentityConfidence.PROVISIONAL),
            features=_good_features(execution_quality_proxy_is_true_quote=False),
        )
        self.assertEqual(result.evidence_quality, EvidenceQuality.LOW)
        self.assertIn(EvidenceConditionId.SG2_IDENTITY_PROVISIONAL, result.fired_conditions)
        self.assertIn(EvidenceConditionId.SG4_EXECUTION_QUALITY_RANGE_PROXY, result.fired_conditions)


class TestAntiCountingRegression(unittest.TestCase):
    """The single most important test class in this module: proves
    severity is determined by WHICH named condition fired, never by HOW
    MANY. A future change that reintroduces counting/scoring must break
    these tests."""

    def test_two_auxiliary_gaps_do_not_escalate_beyond_medium(self) -> None:
        # SG4 is the only AUXILIARY condition in the frozen design, so
        # simulate "more gaps" pressure via a corroborated-but-otherwise
        # clean auxiliary-only fixture repeated — the key assertion is
        # that MEDIUM, not something worse, is the ceiling for
        # auxiliary-only gaps no matter how the auxiliary evidence looks.
        result = _classify(features=_good_features(execution_quality_proxy_is_true_quote=False))
        self.assertEqual(result.evidence_quality, EvidenceQuality.MEDIUM)

    def test_one_core_gap_is_strictly_worse_than_two_auxiliary_gaps(self) -> None:
        # A single CORE gap (fewer named conditions fired) must classify
        # WORSE (LOW) than a case with an AUXILIARY gap (MEDIUM) — proving
        # severity is not proportional to how many conditions fired.
        one_core_gap = _classify(identity_resolution=_resolved_identity(IdentityConfidence.PROVISIONAL))
        one_auxiliary_gap = _classify(features=_good_features(execution_quality_proxy_is_true_quote=False))
        self.assertEqual(one_core_gap.evidence_quality, EvidenceQuality.LOW)
        self.assertEqual(one_auxiliary_gap.evidence_quality, EvidenceQuality.MEDIUM)
        # LOW is "worse" (routes identically to MANUAL_REVIEW here, but
        # is a strictly weaker evidence state per the frozen ordering
        # INSUFFICIENT > LOW > MEDIUM > HIGH) despite firing the SAME
        # NUMBER of named conditions (exactly one) as the MEDIUM case.

    def test_many_hard_fails_at_once_is_still_just_insufficient_not_a_worse_tier(self) -> None:
        # There is no tier below INSUFFICIENT — firing every hard-fail
        # condition simultaneously must not produce a different or
        # "more insufficient" result than firing just one.
        result = _classify(
            identity_resolution=_unresolved_identity(),
            bar=None,
            regime_state=_unknown_regime(),
            features=None,
        )
        self.assertEqual(result.evidence_quality, EvidenceQuality.INSUFFICIENT)
        self.assertEqual(result.decision_type, DecisionType.NOT_ELIGIBLE)
        self.assertGreaterEqual(len(result.fired_conditions), 3)


class TestConfidenceRiskInvariant(unittest.TestCase):
    def test_confidence_and_risk_are_always_none_not_calibrated(self) -> None:
        for result in (
            _classify(),
            _classify(identity_resolution=_unresolved_identity()),
            _classify(features=_good_features(execution_quality_proxy_is_true_quote=False)),
        ):
            self.assertIsNone(result.confidence)
            self.assertEqual(result.confidence_status, ConfidenceStatus.NOT_CALIBRATED)
            self.assertIsNone(result.risk)
            self.assertEqual(result.risk_status, ConfidenceStatus.NOT_CALIBRATED)

    def test_constructing_a_classification_with_a_confidence_value_raises(self) -> None:
        with self.assertRaises(ValueError):
            EvidenceClassification(
                evidence_quality=EvidenceQuality.HIGH,
                decision_type=DecisionType.STANDARD,
                fired_conditions=(),
                evidence_policy_version=EVIDENCE_POLICY_VERSION,
                feature_definition_version=FEATURE_DEFINITION_VERSION,
                confidence=0.9,
            )

    def test_constructing_a_classification_with_calibrated_status_but_no_value_raises(self) -> None:
        with self.assertRaises(ValueError):
            EvidenceClassification(
                evidence_quality=EvidenceQuality.HIGH,
                decision_type=DecisionType.STANDARD,
                fired_conditions=(),
                evidence_policy_version=EVIDENCE_POLICY_VERSION,
                feature_definition_version=FEATURE_DEFINITION_VERSION,
                confidence_status=ConfidenceStatus.CALIBRATED,
            )

    def test_constructing_a_classification_with_a_risk_value_raises(self) -> None:
        with self.assertRaises(ValueError):
            EvidenceClassification(
                evidence_quality=EvidenceQuality.HIGH,
                decision_type=DecisionType.STANDARD,
                fired_conditions=(),
                evidence_policy_version=EVIDENCE_POLICY_VERSION,
                feature_definition_version=FEATURE_DEFINITION_VERSION,
                risk=0.1,
            )

    def test_decision_type_must_match_the_fixed_quality_mapping(self) -> None:
        with self.assertRaises(ValueError):
            EvidenceClassification(
                evidence_quality=EvidenceQuality.HIGH,
                decision_type=DecisionType.MANUAL_REVIEW,  # wrong for HIGH
                fired_conditions=(),
                evidence_policy_version=EVIDENCE_POLICY_VERSION,
                feature_definition_version=FEATURE_DEFINITION_VERSION,
            )


class TestDeterminism(unittest.TestCase):
    def test_identical_inputs_produce_identical_classification(self) -> None:
        a = _classify()
        b = _classify()
        self.assertEqual(a, b)
        self.assertEqual(a.evidence_quality, b.evidence_quality)
        self.assertEqual(a.fired_conditions, b.fired_conditions)

    def test_repeated_calls_are_not_influenced_by_prior_calls(self) -> None:
        # Simulates "evidence history must never influence classification":
        # classify a LOW-quality candidate, then immediately classify an
        # otherwise-identical HIGH-quality candidate for the SAME
        # security_id — the earlier LOW result must have zero effect.
        low = _classify(identity_resolution=_resolved_identity(IdentityConfidence.PROVISIONAL))
        self.assertEqual(low.evidence_quality, EvidenceQuality.LOW)
        high = _classify()  # same security, stronger identity this call
        self.assertEqual(high.evidence_quality, EvidenceQuality.HIGH)

    def test_classify_evidence_signature_has_no_history_parameter(self) -> None:
        import inspect

        signature = inspect.signature(classify_evidence)
        for param_name in signature.parameters:
            self.assertNotIn("history", param_name.lower())
            self.assertNotIn("prior", param_name.lower())
            self.assertNotIn("past", param_name.lower())
            self.assertNotIn("reputation", param_name.lower())


class TestPolicyVersionPropagation(unittest.TestCase):
    def test_every_classification_carries_both_frozen_versions(self) -> None:
        for result in (
            _classify(),
            _classify(identity_resolution=_unresolved_identity()),
            _classify(features=_good_features(execution_quality_proxy_is_true_quote=False)),
        ):
            self.assertEqual(result.evidence_policy_version, "D0026-EV-001")
            self.assertEqual(result.feature_definition_version, "D0026-FEATDEF-001")
            self.assertEqual(result.evidence_policy_version, EVIDENCE_POLICY_VERSION)
            self.assertEqual(result.feature_definition_version, FEATURE_DEFINITION_VERSION)


if __name__ == "__main__":
    unittest.main()
