"""D-0082: the per-symbol score breakdown of every evaluation cycle.

Why this exists: on 2026-10-06 five cycles each evaluated ten symbols
and produced zero proposals, and the cause could NOT be established
from the recorded data. `cycle_metrics` stores the cycle total only --
nobody reached 60, and nothing else. Which symbol was best, which of
the seven components earned anything, and whether a 0.0 meant "no
data" or "scored zero" were all unrecorded.

Every test drives the real Engine through run_trigger_check.
"""

import sqlite3
import unittest
from datetime import date, datetime, timedelta, timezone

from engine.cycle_metrics import (
    CycleMetrics, SymbolScore, make_recorder, record_cycle_metrics,
    record_symbol_scores,
)
from engine.engine import Engine
from persistence.db import APPROVED_SCHEMA_VERSION, bootstrap_schema
from tests.engine.test_engine import (
    StaticWatchlistSource, _make_engine, _now, _repos,
)


class _Research:
    def __init__(self, **kw):
        self.sources_succeeded = kw.get("ok", [])
        self.sources_failed = kw.get("bad", [])
        self.current_price = kw.get("price")
        self.rsi_14 = kw.get("rsi")
        self.day_volume = kw.get("vol")
        self.pe_ratio = kw.get("pe")


class _Res:
    def __init__(self, symbol, score, *, passes=True, breakdown=None,
                 research=None, reasons=()):
        self.symbol = symbol
        self.soft_score = score
        self.passes_hard_filter = passes
        self.hard_filter_reasons = list(reasons)
        self.score_breakdown = breakdown if breakdown is not None else {}
        self.research = research


class _Eval:
    def __init__(self, results):
        self._results = results

    def rank(self, candidates):
        by = {r.symbol: r for r in self._results}
        got = [by[s] for s in candidates if s in by]
        return sorted(got, key=lambda r: -r.soft_score)


FULL = {"fundamentals": 12.3, "technicals": 20.0, "momentum": 0.0,
        "news": 0.0, "trend": 6.4, "rel_str": 0.0, "political": 0.0,
        "risk": -0.0}


def _conn():
    c = sqlite3.connect(":memory:")
    bootstrap_schema(c)
    return c


class TestTheSchema(unittest.TestCase):
    def test_version_is_nine(self):
        self.assertEqual(APPROVED_SCHEMA_VERSION, 9)

    def test_the_table_exists_with_every_component_column(self):
        cols = {r[1] for r in _conn().execute(
            "PRAGMA table_info(cycle_symbol_scores)")}
        for c in ("symbol", "rank_in_cycle", "soft_score",
                  "passed_hard_filter", "hard_filter_reasons",
                  "s_fundamentals", "s_technicals", "s_momentum",
                  "s_news", "s_trend", "s_rel_strength", "s_political",
                  "s_risk_discount", "sources_succeeded",
                  "sources_failed", "current_price", "rsi_14",
                  "day_volume", "pe_ratio"):
            self.assertIn(c, cols)

    def test_0009_is_additive_only(self):
        sql = open("src/persistence/migrations/0009_cycle_symbol_scores.sql",
                   encoding="utf-8").read().upper()
        for forbidden in ("DROP ", "ALTER ", "DELETE ", "UPDATE "):
            self.assertNotIn(forbidden, sql)

    def test_no_semicolon_truncates_0009(self):
        """I made this exact mistake twice in one day: a ';' inside a
        SQL comment cuts the statement, because _apply_migration splits
        on ';' with no comment awareness (P-061)."""
        sql = open("src/persistence/migrations/0009_cycle_symbol_scores.sql",
                   encoding="utf-8").read()
        frags = [f.strip() for f in sql.split(";") if f.strip()]
        for f in frags:
            body = "\n".join(l for l in f.splitlines()
                             if l.strip() and not l.strip().startswith("--"))
            if body.strip():
                self.assertTrue(sqlite3.complete_statement(f + ";"))


def _metrics(symbols, **kw):
    base = dict(
        cycle_at=datetime(2026, 10, 6, 13, 30, tzinfo=timezone.utc),
        effective_date=date(2026, 10, 6), candidates_evaluated=len(symbols),
        rejected_hard_filter=0, above_min_score=0, min_score_required=60.0,
        best_score=max((s.soft_score for s in symbols), default=None),
        proposals_created=0, symbols=tuple(symbols), scored=True,
    )
    base.update(kw)
    return CycleMetrics(**base)


