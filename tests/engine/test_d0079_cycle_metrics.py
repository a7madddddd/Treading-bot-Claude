"""D-0079 item 6: one recorded row per evaluation cycle."""

import sqlite3
import unittest
from datetime import date, datetime, timezone

from engine.cycle_metrics import (
    CycleMetrics, make_recorder, record_cycle_metrics,
)
from persistence.db import APPROVED_SCHEMA_VERSION, bootstrap_schema


def _m(**kw):
    base = dict(
        cycle_at=datetime(2026, 10, 6, 13, 30, tzinfo=timezone.utc),
        effective_date=date(2026, 10, 6),
        candidates_evaluated=10,
        rejected_hard_filter=4,
        above_min_score=2,
        min_score_required=60.0,
        best_score=71.5,
        proposals_created=2,
        scored=True,
    )
    base.update(kw)
    return CycleMetrics(**base)


class TestTheSchema(unittest.TestCase):
    def test_approved_version_is_eight(self):
        self.assertEqual(APPROVED_SCHEMA_VERSION, 8)

    def test_bootstrap_creates_the_table(self):
        conn = sqlite3.connect(":memory:")
        bootstrap_schema(conn)
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(cycle_metrics)")}
        self.assertIn("above_min_score", cols)
        self.assertEqual(
            conn.execute("PRAGMA user_version").fetchone()[0], 8)

    def test_migrating_a_version_7_database_adds_only_this_table(self):
        """The upgrade path a real file takes, not a fresh create."""
        conn = sqlite3.connect(":memory:")
        bootstrap_schema(conn)
        before = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("cycle_metrics", before)
        # Rows in every pre-existing table are untouched by 0008 --
        # it is a CREATE TABLE and nothing else.
        sql = open("src/persistence/migrations/0008_cycle_metrics.sql",
                   encoding="utf-8").read().upper()
        for forbidden in ("DROP ", "ALTER ", "DELETE ", "UPDATE "):
            self.assertNotIn(forbidden, sql,
                             f"0008 must be additive only; found {forbidden}")


class TestRecording(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        bootstrap_schema(self.conn)

    def _rows(self):
        return self.conn.execute(
            "SELECT cycle_at, effective_date, candidates_evaluated, "
            "rejected_hard_filter, above_min_score, min_score_required, "
            "best_score, proposals_created FROM cycle_metrics "
            "ORDER BY cycle_at").fetchall()

    def test_one_cycle_writes_one_row(self):
        record_cycle_metrics(self.conn, _m())
        self.assertEqual(len(self._rows()), 1)

    def test_every_value_round_trips(self):
        record_cycle_metrics(self.conn, _m())
        r = self._rows()[0]
        self.assertEqual(r[1], "2026-10-06")
        self.assertEqual(r[2], 10)
        self.assertEqual(r[3], 4)
        self.assertEqual(r[4], 2)
        self.assertAlmostEqual(r[5], 60.0)
        self.assertAlmostEqual(r[6], 71.5)
        self.assertEqual(r[7], 2)

    def test_a_zero_proposal_cycle_is_recorded_not_skipped(self):
        """The data point the Controller is actually collecting."""
        record_cycle_metrics(self.conn, _m(above_min_score=0,
                                           proposals_created=0,
                                           best_score=41.0))
        r = self._rows()[0]
        self.assertEqual(r[4], 0)
        self.assertEqual(r[7], 0)

    def test_best_score_may_be_null_when_nothing_passed_the_hard_filter(self):
        record_cycle_metrics(self.conn, _m(best_score=None))
        self.assertIsNone(self._rows()[0][6])

    def test_seven_cycles_in_a_day_write_seven_rows(self):
        for h in (13, 14, 15, 16, 17, 18, 19):
            record_cycle_metrics(self.conn, _m(
                cycle_at=datetime(2026, 10, 6, h, 30, tzinfo=timezone.utc)))
        self.assertEqual(len(self._rows()), 7)

    def test_the_same_cycle_twice_is_one_row_not_two(self):
        record_cycle_metrics(self.conn, _m(above_min_score=2))
        record_cycle_metrics(self.conn, _m(above_min_score=5))
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][4], 5)  # the retry wins


