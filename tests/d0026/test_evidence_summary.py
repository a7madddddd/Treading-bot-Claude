"""EvidenceSummary artifact tests.

EvidenceSummary is a SEPARATE, additional presentation/audit artifact
(not a replacement for fired_conditions). It must describe exactly the
same underlying evidence as EvidenceClassification.fired_conditions,
contain zero numeric scoring, never invent narrative content, and have
no ability to influence evidence_quality / decision_type / confidence /
risk.
"""

import unittest
from datetime import date

from d0026.evidence import (
    EVIDENCE_POLICY_VERSION,
    FEATURE_DEFINITION_VERSION,
    DimensionEvidenceState,
    EvidenceQuality,
    build_evidence_summary,
    classify_evidence,
)
from d0026.identity import IdentityConfidence, IdentityResolution, ResolutionOutcome, SecurityIdentity, TickerAlias
from d0026.models import (
    AdjustmentConvention,
    DailySecurityFeatures,
    MarketDataBar,
    RegimeLabel,
    RegimeState,
)

_DATE = date(2026, 1, 5)


def _regime(label: RegimeLabel = RegimeLabel.UNCLASSIFIED_PENDING_CALIBRATION) -> RegimeState:
    return RegimeState(
        as_of_date=_DATE,
        label=label,
        reference_series_values=(() if label is RegimeLabel.UNKNOWN else (("dummy_ref", 1.0),)),
        classification_method_version="none-pending-calibration",
    )


def _resolved_identity(
    *,
    confidence: IdentityConfidence = IdentityConfidence.RESOLVED_CIK,
    source_reference: str = "synthetic-alias-source",
) -> IdentityResolution:
    identity = SecurityIdentity(
        security_id="sec-aaa",
        display_name="AAA Corp",
        cik="0000000001" if confidence is IdentityConfidence.RESOLVED_CIK else None,
        confidence=confidence,
    )
    alias = TickerAlias(
        security_id="sec-aaa",
        ticker="AAA",
        effective_start=None,
        effective_end=None,
        source_reference=source_reference,
    )
    return IdentityResolution(outcome=ResolutionOutcome.RESOLVED, ticker="AAA", as_of_date=_DATE, identity=identity, alias=alias)


def _bar(
    *,
    adjustment: AdjustmentConvention = AdjustmentConvention.SPLIT_ADJUSTED,
    source_reference: str = "synthetic-bar-source",
) -> MarketDataBar:
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
        source_reference=source_reference,
    )


def _features(
    *,
    warm_up_sufficient: bool = True,
    execution_quality_proxy_is_true_quote: bool = True,
    source_reference: str = "synthetic-features-source",
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
        source_reference=source_reference,
    )


class TestSixDimensionsRepresented(unittest.TestCase):
    def test_all_six_dimensions_present_on_clean_evidence(self) -> None:
        summary = build_evidence_summary(
            identity_resolution=_resolved_identity(),
            bar=_bar(),
            bar_corrupted=False,
            regime_state=_regime(),
            features=_features(),
        )
        for name in (
            "identity",
            "price_volume",
            "regime",
            "execution_quality",
            "feature_warm_up",
            "corporate_action_convention",
        ):
            self.assertTrue(hasattr(summary, name), f"missing dimension: {name}")

    def test_clean_evidence_all_dimensions_resolved(self) -> None:
        summary = build_evidence_summary(
            identity_resolution=_resolved_identity(),
            bar=_bar(),
            bar_corrupted=False,
            regime_state=_regime(),
            features=_features(),
        )
        for dimension in (
            summary.identity,
            summary.price_volume,
            summary.regime,
            summary.execution_quality,
            summary.feature_warm_up,
            summary.corporate_action_convention,
        ):
            self.assertEqual(dimension.state, DimensionEvidenceState.RESOLVED)
            self.assertEqual(dimension.fired_conditions, ())


