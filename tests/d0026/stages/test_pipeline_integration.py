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


if __name__ == "__main__":
    unittest.main()
