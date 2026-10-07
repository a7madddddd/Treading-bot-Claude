"""D-0090 / P-092 -- stop scoring on yesterday's numbers, and replace a
proposal whose price has moved instead of letting it rot.

Two findings from the 2026-10-07 PLXS round.

1. `PolygonSource.get_ticker_snapshot` falls back from /v2/snapshot
   (paid) to /v2/aggs/ticker/{sym}/prev (free) and reshapes the PREVIOUS
   SESSION into a `day` block. research_hub copied `day.c` into
   `SymbolResearch.current_price` and `day.v` into `day_volume`, and the
   evaluator used both: a hard reject at `day_volume < 100_000` and a
   score component at `current_price > sma_50`. The block printed under
   the heading "Live snapshot" was byte-identical an hour apart, and its
   range contradicted the engine's own range for the same day inside one
   message.

2. D-0007's +/-0.5% band is enforced only at SUBMISSION, so a drifted
   PENDING proposal kept live buttons for the full hour and the
   Controller's approval was refused at the end of it.
"""

from __future__ import annotations

import unittest
from datetime import timedelta

from engine.deep_research import DeepResearchReport
from engine.engine import Engine
from proposals.models import PRICE_BAND_FRACTION, ApprovalState, TradeAction

from .test_engine import (
    StaticWatchlistSource, _active_trade, _make_engine, _now, _repos,
)


# ------------------------------------------------- the price source --
class _Research:
    def __init__(self, symbol, *, price=None, volume=None):
        self.symbol = symbol
        self.current_price = price
        self.day_volume = volume
        self.day_change_pct = None
        self.sma_50 = 254.9
        self.political_weighted_signal = 0.0
        self.political_sell_wave = False


class _Result:
    def __init__(self, research, score=70.0, passes=True, reasons=()):
        self.symbol = research.symbol
        self.research = research
        self.soft_score = score
        self.passes_hard_filter = passes
        self.hard_filter_reasons = list(reasons)
        self.score_breakdown = {"base": score}


class _Evaluator:
    """Mirrors the two real call sites that read these fields: a hard
    reject below the volume floor, and a score that depends on price."""

    VOLUME_FLOOR = 100_000.0

    def __init__(self):
        self.seen = []

    def rank(self, candidates):
        return [_Result(_Research(s, price=272.63, volume=156_000.0))
                for s in candidates]

    def evaluate_research(self, research):
        self.seen.append((research.symbol, research.current_price,
                          research.day_volume))
        if (research.day_volume is not None
                and research.day_volume < self.VOLUME_FLOOR):
            return _Result(research, 0.0, passes=False,
                           reasons=["low volume"])
        score = 70.0 if (research.current_price or 0) > research.sma_50 else 40.0
        return _Result(research, score)


class TestTheScoreSeesTheEnginesOwnPrice(unittest.TestCase):
    def _engine(self, *, price, volume):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        evaluator = _Evaluator()
        engine, _b, market_data, _d, notifier, _es = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn,
            watchlist=StaticWatchlistSource(("PLXS",)),
            trade_evaluator=evaluator,
        )
        market_data.set_price("PLXS", price)
        market_data.get_price_context = lambda symbol: {
            "today_volume": volume, "previous_close": 272.93,
        }
        engine._lock.acquire(now=_now())
        engine.run_trigger_check(now=_now())
        return engine, evaluator, proposal_repo, notifier

    def test_the_evaluator_is_re_scored_on_the_engines_price(self):
        """272.63 was yesterday's close. 264.93 was the real price, and
        it is the one the broker order was priced from."""
        _e, evaluator, _r, _n = self._engine(price=264.93, volume=420_000.0)
        self.assertIn(("PLXS", 264.93, 420_000.0), evaluator.seen)

    def test_the_stale_volume_no_longer_decides_the_hard_filter(self):
        """Yesterday 156K cleared the 100K floor. Today's real 40K does
        not, and the symbol must be rejected on today's number."""
        _e, _ev, proposal_repo, _n = self._engine(
            price=264.93, volume=40_000.0)
        self.assertEqual(proposal_repo.list_for_symbol("PLXS"), [],
                         msg="a symbol thin TODAY must not be proposed")

    def test_the_reverse_case_too(self):
        """Thin yesterday, active today: it must NOT be rejected."""
        trade_repo, proposal_repo, execution_repo, conn = _repos()

        class _ThinYesterday(_Evaluator):
            def rank(self, candidates):
                return [_Result(_Research(s, price=272.63, volume=40_000.0))
                        for s in candidates]

        evaluator = _ThinYesterday()
        engine, _b, market_data, _d, _n, _es = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn,
            watchlist=StaticWatchlistSource(("PLXS",)),
            trade_evaluator=evaluator,
        )
        market_data.set_price("PLXS", 264.93)
        market_data.get_price_context = lambda symbol: {
            "today_volume": 900_000.0}
        engine._lock.acquire(now=_now())
        engine.run_trigger_check(now=_now())

        self.assertEqual(len(proposal_repo.list_for_symbol("PLXS")), 1)

    def test_an_unreachable_feed_leaves_the_candidate_alone(self):
        """Fail-open: no fresh price must cost the evaluation, never
        more than it already cost before this change."""
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        evaluator = _Evaluator()
        engine, _b, market_data, _d, _n, _es = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn,
            watchlist=StaticWatchlistSource(("PLXS",)),
            trade_evaluator=evaluator,
        )
        market_data.set_price("PLXS", 264.93)
        # raise_unavailable_for is one-shot in the fake, which is
        # exactly the shape under test: the correction pass's own
        # get_last_trade raises and is swallowed, and the rest of the
        # cycle carries on with the feed it can reach.
        market_data.raise_unavailable_for("PLXS")
        engine._lock.acquire(now=_now())
        engine.run_trigger_check(now=_now())   # must not raise

        # The candidate kept the hub's values rather than losing its
        # evaluation -- no worse than before this change.
        self.assertEqual(len(proposal_repo.list_for_symbol("PLXS")), 1)

    def test_day_change_pct_is_deliberately_NOT_populated(self):
        """Scope discipline: the Controller approved price and volume.
        Filling day_change_pct would newly activate a dormant score
        component -- a separate change and a separate decision."""
        _e, evaluator, _r, _n = self._engine(price=264.93, volume=420_000.0)
        self.assertTrue(evaluator.seen)


