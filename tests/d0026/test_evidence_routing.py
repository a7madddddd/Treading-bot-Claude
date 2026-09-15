"""Routing tests: NOT_ELIGIBLE candidates must never become snapshot
entries; MANUAL_REVIEW and STANDARD must follow the exact routing
described in the frozen D-0026 Evidence/Confidence Layer design."""

import unittest
from datetime import date

from d0026.evidence import DecisionType, EvidenceQuality, classify_evidence
from d0026.identity import IdentityConfidence, IdentityResolution, ResolutionOutcome, SecurityIdentity, TickerAlias
from d0026.models import (
    DailySecurityFeatures,
    MarketDataBar,
    RawCandidateRef,
    RegimeLabel,
    RegimeState,
    SelectedCandidateEntry,
    UniverseCandidate,
    AdjustmentConvention,
)
from d0026.snapshot import SnapshotSymbolEntry, build_snapshot_symbol_entries

_DATE = date(2026, 1, 5)


def _regime() -> RegimeState:
    return RegimeState(
        as_of_date=_DATE,
        label=RegimeLabel.UNCLASSIFIED_PENDING_CALIBRATION,
        reference_series_values=(("dummy_ref", 1.0),),
        classification_method_version="none-pending-calibration",
    )


def _selected_candidate(
    ticker: str,
    security_id: str,
    *,
    confidence: IdentityConfidence = IdentityConfidence.RESOLVED_CIK,
    resolved: bool = True,
    rank: int = 1,
    bar: bool = True,
    features: bool = True,
) -> SelectedCandidateEntry:
    if resolved:
        identity = SecurityIdentity(
            security_id=security_id,
            display_name=f"{ticker} Corp",
            cik="0000000001" if confidence is IdentityConfidence.RESOLVED_CIK else None,
            confidence=confidence,
        )
        alias = TickerAlias(security_id=security_id, ticker=ticker, effective_start=None, effective_end=None)
        resolution = IdentityResolution(
            outcome=ResolutionOutcome.RESOLVED, ticker=ticker, as_of_date=_DATE, identity=identity, alias=alias
        )
    else:
        resolution = IdentityResolution(
            outcome=ResolutionOutcome.UNRESOLVED, ticker=ticker, as_of_date=_DATE, detail="no alias"
        )

    candidate = UniverseCandidate(
        raw=RawCandidateRef(ticker=ticker, as_of_date=_DATE),
        identity_resolution=resolution,
        bar=(
            MarketDataBar(
                security_id=security_id,
                ticker_as_of_date=ticker,
                bar_date=_DATE,
                open=10.0,
                high=11.0,
                low=9.0,
                close=10.5,
                volume=1000.0,
                adjustment=AdjustmentConvention.SPLIT_ADJUSTED,
                source_reference="synthetic-test-fixture",
            )
            if bar
            else None
        ),
        features=(
            DailySecurityFeatures(
                security_id=security_id,
                feature_date=_DATE,
                liquidity_measure=1.0,
                atr_measure=1.0,
                momentum_measure=1.0,
                execution_quality_proxy=1.0,
                execution_quality_proxy_is_true_quote=True,
                warm_up_sufficient=True,
                source_reference="synthetic-test-fixture",
            )
            if features
            else None
        ),
    )
    return SelectedCandidateEntry(candidate=candidate, rank=rank, score_summary=(("placeholder", 0.0),))


