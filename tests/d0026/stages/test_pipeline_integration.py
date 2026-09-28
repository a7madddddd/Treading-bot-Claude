"""Full-pipeline integration test: run the D-0048 percentage-only
stages inside UniversePipeline against a synthetic set of raw
candidates. Verifies that the calibration gate is now UNBLOCKED under
the percentage-only design and the pipeline produces a real snapshot."""

import unittest
from datetime import date

from d0026.config import UniverseSelectionConfig
from d0026.failure import SnapshotOutcome
from d0026.identity import (
    IdentityConfidence, IdentityResolution, IdentityResolver,
    ResolutionOutcome, SecurityIdentity, TickerAlias,
)
from d0026.models import RawCandidateRef, RegimeLabel, RegimeState
from d0026.observability import AuditSink
from d0026.provider import UniverseSourceProvider
from d0026.repository import InMemorySnapshotRepository, SnapshotRepository
from d0026.pipeline import UniversePipeline
from d0026.snapshot import ApprovedUniverseSnapshot
from d0026.stages import default_percentage_evaluators

from ._fixtures import DATE


class _StubProvider(UniverseSourceProvider):
    def __init__(self, tickers):
        self._tickers = tickers

    def get_raw_candidates(self, as_of_date):
        return tuple(RawCandidateRef(ticker=t, as_of_date=as_of_date)
                     for t in self._tickers)


class _StubResolver(IdentityResolver):
    def resolve(self, ticker, as_of_date) -> IdentityResolution:
        identity = SecurityIdentity(
            security_id=f"sec-{ticker}", display_name=f"{ticker} Corp",
            cik="0000000001", confidence=IdentityConfidence.RESOLVED_CIK,
        )
        alias = TickerAlias(
            security_id=identity.security_id, ticker=ticker,
            effective_start=None, effective_end=None,
        )
        return IdentityResolution(
            outcome=ResolutionOutcome.RESOLVED, ticker=ticker,
            as_of_date=as_of_date, identity=identity, alias=alias,
        )


class _MemSink(AuditSink):
    def __init__(self):
        self.events = []
    def record(self, event) -> None:
        self.events.append(event)


class TestPipelineIntegration(unittest.TestCase):
    def _regime(self):
        return RegimeState(
            as_of_date=DATE,
            label=RegimeLabel.UNCLASSIFIED_PENDING_CALIBRATION,
            reference_series_values=(("vix_percentile", 0.5),),
            classification_method_version="test",
        )

    def test_pipeline_produces_snapshot_with_percentage_stages(self):
        # Note: identity resolution happens BEFORE stages; stages then
        # operate on identity-resolved candidates whose features/bars
        # are all None (no feature enrichment step in this test). The
        # pipeline should therefore produce an EMPTY snapshot rather
        # than a CRASH -- proving the calibration gate is no longer a
        # blocker under D-0048.
        provider = _StubProvider(("AAPL", "TSLA", "SPY"))
        pipeline = UniversePipeline(
            provider=provider,
            identity_resolver=_StubResolver(),
            stage_evaluators=default_percentage_evaluators(
                UniverseSelectionConfig()
            ),
            snapshot_repository=InMemorySnapshotRepository(),
            audit_sink=_MemSink(),
            selection_version="test-v1",
            universe_source_version="stub",
            identity_mapping_version="stub",
        )
        outcome = pipeline.run(DATE, self._regime())
        self.assertIsInstance(outcome, SnapshotOutcome)
        # Without a feature enricher, everything is filtered by stage A
        # (missing features -> MISSING_MARKET_DATA). Snapshot is
        # EMPTY, but critically: no CalibrationRequiredError crash.
        self.assertTrue(outcome.snapshot.is_empty)
        self.assertEqual(len(outcome.snapshot.symbols), 0)

    def test_pipeline_with_enricher_produces_non_empty_snapshot(self):
        """B23: with a feature enricher wired, fully-featured
        candidates flow through all stages and land in the snapshot as
        real SnapshotSymbolEntry rows."""
        from d0026.models import (
            AdjustmentConvention, DailySecurityFeatures, MarketDataBar,
            UniverseCandidate,
        )

        def enricher(candidate: UniverseCandidate, as_of_date, regime_state):
            ticker = candidate.raw.ticker
            security_id = f"sec-{ticker}"
            # Different momentum per symbol to produce a stable ranking.
            mom_by_ticker = {"AAPL": 0.10, "TSLA": 0.07, "SPY": 0.03}
            # Each candidate in its own sector so the concentration
            # stage doesn't drop 2 of 3 for over-representation.
            sector_by_ticker = {"AAPL": "tech", "TSLA": "auto",
                                "SPY": "index"}
            source_ref = f"sector={sector_by_ticker[ticker]}"
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
                momentum_measure=mom_by_ticker[ticker],
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

        provider = _StubProvider(("AAPL", "TSLA", "SPY"))
        # Permissive config to keep all three candidates through the
        # percentage-based filters; the point of this test is to
        # verify the SYMBOLS-POPULATION wiring, not to re-test the
        # per-stage filter logic (which lives in test_stages.py).
        permissive = UniverseSelectionConfig(
            min_volume_percentile=1.0,
            drop_bottom_price_percentile=0.0,
            min_market_cap_percentile=1.0,
            min_completeness_fraction=0.5,
            min_spread_tightness_percentile=1.0,
            min_trend_percentile=1.0,
            top_n=10,
        )
        pipeline = UniversePipeline(
            provider=provider,
            identity_resolver=_StubResolver(),
            stage_evaluators=default_percentage_evaluators(permissive),
            snapshot_repository=InMemorySnapshotRepository(),
            audit_sink=_MemSink(),
            selection_version="test-v1",
            universe_source_version="stub",
            identity_mapping_version="stub",
            feature_enricher=enricher,
        )
        outcome = pipeline.run(DATE, self._regime())
        self.assertIsInstance(outcome, SnapshotOutcome)
        self.assertFalse(outcome.snapshot.is_empty,
                         msg=f"expected non-empty; got {outcome.snapshot}")
        # All three make Top-N. Ranking orders by momentum (AAPL top).
        tickers = tuple(s.ticker_as_of_date for s in outcome.snapshot.symbols)
        self.assertEqual(len(outcome.snapshot.symbols), 3)
        self.assertEqual(tickers[0], "AAPL")
        self.assertEqual(outcome.snapshot.symbols[0].rank, 1)


if __name__ == "__main__":
    unittest.main()