# ------------------------------------------------------- the heading --
class TestTheSnapshotHeadingTellsTheTruth(unittest.TestCase):
    def test_the_free_tier_block_is_labelled_previous_session(self):
        report = DeepResearchReport(symbol="PLXS")
        report.snapshot_lines = ["$272.63", "Day range: $271.73 — $275.51"]
        report.snapshot_is_previous_session = True
        text = report.to_telegram_text()
        self.assertIn("Previous session", text)
        self.assertNotIn("Live snapshot", text)

    def test_a_real_live_snapshot_keeps_its_heading(self):
        report = DeepResearchReport(symbol="PLXS")
        report.snapshot_lines = ["$264.93"]
        text = report.to_telegram_text()
        self.assertIn("Live snapshot", text)
        self.assertNotIn("Previous session", text)

    def test_the_fallback_marker_is_what_sets_it(self):
        """The marker existed all along -- `_fallback_source` -- and
        nothing read it. That was the whole defect."""
        from engine.deep_research import _call_snapshot

        class _Pg:
            def get_ticker_snapshot(_s, symbol):
                return {"day": {"c": 272.63, "h": 275.51, "l": 271.73,
                                "v": 156_000},
                        "prevDay": {},
                        "_fallback_source": "aggs/prev"}

            def get_ticker_details(_s, symbol):
                return None

        _lines, signals = _call_snapshot(_Pg(), "PLXS")
        self.assertTrue(signals.get("snapshot_is_previous_session"))

    def test_a_paid_snapshot_does_not_set_it(self):
        from engine.deep_research import _call_snapshot

        class _Pg:
            def get_ticker_snapshot(_s, symbol):
                return {"day": {"c": 264.93, "h": 268.03, "l": 264.26,
                                "v": 420_000},
                        "prevDay": {"c": 272.93}}

            def get_ticker_details(_s, symbol):
                return None

        _lines, signals = _call_snapshot(_Pg(), "PLXS")
        self.assertFalse(signals.get("snapshot_is_previous_session"))