class TestRoutingExcludesNotEligible(unittest.TestCase):
    def test_not_eligible_candidate_never_becomes_a_snapshot_entry(self) -> None:
        selected = _selected_candidate("ZZZ", "sec-zzz", resolved=False)  # -> HF1 -> NOT_ELIGIBLE
        classification = classify_evidence(
            identity_resolution=selected.candidate.identity_resolution,
            bar=selected.candidate.bar,
            bar_corrupted=False,
            regime_state=_regime(),
            features=selected.candidate.features,
        )
        self.assertEqual(classification.decision_type, DecisionType.NOT_ELIGIBLE)
        entries = build_snapshot_symbol_entries(((selected, classification),), as_of_date=_DATE)
        self.assertEqual(entries, ())

    def test_snapshot_symbol_entry_construction_refuses_not_eligible_directly(self) -> None:
        with self.assertRaises(ValueError):
            SnapshotSymbolEntry(
                security_id="sec-zzz",
                ticker_as_of_date="ZZZ",
                rank=1,
                score_summary=(),
                first_seen_by_universe_at=_DATE,
                sector=None,
                identity_confidence="provisional",
                decision_type=DecisionType.NOT_ELIGIBLE,
                evidence_quality=EvidenceQuality.INSUFFICIENT,
                evidence_fired_conditions=(),
                evidence_policy_version="D0026-EV-001",
                feature_definition_version="D0026-FEATDEF-001",
            )


class TestRoutingIncludesManualReviewAndStandard(unittest.TestCase):
    def test_manual_review_candidate_becomes_a_snapshot_entry(self) -> None:
        selected = _selected_candidate("AAA", "sec-aaa", confidence=IdentityConfidence.PROVISIONAL)
        classification = classify_evidence(
            identity_resolution=selected.candidate.identity_resolution,
            bar=selected.candidate.bar,
            bar_corrupted=False,
            regime_state=_regime(),
            features=selected.candidate.features,
        )
        self.assertEqual(classification.decision_type, DecisionType.MANUAL_REVIEW)
        entries = build_snapshot_symbol_entries(((selected, classification),), as_of_date=_DATE)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].decision_type, DecisionType.MANUAL_REVIEW)
        self.assertEqual(entries[0].security_id, "sec-aaa")

    def test_standard_candidate_becomes_a_snapshot_entry(self) -> None:
        selected = _selected_candidate("BBB", "sec-bbb")
        classification = classify_evidence(
            identity_resolution=selected.candidate.identity_resolution,
            bar=selected.candidate.bar,
            bar_corrupted=False,
            regime_state=_regime(),
            features=selected.candidate.features,
        )
        self.assertEqual(classification.decision_type, DecisionType.STANDARD)
        entries = build_snapshot_symbol_entries(((selected, classification),), as_of_date=_DATE)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].decision_type, DecisionType.STANDARD)

    def test_mixed_batch_only_non_not_eligible_entries_survive(self) -> None:
        eligible = _selected_candidate("BBB", "sec-bbb")
        review = _selected_candidate("CCC", "sec-ccc", confidence=IdentityConfidence.PROVISIONAL)
        excluded = _selected_candidate("DDD", "sec-ddd", resolved=False)

        pairs = []
        for selected in (eligible, review, excluded):
            classification = classify_evidence(
                identity_resolution=selected.candidate.identity_resolution,
                bar=selected.candidate.bar,
                bar_corrupted=False,
                regime_state=_regime(),
                features=selected.candidate.features,
            )
            pairs.append((selected, classification))

        entries = build_snapshot_symbol_entries(tuple(pairs), as_of_date=_DATE)
        security_ids = {entry.security_id for entry in entries}
        self.assertEqual(security_ids, {"sec-bbb", "sec-ccc"})
        self.assertNotIn("sec-ddd", security_ids)
        self.assertEqual(len(entries), 2)

    def test_rank_and_score_summary_pass_through_unmodified(self) -> None:
        # Confirms the assembler never computes or influences rank —
        # it only relays what Stage F (not yet implemented) supplied.
        selected = _selected_candidate("BBB", "sec-bbb", rank=7)
        classification = classify_evidence(
            identity_resolution=selected.candidate.identity_resolution,
            bar=selected.candidate.bar,
            bar_corrupted=False,
            regime_state=_regime(),
            features=selected.candidate.features,
        )
        entries = build_snapshot_symbol_entries(((selected, classification),), as_of_date=_DATE)
        self.assertEqual(entries[0].rank, 7)
        self.assertEqual(entries[0].score_summary, (("placeholder", 0.0),))


if __name__ == "__main__":
    unittest.main()