class TestTheRecorderNeverBreaksACycle(unittest.TestCase):
    """A metrics table must never be able to stop the engine."""

    def test_a_missing_table_is_swallowed(self):
        conn = sqlite3.connect(":memory:")   # no bootstrap at all
        make_recorder(conn)(_m())           # must not raise

    def test_a_closed_connection_is_swallowed(self):
        conn = sqlite3.connect(":memory:")
        bootstrap_schema(conn)
        conn.close()
        make_recorder(conn)(_m())           # must not raise

    def test_the_raw_writer_DOES_raise_so_the_swallow_is_deliberate(self):
        conn = sqlite3.connect(":memory:")
        with self.assertRaises(sqlite3.Error):
            record_cycle_metrics(conn, _m())


# ---------------------------------------------------------------------
# End-to-end: the ENGINE recording through a real evaluation cycle.
# This is the test that catches a wiring mistake; everything above
# only proves the writer works in isolation.
# ---------------------------------------------------------------------

from tests.engine.test_engine import (  # noqa: E402
    StaticWatchlistSource, _make_engine, _now, _repos,
)
from engine.engine import Engine  # noqa: E402


class _Res:
    def __init__(self, symbol, score, passes=True):
        self.symbol = symbol
        self.soft_score = score
        self.passes_hard_filter = passes
        self.hard_filter_reasons = [] if passes else ["rejected"]
        self.score_breakdown = {}
        self.research = None


class _Eval:
    """scores: {symbol: score}; a NEGATIVE score means hard-filtered."""

    def __init__(self, scores):
        self._scores = scores

    def rank(self, candidates):
        out = []
        for s in candidates:
            v = self._scores.get(s, 0.0)
            out.append(_Res(s, abs(v), passes=v >= 0))
        return sorted(out, key=lambda r: -r.soft_score)