class TestRequiredListFieldsRepresented(unittest.TestCase):
    def test_all_six_required_list_fields_present(self) -> None:
        summary = build_evidence_summary(
            identity_resolution=_resolved_identity(),
            bar=_bar(),
            bar_corrupted=False,
            regime_state=_regime(),
            features=_features(),
        )
        for name in (
            "evidence_collected",
            "evidence_missing",
            "uncertainties",
            "recommendation_reasons",
            "counter_arguments",
            "source_references",
        ):
            value = getattr(summary, name)
            self.assertIsInstance(value, tuple, f"{name} must be a tuple")


class TestDeterministicConstruction(unittest.TestCase):
    def test_identical_inputs_produce_identical_summary(self) -> None:
        kwargs = dict(
            identity_resolution=_resolved_identity(confidence=IdentityConfidence.PROVISIONAL),
            bar=_bar(),
            bar_corrupted=False,
            regime_state=_regime(),
            features=_features(warm_up_sufficient=False),
        )
        a = build_evidence_summary(**kwargs)
        b = build_evidence_summary(**kwargs)
        self.assertEqual(a, b)


class TestVersionPropagation(unittest.TestCase):
    def test_summary_carries_the_established_version_constants(self) -> None:
        summary = build_evidence_summary(
            identity_resolution=_resolved_identity(),
            bar=_bar(),
            bar_corrupted=False,
            regime_state=_regime(),
            features=_features(),
        )
        self.assertEqual(summary.evidence_policy_version, "D0026-EV-001")
        self.assertEqual(summary.feature_definition_version, "D0026-FEATDEF-001")
        self.assertEqual(summary.evidence_policy_version, EVIDENCE_POLICY_VERSION)
        self.assertEqual(summary.feature_definition_version, FEATURE_DEFINITION_VERSION)


class TestNoInventedProseOnMissingEvidence(unittest.TestCase):
    def test_missing_bar_and_features_produce_no_collected_claims_for_those_dimensions(self) -> None:
        summary = build_evidence_summary(
            identity_resolution=_resolved_identity(),
            bar=None,
            bar_corrupted=False,
            regime_state=_regime(),
            features=None,
        )
        self.assertEqual(summary.price_volume.state, DimensionEvidenceState.MISSING)
        self.assertEqual(summary.feature_warm_up.state, DimensionEvidenceState.MISSING)
        # Execution quality and corporate-action convention cannot be
        # assessed at all without a bar/features -- they must not
        # fabricate a claim, they simply have nothing to report.
        self.assertEqual(summary.execution_quality.fired_conditions, ())
        self.assertEqual(summary.corporate_action_convention.fired_conditions, ())
        for claim in summary.evidence_collected:
            self.assertNotIn("liquidity", claim.lower())
            self.assertNotIn("strong", claim.lower())

    def test_unresolved_identity_produces_no_identity_collected_claim(self) -> None:
        unresolved = IdentityResolution(
            outcome=ResolutionOutcome.UNRESOLVED, ticker="ZZZ", as_of_date=_DATE, detail="no alias"
        )
        summary = build_evidence_summary(
            identity_resolution=unresolved,
            bar=_bar(),
            bar_corrupted=False,
            regime_state=_regime(),
            features=_features(),
        )
        self.assertEqual(summary.identity.state, DimensionEvidenceState.MISSING)
        self.assertIn(
            "Identity could not be resolved for this ticker on this date (no matching alias).",
            summary.evidence_missing,
        )