def _score(symbol, score, **kw):
    base = dict(rank_in_cycle=1, passed_hard_filter=True,
                hard_filter_reasons=(), components=dict(FULL),
                sources_succeeded=("finnhub", "polygon"),
                sources_failed=("alpha_vantage",),
                current_price=633.9, rsi_14=49.0, day_volume=3598665.0,
                pe_ratio=14.7)
    base.update(kw)
    return SymbolScore(symbol=symbol, soft_score=score, **base)


class TestWritingTheBreakdown(unittest.TestCase):
    def setUp(self):
        self.conn = _conn()

    def _rows(self):
        return self.conn.execute(
            "SELECT symbol, rank_in_cycle, soft_score, passed_hard_filter,"
            " hard_filter_reasons, s_fundamentals, s_technicals,"
            " s_momentum, s_news, s_trend, s_rel_strength, s_political,"
            " sources_succeeded, sources_failed, current_price, rsi_14,"
            " pe_ratio, scheduled_slot"
            " FROM cycle_symbol_scores ORDER BY rank_in_cycle").fetchall()

    def test_one_row_per_symbol(self):
        record_symbol_scores(self.conn, _metrics([
            _score("MUFG", 38.75, rank_in_cycle=1),
            _score("SMH", 19.70, rank_in_cycle=2),
            _score("ICLN", 25.89, rank_in_cycle=3),
        ]))
        self.assertEqual(len(self._rows()), 3)

    def test_every_component_round_trips(self):
        record_symbol_scores(self.conn, _metrics([_score("MUFG", 38.75)]))
        r = self._rows()[0]
        self.assertEqual(r[0], "MUFG")
        self.assertAlmostEqual(r[2], 38.75)
        self.assertAlmostEqual(r[5], 12.3)   # fundamentals
        self.assertAlmostEqual(r[6], 20.0)   # technicals
        self.assertAlmostEqual(r[7], 0.0)    # momentum -- a real zero
        self.assertAlmostEqual(r[9], 6.4)    # trend

    def test_a_MISSING_component_is_NULL_not_zero(self):
        """The distinction the whole table exists for. A hard-filtered
        symbol gets an EMPTY breakdown; writing 0.0 would claim it was
        scored and earned nothing."""
        record_symbol_scores(self.conn, _metrics([
            _score("TX", 0.0, passed_hard_filter=False, components={},
                   hard_filter_reasons=("no live price",)),
        ]))
        r = self._rows()[0]
        self.assertEqual(r[0], "TX")
        self.assertEqual(r[3], 0)                  # passed_hard_filter
        self.assertEqual(r[4], "no live price")
        for i in range(5, 12):                     # every s_* column
            self.assertIsNone(r[i], f"column {i} should be NULL")

    def test_the_source_lists_make_a_zero_readable(self):
        record_symbol_scores(self.conn, _metrics([_score("SMH", 19.70)]))
        r = self._rows()[0]
        self.assertEqual(r[12], "finnhub,polygon")
        self.assertEqual(r[13], "alpha_vantage")

    def test_the_raw_inputs_are_stored(self):
        record_symbol_scores(self.conn, _metrics([
            _score("SMH", 19.70, rsi_14=None, pe_ratio=None)]))
        r = self._rows()[0]
        self.assertAlmostEqual(r[14], 633.9)
        self.assertIsNone(r[15], "rsi None must stay NULL")
        self.assertIsNone(r[16], "pe None must stay NULL")

    def test_the_seven_firings_of_one_check_collapse_per_symbol(self):
        base = datetime(2026, 10, 6, 13, 28, 30, tzinfo=timezone.utc)
        for i in range(7):
            record_symbol_scores(self.conn, _metrics(
                [_score("MUFG", 30.0 + i)],
                cycle_at=base + timedelta(seconds=30 * i)))
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0][2], 36.0, msg="last firing wins")
        self.assertEqual(rows[0][17], "09:30")

    def test_the_unscored_path_writes_no_symbol_rows(self):
        record_symbol_scores(self.conn, _metrics(
            [], rejected_hard_filter=None, above_min_score=None,
            best_score=None, scored=False))
        self.assertEqual(self._rows(), [])

    def test_make_recorder_writes_BOTH_tables(self):
        make_recorder(self.conn)(_metrics([_score("MUFG", 38.75)]))
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM cycle_metrics")
            .fetchone()[0], 1)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM cycle_symbol_scores")
            .fetchone()[0], 1)

    def test_a_missing_table_never_breaks_the_recorder(self):
        make_recorder(sqlite3.connect(":memory:"))(
            _metrics([_score("MUFG", 38.75)]))   # must not raise


