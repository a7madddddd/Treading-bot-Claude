"""End-to-end rehearsal of the seam that failed on 2026-10-05: the
universe pipeline WRITES a snapshot to real SQLite, and the engine's
SnapshotUniverseSource READS it back.

Every other test in this area exercises one side or the other. This one
runs both against the same file, because the incident was not a defect
in either half -- each worked -- but in what the pair did together when
the producing run was not a real run.

No broker, no network. The provider and enricher are stubs; the
repository and the database are real.
"""

import os
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timezone

from d0026.config import UniverseSelectionConfig
from d0026.failure import CrashOutcome, SnapshotOutcome
from d0026.models import (
    AdjustmentConvention, DailySecurityFeatures, MarketDataBar,
    RegimeLabel, RegimeState, UniverseCandidate,
)
from d0026.pipeline import UniversePipeline
from d0026.sqlite_repository import SqliteSnapshotRepository
from d0026.stages import default_percentage_evaluators
from engine.snapshot_watchlist import (
    SnapshotUniverseSource, _current_effective_date_et,
)
from persistence.db import connect, bootstrap_schema

from .stages.test_pipeline_integration import (
    _MemSink, _StubProvider, _StubResolver,
)


# 10:00 UTC on a Tuesday = 06:00 US Eastern during EDT -- the exact
# moment universe-refresh.timer fires in production.
_RUN_AT = datetime(2026, 10, 6, 10, 0, 0, tzinfo=timezone.utc)
# 14:00 UTC the same day = 10:00 ET, half an hour after the open, when
# the engine is looking for a universe to trade.
_READ_AT = datetime(2026, 10, 6, 14, 0, 0, tzinfo=timezone.utc)

_PERMISSIVE = UniverseSelectionConfig(
    min_volume_percentile=1.0,
    drop_bottom_price_percentile=0.0,
    min_market_cap_percentile=1.0,
    min_completeness_fraction=0.5,
    min_spread_tightness_percentile=1.0,
    min_trend_percentile=1.0,
    top_n=10,
)


def _enricher(candidate, as_of_date, regime_state):
    ticker = candidate.raw.ticker
    security_id = f"sec-{ticker}"
    source_ref = f"sector=sector-{ticker[-1]}"
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
        # Strictly increasing with the numeric suffix, so the ranking
        # is deterministic and the Top-10 is predictable.
        momentum_measure=int(ticker[3:]) / 10_000.0,
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


def _regime(as_of):
    return RegimeState(
        as_of_date=as_of,
        label=RegimeLabel.UNCLASSIFIED_PENDING_CALIBRATION,
        reference_series_values=(("vix_percentile", 0.5),),
        classification_method_version="test",
    )