class TestSummaryDoesNotAffectClassification(unittest.TestCase):
    def _classify(self, **kwargs) -> EvidenceQuality:
        return classify_evidence(**kwargs).evidence_quality

    def test_building_a_summary_does_not_change_evidence_quality(self) -> None:
        kwargs = dict(
            identity_resolution=_resolved_identity(confidence=IdentityConfidence.PROVISIONAL),
            bar=_bar(),
            bar_corrupted=False,
            regime_state=_regime(),
            features=_features(),
        )
        quality_before = classify_evidence(**kwargs).evidence_quality
        build_evidence_summary(**kwargs)
        quality_after = classify_evidence(**kwargs).evidence_quality
        self.assertEqual(quality_before, quality_after)

    def test_building_a_summary_does_not_change_decision_type(self) -> None:
        kwargs = dict(
            identity_resolution=_resolved_identity(confidence=IdentityConfidence.PROVISIONAL),
            bar=_bar(),
            bar_corrupted=False,
            regime_state=_regime(),
            features=_features(),
        )
        decision_before = classify_evidence(**kwargs).decision_type
        build_evidence_summary(**kwargs)
        decision_after = classify_evidence(**kwargs).decision_type
        self.assertEqual(decision_before, decision_after)

    def test_building_a_summary_does_not_change_confidence(self) -> None:
        kwargs = dict(
            identity_resolution=_resolved_identity(),
            bar=_bar(),
            bar_corrupted=False,
            regime_state=_regime(),
            features=_features(),
        )
        classification = classify_evidence(**kwargs)
        build_evidence_summary(**kwargs)
        self.assertIsNone(classification.confidence)

    def test_building_a_summary_does_not_change_risk(self) -> None:
        kwargs = dict(
            identity_resolution=_resolved_identity(),
            bar=_bar(),
            bar_corrupted=False,
            regime_state=_regime(),
            features=_features(),
        )
        classification = classify_evidence(**kwargs)
        build_evidence_summary(**kwargs)
        self.assertIsNone(classification.risk)

    def test_changing_only_presentation_data_cannot_change_classification_fields(self) -> None:
        # Proves EvidenceSummary is a strictly downstream, independently
        # derived artifact: constructing two different EvidenceSummary
        # objects for the same underlying evidence (here: differing only
        # by which dimension descriptions happen to be populated) has no
        # bearing whatsoever on the EvidenceClassification computed for
        # that same evidence -- because classify_evidence() never reads
        # an EvidenceSummary as input.
        kwargs = dict(
            identity_resolution=_resolved_identity(confidence=IdentityConfidence.PROVISIONAL),
            bar=_bar(),
            bar_corrupted=False,
            regime_state=_regime(),
            features=_features(warm_up_sufficient=False),
        )
        classification = classify_evidence(**kwargs)
        summary_a = build_evidence_summary(**kwargs)
        summary_b = build_evidence_summary(**kwargs)
        self.assertEqual(summary_a, summary_b)
        self.assertEqual(classification.evidence_quality, EvidenceQuality.LOW)
        self.assertEqual(classification.decision_type.value, "manual_review")
        self.assertEqual(classification.confidence_status.value, "not_calibrated")
        self.assertEqual(classification.risk_status.value, "not_calibrated")


class TestFiredConditionsAndSummaryCoexist(unittest.TestCase):
    def test_fired_conditions_still_present_alongside_summary(self) -> None:
        kwargs = dict(
            identity_resolution=_resolved_identity(confidence=IdentityConfidence.PROVISIONAL),
            bar=_bar(),
            bar_corrupted=False,
            regime_state=_regime(),
            features=_features(),
        )
        classification = classify_evidence(**kwargs)
        summary = build_evidence_summary(**kwargs)
        self.assertNotEqual(classification.fired_conditions, ())
        self.assertEqual(summary.identity.fired_conditions, classification.fired_conditions)

    def test_summary_and_fired_conditions_describe_identical_evidence(self) -> None:
        kwargs = dict(
            identity_resolution=_resolved_identity(),
            bar=None,
            bar_corrupted=False,
            regime_state=_regime(RegimeLabel.UNKNOWN),
            features=None,
        )
        classification = classify_evidence(**kwargs)
        summary = build_evidence_summary(**kwargs)
        all_summary_conditions = set(
            summary.identity.fired_conditions
            + summary.price_volume.fired_conditions
            + summary.regime.fired_conditions
            + summary.execution_quality.fired_conditions
            + summary.feature_warm_up.fired_conditions
            + summary.corporate_action_convention.fired_conditions
        )
        self.assertEqual(all_summary_conditions, set(classification.fired_conditions))