class TestTheEngineCapturesIt(unittest.TestCase):
    """End-to-end: what the engine actually hands the recorder."""

    def _run(self, results, symbols):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        captured = []
        engine, _b, market_data, *_ = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn,
            watchlist=StaticWatchlistSource(tuple(symbols)),
            trade_evaluator=_Eval(results))
        engine._cycle_metrics_recorder = captured.append
        for s in symbols:
            market_data.set_price(s, 100.0)
        engine._lock.acquire(now=_now())
        engine.run_trigger_check(now=_now())
        return captured

    def test_every_evaluated_symbol_is_captured_in_ranked_order(self):
        got = self._run([
            _Res("LOW", 30.0, breakdown=dict(FULL),
                 research=_Research(ok=["finnhub"], price=1.0)),
            _Res("MUFG", 55.0, breakdown=dict(FULL),
                 research=_Research(ok=["finnhub"], price=2.0)),
            _Res("SMH", 40.0, breakdown=dict(FULL),
                 research=_Research(ok=["polygon"], price=3.0)),
        ], ["LOW", "MUFG", "SMH"])
        syms = [s.symbol for s in got[0].symbols]
        self.assertEqual(syms, ["MUFG", "SMH", "LOW"])
        self.assertEqual([s.rank_in_cycle for s in got[0].symbols], [1, 2, 3])

    def test_the_components_reach_the_recorder(self):
        got = self._run([_Res("MUFG", 38.75, breakdown=dict(FULL),
                              research=_Research(ok=["finnhub"], rsi=49.0,
                                                 pe=14.7, price=10.0))],
                        ["MUFG"])
        s = got[0].symbols[0]
        self.assertAlmostEqual(s.components["fundamentals"], 12.3)
        self.assertAlmostEqual(s.rsi_14, 49.0)
        self.assertAlmostEqual(s.pe_ratio, 14.7)
        self.assertEqual(s.sources_succeeded, ("finnhub",))

    def test_a_hard_filtered_symbol_carries_its_reason_and_no_components(self):
        got = self._run([
            _Res("TX", 0.0, passes=False, reasons=["no live price"],
                 breakdown={}, research=_Research(rsi=61.0, pe=15.9)),
            _Res("MUFG", 50.0, breakdown=dict(FULL),
                 research=_Research(ok=["finnhub"], price=1.0)),
        ], ["TX", "MUFG"])
        tx = [s for s in got[0].symbols if s.symbol == "TX"][0]
        self.assertFalse(tx.passed_hard_filter)
        self.assertEqual(tx.hard_filter_reasons, ("no live price",))
        self.assertEqual(tx.components, {})
        self.assertAlmostEqual(tx.rsi_14, 61.0,
                               msg="the raw input is kept even when filtered")

    def test_research_None_costs_the_detail_not_the_cycle(self):
        """Every pre-existing engine test's fake evaluator sets
        research=None. That must never raise."""
        got = self._run([_Res("A", 70.0, breakdown=dict(FULL),
                              research=None)], ["A"])
        s = got[0].symbols[0]
        self.assertEqual(s.sources_succeeded, ())
        self.assertIsNone(s.current_price)
        self.assertAlmostEqual(s.soft_score, 70.0)

    def test_the_unscored_evaluator_failure_path_captures_no_symbols(self):
        class _Boom:
            def rank(self, c):
                raise RuntimeError("evaluator blew up")
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        captured = []
        engine, _b, market_data, *_ = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn,
            watchlist=StaticWatchlistSource(("A",)), trade_evaluator=_Boom())
        engine._cycle_metrics_recorder = captured.append
        market_data.set_price("A", 100.0)
        engine._lock.acquire(now=_now())
        engine.run_trigger_check(now=_now())
        self.assertEqual(captured[0].symbols, ())
        self.assertFalse(captured[0].scored)