class TestUniverseToEngineHandoff(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        conn = connect(self.db_path)
        bootstrap_schema(conn)
        conn.close()
        # The date both halves must independently agree on.
        self.effective = _current_effective_date_et(_RUN_AT)

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def _run_pipeline(self, tickers, *, min_raw_candidates=500):
        conn = connect(self.db_path)
        pipeline = UniversePipeline(
            provider=_StubProvider(tuple(tickers)),
            identity_resolver=_StubResolver(),
            stage_evaluators=default_percentage_evaluators(_PERMISSIVE),
            snapshot_repository=SqliteSnapshotRepository(conn),
            audit_sink=_MemSink(),
            selection_version="D-0048-v1",
            universe_source_version="stub",
            identity_mapping_version="stub",
            feature_enricher=_enricher,
            min_raw_candidates=min_raw_candidates,
        )
        outcome = pipeline.run(self.effective, _regime(self.effective))
        conn.close()
        return outcome

    def _read_as_engine(self, now=_READ_AT):
        conn = connect(self.db_path)
        source = SnapshotUniverseSource(
            SqliteSnapshotRepository(conn),
            fallback_watchlist=None,
            now_fn=lambda: now,
        )
        symbols = source.get_active_symbols()
        conn.close()
        return symbols

    # ---- the date contract ------------------------------------------

    def test_producer_and_consumer_agree_on_the_trading_date(self):
        # The run happens at 06:00 ET and the read at 10:00 ET. If these
        # resolved to different dates the engine would find nothing,
        # every day, and the only symptom would be silence.
        self.assertEqual(_current_effective_date_et(_RUN_AT),
                         _current_effective_date_et(_READ_AT))

    def test_the_agreed_date_is_the_calendar_trading_day(self):
        self.assertEqual(_current_effective_date_et(_RUN_AT),
                         date(2026, 10, 6))

    # ---- the happy path ---------------------------------------------

    def test_a_full_run_is_published_and_the_engine_sees_it(self):
        outcome = self._run_pipeline(
            tuple(f"SYM{i:04d}" for i in range(600)))
        self.assertIsInstance(outcome, SnapshotOutcome)
        symbols = self._read_as_engine()
        self.assertEqual(len(symbols), 10)
        self.assertEqual(set(symbols),
                         {e.ticker_as_of_date.upper()
                          for e in outcome.snapshot.symbols})

    def test_the_engine_reads_through_a_separate_connection(self):
        # The universe job is a different PROCESS from the engine. This
        # asserts the write is committed and visible, not merely held in
        # the writer's transaction.
        self._run_pipeline(tuple(f"SYM{i:04d}" for i in range(600)))
        raw = sqlite3.connect(self.db_path)
        count = raw.execute(
            "SELECT COUNT(*) FROM universe_snapshots "
            "WHERE effective_trading_date = ?",
            (self.effective.isoformat(),)).fetchone()[0]
        raw.close()
        self.assertEqual(count, 1)

    def test_published_snapshot_carries_the_data_quality_block(self):
        outcome = self._run_pipeline(
            tuple(f"SYM{i:04d}" for i in range(600)))
        summary = dict(outcome.snapshot.data_quality_summary)
        self.assertEqual(summary["raw_candidates_fetched"], 600)
        self.assertEqual(summary["survivors_to_snapshot"], 10)

    def test_data_quality_survives_the_round_trip_through_sqlite(self):
        # It is stored as JSON; a shape the decoder cannot rebuild would
        # only show up here, not in an in-memory test.
        self._run_pipeline(tuple(f"SYM{i:04d}" for i in range(600)))
        conn = connect(self.db_path)
        snap = SqliteSnapshotRepository(conn).get_latest_for_date(
            self.effective)
        conn.close()
        summary = dict(snap.data_quality_summary)
        self.assertEqual(summary["raw_candidates_fetched"], 600)
        self.assertEqual(summary["min_raw_candidates_configured"], 500)

    # ---- the 2026-10-05 incident, replayed ---------------------------

    def test_a_40_candidate_run_cannot_become_the_engines_universe(self):
        good = self._run_pipeline(
            tuple(f"SYM{i:04d}" for i in range(600)))
        self.assertIsInstance(good, SnapshotOutcome)
        before = self._read_as_engine()
        self.assertEqual(len(before), 10)

        # Now the incident: a tiny run against the same date.
        bad = self._run_pipeline(tuple(f"SYM{i:04d}" for i in range(40)))
        self.assertIsInstance(bad, CrashOutcome)

        after = self._read_as_engine()
        self.assertEqual(after, before,
                         "a refused run changed what the engine trades")

    def test_without_the_guard_the_incident_still_reproduces(self):
        # Proves the test is actually testing the guard, not something
        # incidental: with min_raw_candidates=0 the old behavior returns.
        self._run_pipeline(tuple(f"SYM{i:04d}" for i in range(600)))
        before = self._read_as_engine()
        self._run_pipeline(tuple(f"SYM{i:04d}" for i in range(40)),
                           min_raw_candidates=0)
        after = self._read_as_engine()
        self.assertNotEqual(after, before)

    def test_no_snapshot_for_the_date_means_no_symbols_not_a_crash(self):
        self.assertEqual(self._read_as_engine(), ())


if __name__ == "__main__":
    unittest.main()