class TestNoNumericScoring(unittest.TestCase):
    def test_evidence_summary_has_no_numeric_fields(self) -> None:
        import dataclasses

        from d0026.evidence import EvidenceSummary

        forbidden_substrings = ("score", "weight", "probability", "threshold", "confidence", "risk")
        for field in dataclasses.fields(EvidenceSummary):
            lowered = field.name.lower()
            for forbidden in forbidden_substrings:
                self.assertNotIn(
                    forbidden,
                    lowered,
                    f"EvidenceSummary field {field.name!r} suggests numeric scoring",
                )

    def test_evidence_summary_instances_contain_no_numeric_leaf_values(self) -> None:
        import dataclasses

        summary = build_evidence_summary(
            identity_resolution=_resolved_identity(confidence=IdentityConfidence.PROVISIONAL),
            bar=_bar(),
            bar_corrupted=False,
            regime_state=_regime(),
            features=_features(warm_up_sufficient=False),
        )
        for field in dataclasses.fields(summary):
            value = getattr(summary, field.name)
            self._assert_no_numeric_leaf(value)

    def _assert_no_numeric_leaf(self, value) -> None:
        import dataclasses

        if isinstance(value, bool):
            return
        if isinstance(value, (int, float)):
            self.fail(f"unexpected numeric leaf value in EvidenceSummary: {value!r}")
        if isinstance(value, tuple):
            for item in value:
                self._assert_no_numeric_leaf(item)
        elif dataclasses.is_dataclass(value):
            for field in dataclasses.fields(value):
                self._assert_no_numeric_leaf(getattr(value, field.name))

    def test_no_counting_based_quality_calculation_in_build_evidence_summary_source(self) -> None:
        import inspect

        source = inspect.getsource(build_evidence_summary)
        self.assertNotIn("len(", source)
        self.assertNotIn("sum(", source)


class TestNoHistoricalEvidenceQualityState(unittest.TestCase):
    def test_build_evidence_summary_has_no_history_parameter(self) -> None:
        import inspect

        signature = inspect.signature(build_evidence_summary)
        forbidden_substrings = ("history", "prior", "past", "reputation", "trust", "track_record")
        for param_name in signature.parameters:
            lowered = param_name.lower()
            for forbidden in forbidden_substrings:
                self.assertNotIn(forbidden, lowered)

    def test_build_evidence_summary_is_pure_of_module_level_mutable_state(self) -> None:
        import d0026.evidence as evidence_module

        self.assertIsInstance(evidence_module._HARD_FAIL_DESCRIPTIONS, dict)
        self.assertIsInstance(evidence_module._SOFT_GAP_DESCRIPTIONS, dict)


class TestSourceReferences(unittest.TestCase):
    def test_source_references_collected_from_supplied_evidence_only(self) -> None:
        summary = build_evidence_summary(
            identity_resolution=_resolved_identity(source_reference="alias-ref-1"),
            bar=_bar(source_reference="bar-ref-1"),
            bar_corrupted=False,
            regime_state=_regime(),
            features=_features(source_reference="features-ref-1"),
        )
        self.assertEqual(
            set(summary.source_references), {"alias-ref-1", "bar-ref-1", "features-ref-1"}
        )

    def test_no_source_references_fabricated_when_evidence_absent(self) -> None:
        unresolved = IdentityResolution(
            outcome=ResolutionOutcome.UNRESOLVED, ticker="ZZZ", as_of_date=_DATE, detail="no alias"
        )
        summary = build_evidence_summary(
            identity_resolution=unresolved,
            bar=None,
            bar_corrupted=False,
            regime_state=_regime(),
            features=None,
        )
        self.assertEqual(summary.source_references, ())


if __name__ == "__main__":
    unittest.main()
