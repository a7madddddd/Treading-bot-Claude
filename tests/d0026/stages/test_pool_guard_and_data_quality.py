"""P-036 and P-038 (2026-10-05).

P-036: a run whose raw-candidate pool is far smaller than a real
whole-market fetch must refuse to publish anything, because its
percentage stages would rank a population that is not the market. On
2026-10-05 a 40-candidate test run published the day's snapshot with
one symbol and the engine traded that universe for a whole session.

P-038: the snapshot's data_quality_summary was hardcoded to () since
the pipeline was written, so the one field designed to reveal a
degraded run reported nothing on the worst run to date.
"""

import unittest
from datetime import date

from d0026.config import UniverseSelectionConfig
from d0026.failure import CrashCategory, CrashOutcome, SnapshotOutcome
from d0026.models import (
    AdjustmentConvention, DailySecurityFeatures, MarketDataBar,
    RegimeLabel, RegimeState, UniverseCandidate,
)
from d0026.pipeline import InsufficientCandidatePoolError, UniversePipeline
from d0026.repository import InMemorySnapshotRepository
from d0026.stages import default_percentage_evaluators

from ._fixtures import DATE
from .test_pipeline_integration import _MemSink, _StubProvider, _StubResolver


_PERMISSIVE = UniverseSelectionConfig(
    min_volume_percentile=1.0,
    drop_bottom_price_percentile=0.0,
    min_market_cap_percentile=1.0,
    min_completeness_fraction=0.5,
    min_spread_tightness_percentile=1.0,
    min_trend_percentile=1.0,
    top_n=10,
)


def _enricher(candidate: UniverseCandidate, as_of_date, regime_state):
    """Gives every candidate complete, qualifying features so the
    stages are not what the test is measuring."""
    ticker = candidate.raw.ticker
    security_id = f"sec-{ticker}"
    source_ref = f"sector=sector-{ticker}"
    bar = MarketDataBar(
        security_id=security_id, ticker_as_of_date=ticker,
        bar_date=as_of_date, open=100.0, high=102.0, low=98.0,
        close=100.0, volume=5_000_000.0,
        adjustment=AdjustmentConvention.SPLIT_ADJUSTED,
        source_reference=source_ref,
    )
    features = DailySecurityFeatures(
        security_id=security_id, feature_date=as_of_date,
        liquidity_measure=10_000_000.0, atr_measure=2.0,
        momentum_measure=0.05 + len(ticker) / 1000.0,
        execution_quality_proxy=0.001,
        execution_quality_proxy_is_true_quote=True,
        warm_up_sufficient=True,
        source_reference=source_ref,
    )
    return UniverseCandidate(
        raw=candidate.raw,
        identity_resolution=candidate.identity_resolution,
        bar=bar, features=features,
    )


def _regime():
    return RegimeState(
        as_of_date=DATE,
        label=RegimeLabel.UNCLASSIFIED_PENDING_CALIBRATION,
        reference_series_values=(("vix_percentile", 0.5),),
        classification_method_version="test",
    )


def _build(tickers, *, repo, min_raw_candidates=0, enrich=True):
    return UniversePipeline(
        provider=_StubProvider(tuple(tickers)),
        identity_resolver=_StubResolver(),
        stage_evaluators=default_percentage_evaluators(_PERMISSIVE),
        snapshot_repository=repo,
        audit_sink=_MemSink(),
        selection_version="test-v1",
        universe_source_version="stub",
        identity_mapping_version="stub",
        feature_enricher=_enricher if enrich else None,
        min_raw_candidates=min_raw_candidates,
    )


_TICKERS = tuple(f"SYM{i:03d}" for i in range(40))