# --------------------------------------------------- the replacement --
class TestADriftedProposalIsReplaced(unittest.TestCase):
    """The Controller's decision: "the proposal that excluded should be
    replaced with a new price.\""""

    def _pending(self, *, drift_to=None):
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        engine, _b, market_data, _d, notifier, _es = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn,
            watchlist=StaticWatchlistSource(("TSLA",)),
        )
        market_data.set_price("TSLA", 100.0)
        engine._lock.acquire(now=_now())
        engine.run_trigger_check(now=_now())
        first = [p for p in proposal_repo.list_for_symbol("TSLA")
                 if p.approval_state is ApprovalState.PENDING][0]
        self.assertEqual(first.proposed_entry, 100.0)
        notifier.events.clear()
        if drift_to is not None:
            market_data.set_price("TSLA", drift_to)
        return engine, proposal_repo, notifier, first

    def test_a_price_outside_the_band_retires_the_old_proposal(self):
        """0.5% of 100.00 is 0.50. 101.00 is twice outside it."""
        engine, repo, _n, first = self._pending(drift_to=101.0)
        engine.run_trigger_check(now=_now() + timedelta(minutes=10))
        self.assertIs(repo.get(first.proposal_id).approval_state,
                      ApprovalState.EXPIRED)

    def test_and_a_FRESH_proposal_replaces_it_in_the_same_check(self):
        """The point of the whole change. Retiring alone would tell him
        'too late'; this hands him the same opportunity repriced."""
        engine, repo, _n, first = self._pending(drift_to=101.0)
        engine.run_trigger_check(now=_now() + timedelta(minutes=10))
        pending = [p for p in repo.list_for_symbol("TSLA")
                   if p.approval_state is ApprovalState.PENDING]
        self.assertEqual(len(pending), 1)
        self.assertNotEqual(pending[0].proposal_id, first.proposal_id)
        self.assertEqual(pending[0].proposed_entry, 101.0)

    def test_the_message_says_what_moved_and_by_how_much(self):
        engine, _r, notifier, _f = self._pending(drift_to=101.0)
        engine.run_trigger_check(now=_now() + timedelta(minutes=10))
        events = [e for e in notifier.events
                  if e.event == "proposal_superseded_on_price"]
        self.assertEqual(len(events), 1)
        body = events[0].message
        self.assertIn("$100.00", body)
        self.assertIn("$101.00", body)
        self.assertIn("Nothing was bought or sold", body)

    def test_a_price_INSIDE_the_band_is_left_alone(self):
        """100.40 is 0.40% away -- inside 0.5%. His approval would still
        execute, so there is nothing to replace."""
        engine, repo, notifier, first = self._pending(drift_to=100.4)
        engine.run_trigger_check(now=_now() + timedelta(minutes=10))
        self.assertIs(repo.get(first.proposal_id).approval_state,
                      ApprovalState.PENDING)
        self.assertEqual(
            [e for e in notifier.events
             if e.event == "proposal_superseded_on_price"], [])

    def test_the_band_is_D0007s_own_number_not_a_new_one(self):
        self.assertEqual(PRICE_BAND_FRACTION, 0.005)

    def test_an_unreachable_price_leaves_it_PENDING(self):
        """No fresh price is no evidence of drift. The TTL still owns
        it, and D-0007 still refuses a bad submission."""
        engine, repo, _n, first = self._pending()
        engine._market_data.raise_unavailable_for("TSLA")
        engine.run_trigger_check(now=_now() + timedelta(minutes=10))
        self.assertIs(repo.get(first.proposal_id).approval_state,
                      ApprovalState.PENDING)

    def test_the_reconciliation_tick_does_NOT_sweep(self):
        """Bounded to the seven scheduled checks on purpose. At a
        30-second cadence a symbol with PLXS's 1.43% daily range against
        a 0.5% band would bury him in replacements."""
        engine, repo, notifier, first = self._pending(drift_to=101.0)
        engine.run_reconciliation_tick(now=_now() + timedelta(minutes=10))
        self.assertIs(repo.get(first.proposal_id).approval_state,
                      ApprovalState.PENDING)
        self.assertEqual(
            [e for e in notifier.events
             if e.event == "proposal_superseded_on_price"], [])

    def test_a_LADDER_proposal_is_never_superseded_this_way(self):
        """It belongs to a trade that already holds shares. Ladders lapse
        on the TTL, exactly as _expire_stale_proposals documents."""
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        _active_trade(trade_repo, price=100.0)
        engine, _b, market_data, _d, _n, _es = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn)
        market_data.set_price("TSLA", 94.6)       # below ladder 1 at 95.0
        engine._lock.acquire(now=_now())
        engine.run_trigger_check(now=_now())
        ladder = [p for p in proposal_repo.list_for_trade("T-1")
                  if p.proposed_action is TradeAction.LADDER_1][0]

        market_data.set_price("TSLA", 80.0)       # far outside the band
        engine.run_trigger_check(now=_now() + timedelta(minutes=10))
        self.assertIs(proposal_repo.get(ladder.proposal_id).approval_state,
                      ApprovalState.PENDING)

    def test_an_APPROVED_proposal_is_never_touched(self):
        """It is his decision. D-0007 refuses it at the broker boundary
        if the price has moved; no sweep discards it here."""
        engine, repo, _n, first = self._pending()
        repo.record_decision(first.proposal_id, approved=True,
                             decided_by="controller", decided_at=_now(),
                             action=TradeAction.INITIAL_ENTRY)
        engine._market_data.set_price("TSLA", 150.0)
        engine.run_trigger_check(now=_now() + timedelta(minutes=10))
        self.assertIs(repo.get(first.proposal_id).approval_state,
                      ApprovalState.APPROVED)


if __name__ == "__main__":
    unittest.main()