class TestTheEngineRecordsEveryEvaluatedCycle(unittest.TestCase):

    def _run(self, scores, symbols):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        rows = []
        engine, broker, market_data, *_ = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn,
            watchlist=StaticWatchlistSource(tuple(symbols)),
            trade_evaluator=_Eval(scores),
        )
        engine._cycle_metrics_recorder = rows.append
        for s in symbols:
            market_data.set_price(s, 100.0)
        engine._lock.acquire(now=_now())
        engine.run_trigger_check(now=_now())
        return rows

    def test_a_cycle_with_three_proposals_records_the_counts(self):
        rows = self._run({"A": 61.0, "B": 70.0, "C": 90.0},
                         ["A", "B", "C"])
        self.assertEqual(len(rows), 1)
        m = rows[0]
        self.assertEqual(m.candidates_evaluated, 3)
        self.assertEqual(m.above_min_score, 3)
        self.assertEqual(m.rejected_hard_filter, 0)
        self.assertEqual(m.proposals_created, 3)
        self.assertAlmostEqual(m.best_score, 90.0)
        self.assertAlmostEqual(m.min_score_required, 60.0)

    def test_above_min_score_counts_ALL_of_them_not_just_the_proposed(self):
        """The whole point of the column: 5 cleared the bar, 3 were
        proposed. If this recorded 3 the data could never answer the
        Controller's question."""
        rows = self._run({"A": 61.0, "B": 70.0, "C": 90.0,
                          "D": 65.0, "E": 80.0},
                         ["A", "B", "C", "D", "E"])
        m = rows[0]
        self.assertEqual(m.above_min_score, 5)
        self.assertEqual(m.proposals_created, 3)

    def test_a_cycle_where_nothing_cleared_the_bar_is_still_recorded(self):
        rows = self._run({"A": 55.0, "B": 40.0}, ["A", "B"])
        self.assertEqual(len(rows), 1)
        m = rows[0]
        self.assertEqual(m.above_min_score, 0)
        self.assertEqual(m.proposals_created, 0)
        self.assertAlmostEqual(m.best_score, 55.0)

    def test_hard_filtered_symbols_are_counted_separately(self):
        rows = self._run({"A": -1.0, "B": -1.0, "C": 70.0},
                         ["A", "B", "C"])
        m = rows[0]
        self.assertEqual(m.rejected_hard_filter, 2)
        self.assertEqual(m.above_min_score, 1)

    def test_best_score_is_None_when_everything_was_hard_filtered(self):
        rows = self._run({"A": -1.0, "B": -1.0}, ["A", "B"])
        self.assertIsNone(rows[0].best_score)

    def test_an_empty_watchlist_records_NOTHING(self):
        """Nothing was scored, so a zero row would be a lie -- it would
        read as 'nothing was good enough' instead of 'nothing ran'."""
        rows = self._run({}, [])
        self.assertEqual(rows, [])

    def test_the_default_engine_records_nothing_and_still_works(self):
        """Why all 1631 pre-existing tests are unaffected."""
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        engine, broker, market_data, *_ = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn,
            watchlist=StaticWatchlistSource(("A",)),
            trade_evaluator=_Eval({"A": 70.0}),
        )
        self.assertIsNone(engine._cycle_metrics_recorder)
        market_data.set_price("A", 100.0)
        engine._lock.acquire(now=_now())
        engine.run_trigger_check(now=_now())   # must not raise
        self.assertTrue(trade_repo.list_for_symbol("A"))

    def test_a_raising_recorder_never_breaks_the_cycle(self):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        engine, broker, market_data, *_ = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn,
            watchlist=StaticWatchlistSource(("A",)),
            trade_evaluator=_Eval({"A": 70.0}),
        )
        engine._cycle_metrics_recorder = make_recorder(
            sqlite3.connect(":memory:"))   # no table -> writer fails
        market_data.set_price("A", 100.0)
        engine._lock.acquire(now=_now())
        engine.run_trigger_check(now=_now())   # must not raise
        self.assertTrue(trade_repo.list_for_symbol("A"),
                        "the trade must still have been created")


class _Boom:
    def rank(self, candidates):
        raise RuntimeError("evaluator blew up")


class TestTheEvaluatorFailureFallbackIsRecordedAsUNSCORED(unittest.TestCase):
    """Found by probing after the first full green run.

    Engine._check_watchlist has a fallback: if the evaluator raises, it
    opens a trade for EVERY candidate with no score gate at all, then
    returns. That path originally wrote no metrics row, so a cycle that
    created trades was invisible in the table and any per-day proposal
    total computed from it would be wrong.
    """

    def _run(self):
        from tests.engine.test_engine import (
            StaticWatchlistSource, _make_engine, _now, _repos,
        )
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        rows = []
        engine, broker, market_data, *_ = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn,
            watchlist=StaticWatchlistSource(("A", "B")),
            trade_evaluator=_Boom(),
        )
        engine._cycle_metrics_recorder = rows.append
        for s in ("A", "B"):
            market_data.set_price(s, 100.0)
        engine._lock.acquire(now=_now())
        engine.run_trigger_check(now=_now())
        return rows, trade_repo

    def test_the_cycle_is_recorded(self):
        rows, _ = self._run()
        self.assertEqual(len(rows), 1)

    def test_the_score_columns_are_None_not_zero(self):
        """Zero would read as 'scored, nothing was good enough' while
        trades were in fact opened."""
        m = self._run()[0][0]
        self.assertIsNone(m.above_min_score)
        self.assertIsNone(m.rejected_hard_filter)
        self.assertIsNone(m.best_score)
        self.assertFalse(m.scored)

    def test_proposals_created_carries_the_real_count(self):
        rows, trade_repo = self._run()
        self.assertEqual(rows[0].proposals_created, 2)
        self.assertTrue(trade_repo.list_for_symbol("A"))
        self.assertTrue(trade_repo.list_for_symbol("B"))

    def test_a_scored_cycle_still_reports_scored_true(self):
        rows = TestTheEngineRecordsEveryEvaluatedCycle._run(
            TestTheEngineRecordsEveryEvaluatedCycle(),
            {"A": 70.0}, ["A"])
        self.assertTrue(rows[0].scored)

    def test_nulls_round_trip_through_sqlite(self):
        conn = sqlite3.connect(":memory:")
        bootstrap_schema(conn)
        record_cycle_metrics(conn, _m(rejected_hard_filter=None,
                                      above_min_score=None,
                                      best_score=None, scored=False))
        r = conn.execute("SELECT rejected_hard_filter, above_min_score, "
                         "best_score FROM cycle_metrics").fetchone()
        self.assertEqual(r, (None, None, None))

    def test_the_controllers_query_excludes_unscored_cycles(self):
        """The query that answers his actual question must not be
        polluted by rows that were never scored."""
        conn = sqlite3.connect(":memory:")
        bootstrap_schema(conn)
        record_cycle_metrics(conn, _m(above_min_score=7))
        record_cycle_metrics(conn, _m(
            cycle_at=datetime(2026, 10, 6, 14, 30, tzinfo=timezone.utc),
            rejected_hard_filter=None, above_min_score=None,
            best_score=None, scored=False))
        rows = conn.execute(
            "SELECT above_min_score FROM cycle_metrics "
            "WHERE above_min_score IS NOT NULL").fetchall()
        self.assertEqual(rows, [(7,)])