class TestP036PoolGuard(unittest.TestCase):
    def test_pool_below_minimum_is_a_crash_not_an_empty_snapshot(self):
        # The distinction matters: EMPTY means "the market was examined
        # and nothing qualified", which is a tradable-day fact. CRASH
        # means "this run is not evidence about the market at all".
        repo = InMemorySnapshotRepository()
        outcome = _build(_TICKERS, repo=repo,
                         min_raw_candidates=500).run(DATE, _regime())
        self.assertIsInstance(outcome, CrashOutcome)
        self.assertIs(outcome.category,
                      CrashCategory.INSUFFICIENT_CANDIDATE_POOL)

    def test_refused_run_writes_no_snapshot_at_all(self):
        # This is the property an empty snapshot would NOT have: a good
        # snapshot already published for the same date must survive.
        repo = InMemorySnapshotRepository()
        _build(_TICKERS, repo=repo,
               min_raw_candidates=500).run(DATE, _regime())
        self.assertIsNone(repo.get_latest_for_date(DATE))

    def test_a_good_snapshot_for_the_same_date_survives_a_refusal(self):
        repo = InMemorySnapshotRepository()
        good = _build(_TICKERS, repo=repo).run(DATE, _regime())
        self.assertIsInstance(good, SnapshotOutcome)
        before = repo.get_latest_for_date(DATE)
        self.assertIsNotNone(before)

        _build(("ONLY",), repo=repo,
               min_raw_candidates=500).run(DATE, _regime())
        self.assertEqual(repo.get_latest_for_date(DATE).snapshot_id,
                         before.snapshot_id)

    def test_detail_names_both_numbers(self):
        outcome = _build(_TICKERS, repo=InMemorySnapshotRepository(),
                         min_raw_candidates=500).run(DATE, _regime())
        self.assertIn("40", outcome.detail)
        self.assertIn("500", outcome.detail)

    def test_pool_at_exactly_the_minimum_is_accepted(self):
        # Boundary: the guard is "below the minimum", not "at or below".
        outcome = _build(_TICKERS, repo=InMemorySnapshotRepository(),
                         min_raw_candidates=40).run(DATE, _regime())
        self.assertIsInstance(outcome, SnapshotOutcome)

    def test_zero_disables_the_guard_preserving_historical_behavior(self):
        outcome = _build(("A", "B"), repo=InMemorySnapshotRepository(),
                         min_raw_candidates=0).run(DATE, _regime())
        self.assertIsInstance(outcome, SnapshotOutcome)

    def test_default_is_disabled_so_existing_callers_are_unchanged(self):
        outcome = _build(("A", "B"),
                         repo=InMemorySnapshotRepository()).run(
                             DATE, _regime())
        self.assertIsInstance(outcome, SnapshotOutcome)

    def test_negative_minimum_is_rejected_at_construction(self):
        with self.assertRaises(ValueError):
            _build(("A",), repo=InMemorySnapshotRepository(),
                   min_raw_candidates=-1)

    def test_guard_runs_before_enrichment_is_spent(self):
        # The guard must cost nothing: a refused run must not call the
        # enricher even once, since enrichment is one API call per
        # symbol and is what exhausted the rate limit on 2026-10-05.
        calls = []

        def counting_enricher(candidate, as_of_date, regime_state):
            calls.append(candidate.raw.ticker)
            return _enricher(candidate, as_of_date, regime_state)

        pipeline = UniversePipeline(
            provider=_StubProvider(_TICKERS),
            identity_resolver=_StubResolver(),
            stage_evaluators=default_percentage_evaluators(_PERMISSIVE),
            snapshot_repository=InMemorySnapshotRepository(),
            audit_sink=_MemSink(),
            selection_version="test-v1",
            universe_source_version="stub",
            identity_mapping_version="stub",
            feature_enricher=counting_enricher,
            min_raw_candidates=500,
        )
        pipeline.run(DATE, _regime())
        self.assertEqual(calls, [])

    def test_the_error_type_is_public_and_specific(self):
        self.assertTrue(issubclass(InsufficientCandidatePoolError,
                                   RuntimeError))


class TestP038DataQuality(unittest.TestCase):
    def _summary(self, tickers=_TICKERS, enrich=True):
        outcome = _build(tickers, repo=InMemorySnapshotRepository(),
                         enrich=enrich).run(DATE, _regime())
        self.assertIsInstance(outcome, SnapshotOutcome)
        return dict(outcome.snapshot.data_quality_summary)

    def test_summary_is_no_longer_empty(self):
        self.assertTrue(self._summary())

    def test_reports_how_many_raw_candidates_were_fetched(self):
        self.assertEqual(self._summary()["raw_candidates_fetched"], 40)

    def test_a_truncated_run_is_visibly_different_from_a_full_one(self):
        # The whole point: 40 and 11,683 must not print the same shape.
        small = self._summary(_TICKERS)
        big = self._summary(tuple(f"S{i:04d}" for i in range(600)))
        self.assertNotEqual(small["raw_candidates_fetched"],
                            big["raw_candidates_fetched"])

    def test_identity_counts_add_up_to_the_fetched_total(self):
        s = self._summary()
        self.assertEqual(s["identity_resolved"] + s["identity_rejected"],
                         s["raw_candidates_fetched"])

    def test_enrichment_counts_add_up_to_the_resolved_total(self):
        s = self._summary()
        self.assertEqual(
            s["enriched_with_features"] + s["missing_features"],
            s["identity_resolved"],
        )

    def test_missing_features_is_visible_when_no_enricher_is_wired(self):
        s = self._summary(enrich=False)
        self.assertEqual(s["enriched_with_features"], 0)
        self.assertEqual(s["missing_features"], 40)

    def test_survivor_count_matches_the_published_symbols(self):
        outcome = _build(_TICKERS,
                         repo=InMemorySnapshotRepository()).run(
                             DATE, _regime())
        s = dict(outcome.snapshot.data_quality_summary)
        self.assertEqual(s["survivors_to_snapshot"],
                         len(outcome.snapshot.symbols))

    def test_the_configured_guard_value_is_recorded(self):
        outcome = _build(_TICKERS, repo=InMemorySnapshotRepository(),
                         min_raw_candidates=10).run(DATE, _regime())
        s = dict(outcome.snapshot.data_quality_summary)
        self.assertEqual(s["min_raw_candidates_configured"], 10)

    def test_every_value_is_an_int_so_the_json_column_round_trips(self):
        for key, value in self._summary().items():
            self.assertIsInstance(key, str)
            self.assertIsInstance(value, int)


if __name__ == "__main__":
    unittest.main()