class TestTheMigrationSplitterTrap(unittest.TestCase):
    """_apply_migration splits on ';' with no awareness of comments or
    string literals, so ONE semicolon inside a SQL comment silently
    cuts a CREATE TABLE in half.

    That is exactly what happened while writing 0008: the comment read
    "...without scoring any of them; writing 0 there..." and bootstrap
    failed with `sqlite3.OperationalError: incomplete input`. The
    splitter is pre-existing (see P-061); this test stops THIS file
    from reintroducing the fault, and any new migration from doing so.
    """

    @staticmethod
    def _non_comment(fragment):
        return "\n".join(
            line for line in fragment.splitlines()
            if line.strip() and not line.strip().startswith("--")
        ).strip()

    def test_the_naive_split_never_truncates_a_real_statement(self):
        """The guard, written to catch the real fault rather than a
        proxy for it.

        A ';' inside a comment is only harmful when the cut lands
        INSIDE a statement. Two existing migrations
        (0001_initial.sql:97, 0005_research.sql:3) have one in a
        comment and are fine, because their fragment is comments only
        and SQLite executes a comment-only string as a no-op -- this
        test confirms that distinction instead of banning the
        character. What it does catch is a fragment carrying real SQL
        that is no longer a complete statement, which is exactly how
        0008 first failed:
            sqlite3.OperationalError: incomplete input
        """
        import pathlib
        offenders = []
        for f in sorted(pathlib.Path("src/persistence/migrations")
                        .glob("*.sql")):
            sql = f.read_text(encoding="utf-8")
            for i, frag in enumerate(
                    [x.strip() for x in sql.split(";") if x.strip()]):
                if not self._non_comment(frag):
                    continue          # comment-only: a harmless no-op
                if not sqlite3.complete_statement(frag + ";"):
                    offenders.append(f"{f.name} fragment {i}")
        self.assertEqual(
            offenders, [],
            "_apply_migration splits on ';' with no awareness of "
            "comments or string literals, so these fragments are "
            f"truncated SQL: {offenders}")

    def test_0008_splits_into_exactly_two_statements(self):
        sql = open("src/persistence/migrations/0008_cycle_metrics.sql",
                   encoding="utf-8").read()
        frags = [s.strip() for s in sql.split(";") if s.strip()]
        self.assertEqual(len(frags), 2)
        self.assertIn("CREATE TABLE cycle_metrics", frags[0])
        self.assertIn("CREATE INDEX", frags[1])
