"""P-091 -- the 60-minute approval clock used to start before the
Controller could see the proposal.

Measured on 2026-10-07, twice, from the live log:

    proposal 1   created 18:28:43.288   sent 18:38:35.693   gap 9m52s
    proposal 2   created 19:28:44.676   sent 19:38:31.054   gap 9m47s

The research report is assembled between saving the proposal row and
sending the message, and the TTL was measured from the row. PLXS expired
at 15:28:44 ET on a clock that started at 14:28:43 ET, while the message
had only reached the Controller at 14:38:35 ET. He was handed 50 minutes
of a 60-minute window, and the price he read was ten minutes old.

Three Controller-approved changes (2026-10-07), one section each:
  1. the research fetch moves BEFORE the row is written,
  2. it runs under a timeout,
  3. the TTL measures from notified_at, falling back to
     proposal_created_at.

The 9m52s gap of the real incident is reused verbatim below, so these
tests fail against the pre-change engine for the real reason.
"""

from __future__ import annotations

import threading
import time
import unittest
from datetime import timedelta

from persistence.db import bootstrap_schema, connect
from proposals.models import ApprovalState, TradeAction
from proposals.repository import InMemoryProposalRepository

from .test_engine import (
    StaticWatchlistSource, _make_engine, _now, _repos,
)

REPORT_GAP = timedelta(minutes=9, seconds=52)   # the measured gap
TTL = timedelta(seconds=3600)


def _engine_with_pending(*, enricher=None, timeout=None):
    """One PENDING INITIAL_ENTRY for TSLA, created at _now()."""
    trade_repo, proposal_repo, execution_repo, conn = _repos()
    engine, _broker, market_data, _dec, notifier, _es = _make_engine(
        trade_repo, proposal_repo, execution_repo, conn,
        watchlist=StaticWatchlistSource(("TSLA",)),
    )
    market_data.set_price("TSLA", 100.0)
    if enricher is not None:
        engine._proposal_enricher = enricher
    if timeout is not None:
        engine.ENRICHMENT_TIMEOUT_SECONDS = timeout
    engine._lock.acquire(now=_now())
    notifier.events.clear()
    engine.run_trigger_check(now=_now())
    return engine, proposal_repo, notifier


def _the_pending(proposal_repo):
    pending = [p for p in proposal_repo.list_for_symbol("TSLA")
               if p.approval_state is ApprovalState.PENDING]
    assert len(pending) == 1, f"fixture produced {len(pending)} pending"
    assert pending[0].proposed_action is TradeAction.INITIAL_ENTRY
    return pending[0]


# ---------------------------------------------------------------- 3 --
class TestTheClockStartsWhenHeCouldSeeIt(unittest.TestCase):
    def _case(self, *, notified):
        """A proposal whose message went out REPORT_GAP after the row was
        written -- the shape of every proposal before change 1.

        The engine now stamps notified_at itself, and mark_notified is
        first-write-wins, so the lagging send is forced at the row level.
        That is the only way to reproduce a pre-change row, which is
        exactly what the sweep must keep handling correctly.
        """
        engine, repo, _n = _engine_with_pending()
        proposal = _the_pending(repo)
        value = (_now() + REPORT_GAP).isoformat() if notified else None
        engine._proposal_repo._conn.execute(
            "UPDATE proposals SET notified_at = ? WHERE proposal_id = ?",
            (value, proposal.proposal_id),
        )
        engine._proposal_repo._conn.commit()
        self.assertEqual(
            repo.get(proposal.proposal_id).notified_at,
            (_now() + REPORT_GAP) if notified else None,
            msg="fixture precondition")
        return engine, repo, proposal.proposal_id

    def _state(self, repo, pid):
        return repo.get(pid).approval_state

    def test_the_moment_PLXS_died_no_longer_expires_it(self):
        """Exactly one hour after the row was written. Under the old
        clock this expired; the message was only 50 minutes old."""
        engine, repo, pid = self._case(notified=True)
        engine.run_reconciliation_tick(now=_now() + TTL)
        self.assertIs(self._state(repo, pid), ApprovalState.PENDING)

    def test_he_gets_the_full_hour_measured_from_the_message(self):
        engine, repo, pid = self._case(notified=True)
        engine.run_reconciliation_tick(
            now=_now() + REPORT_GAP + TTL - timedelta(seconds=1))
        self.assertIs(self._state(repo, pid), ApprovalState.PENDING)

    def test_and_it_STILL_expires_an_hour_after_the_message(self):
        """The TTL is re-anchored, never weakened."""
        engine, repo, pid = self._case(notified=True)
        engine.run_reconciliation_tick(now=_now() + REPORT_GAP + TTL)
        self.assertIs(self._state(repo, pid), ApprovalState.EXPIRED)

    def test_a_row_with_no_notified_at_keeps_the_OLD_clock(self):
        """Every pre-migration row, and every proposal whose delivery
        failed. Nothing that used to expire stops expiring."""
        engine, repo, pid = self._case(notified=False)
        engine.run_reconciliation_tick(now=_now() + TTL)
        self.assertIs(self._state(repo, pid), ApprovalState.EXPIRED)

    def test_a_delivered_proposal_is_marked_automatically(self):
        """Not a unit test of mark_notified -- a test that the engine
        actually calls it on the delivery path. Without this the column
        stays NULL forever and the fix is inert."""
        engine, repo, _n = _engine_with_pending()
        self.assertEqual(_the_pending(repo).notified_at, _now())


class TestADeliveryFailureDoesNotStartTheClock(unittest.TestCase):
    def test_an_undelivered_proposal_keeps_the_creation_clock(self):
        """He never saw it, so he is not owed an hour from the send --
        and it must not become immortal either."""
        trade_repo, proposal_repo, execution_repo, conn = _repos()

        class _FailingNotifier:
            def __init__(self):
                self.events = []

            def send(self, event):
                from notifications.service import NotificationResult
                self.events.append(event)
                return NotificationResult(success=False, attempts=1)

        engine, _b, market_data, _d, _n, _es = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn,
            watchlist=StaticWatchlistSource(("TSLA",)),
            notifier=_FailingNotifier(),
        )
        market_data.set_price("TSLA", 100.0)
        engine._lock.acquire(now=_now())
        engine.run_trigger_check(now=_now())

        proposal = _the_pending(proposal_repo)
        self.assertIsNone(proposal.notified_at)

        engine.run_reconciliation_tick(now=_now() + TTL)
        self.assertIs(
            proposal_repo.get(proposal.proposal_id).approval_state,
            ApprovalState.EXPIRED)


# ---------------------------------------------------------------- 1 --
class TestTheResearchIsFetchedBeforeTheClockStarts(unittest.TestCase):
    def test_the_research_runs_BEFORE_the_row_is_saved(self):
        """The whole point of change 1, stated as an ordering.

        Observed by tracing the two calls rather than by querying the
        repository from inside the enricher: `enrich()` now runs on a
        worker thread, and an sqlite3 connection belongs to the thread
        that opened it, so a query there would raise and be swallowed as
        an ordinary enrichment failure -- the test would pass for the
        wrong reason. list.append is atomic, so the order is observed
        safely from either thread.
        """
        trade_repo, proposal_repo, execution_repo, conn = _repos()
        engine, _b, market_data, _d, _n, _es = _make_engine(
            trade_repo, proposal_repo, execution_repo, conn,
            watchlist=StaticWatchlistSource(("TSLA",)),
        )
        market_data.set_price("TSLA", 100.0)

        order: list = []
        original_start = engine._trade_proposal_service.start_trade

        def _traced(**kwargs):
            order.append("save")
            return original_start(**kwargs)

        engine._trade_proposal_service.start_trade = _traced

        class _Spy:
            def enrich(_self, symbol):
                order.append("enrich")
                return "research block"

        engine._proposal_enricher = _Spy()
        engine._lock.acquire(now=_now())
        engine.run_trigger_check(now=_now())

        self.assertEqual(
            order, ["enrich", "save"],
            msg="the clock must not start before the slow work is done")

    def test_the_blurb_still_reaches_the_message(self):
        class _Enricher:
            def enrich(_self, symbol):
                return "UNMISTAKABLE-RESEARCH-MARKER"

        _engine, _repo, notifier = _engine_with_pending(enricher=_Enricher())
        bodies = [e.message for e in notifier.events
                  if e.event.endswith("proposal_awaiting_approval")]
        self.assertEqual(len(bodies), 1)
        self.assertIn("UNMISTAKABLE-RESEARCH-MARKER", bodies[0])

    def test_the_research_is_fetched_exactly_once_per_proposal(self):
        calls: list = []

        class _Counting:
            def enrich(_self, symbol):
                calls.append(symbol)
                return "block"

        _engine, _repo, _n = _engine_with_pending(enricher=_Counting())
        self.assertEqual(calls, ["TSLA"],
                         msg="the prefetch must not double-fetch")


# ---------------------------------------------------------------- 2 --
class TestAHungReportCannotEatTheWindow(unittest.TestCase):
    def test_a_hanging_enricher_is_abandoned_and_the_proposal_still_goes(self):
        """The 2026-10-07 shape: one symbol's report took 9m52s."""
        released = threading.Event()

        class _Hanging:
            def enrich(_self, symbol):
                released.wait(timeout=5)
                return "TOO-LATE"

        started = time.monotonic()
        try:
            _engine, repo, notifier = _engine_with_pending(
                enricher=_Hanging(), timeout=0.2)
            elapsed = time.monotonic() - started

            self.assertLess(elapsed, 10.0,
                            msg="a hung call must not pin the engine loop")
            bodies = [e.message for e in notifier.events
                      if e.event.endswith("proposal_awaiting_approval")]
            self.assertEqual(
                len(bodies), 1,
                msg="a hung report must not cost the Controller the proposal")
            self.assertNotIn("TOO-LATE", bodies[0])
            self.assertIn("enrichment_timeout",
                          [e.event for e in notifier.events])
            self.assertIsNotNone(_the_pending(repo).notified_at)
        finally:
            released.set()

    def test_the_timeout_notice_is_OPTIONAL_not_an_alarm(self):
        """A dropped research block is not a trading fault. P-085's
        lesson: an alarm on tolerable behavior trains him to ignore
        alarms."""
        released = threading.Event()

        class _Hanging:
            def enrich(_self, symbol):
                released.wait(timeout=5)

        try:
            _engine, _repo, notifier = _engine_with_pending(
                enricher=_Hanging(), timeout=0.2)
            timeouts = [e for e in notifier.events
                        if e.event == "enrichment_timeout"]
            self.assertEqual(len(timeouts), 1)
            self.assertEqual(timeouts[0].level.value, "OPTIONAL")
        finally:
            released.set()

    def test_an_enricher_that_RAISES_still_sends_the_proposal(self):
        """The pre-existing fail-open guarantee must survive the move."""
        class _Broken:
            def enrich(_self, symbol):
                raise RuntimeError("the research provider is down")

        _engine, repo, notifier = _engine_with_pending(enricher=_Broken())
        bodies = [e.message for e in notifier.events
                  if e.event.endswith("proposal_awaiting_approval")]
        self.assertEqual(len(bodies), 1)
        self.assertIsNotNone(_the_pending(repo).notified_at)


# ------------------------------------------------------- persistence --
class TestNotifiedAtSurvivesTheDatabase(unittest.TestCase):
    """The column is useless if a restart loses it: the sweep would fall
    back to the old clock on every recovered proposal."""

    def test_the_timestamp_round_trips_through_sqlite(self):
        engine, repo, _n = _engine_with_pending()
        proposal = _the_pending(repo)
        reread = repo.get(proposal.proposal_id)
        self.assertEqual(reread.notified_at, _now())

    def test_marking_twice_does_NOT_extend_his_deadline(self):
        """A recovery re-send must not push the clock forward, or a
        restart loop would keep a proposal alive indefinitely."""
        _engine, repo, _n = _engine_with_pending()
        pid = _the_pending(repo).proposal_id
        again = repo.mark_notified(pid, notified_at=_now() + timedelta(hours=1))
        self.assertEqual(again.notified_at, _now())
        self.assertEqual(repo.get(pid).notified_at, _now())

    def test_an_unknown_proposal_returns_None_rather_than_raising(self):
        """It is a bookkeeping write on the delivery path. Raising there
        would undo a proposal he has already received."""
        self.assertIsNone(InMemoryProposalRepository().mark_notified(
            "no-such-id", notified_at=_now()))

    def test_the_two_repositories_agree(self):
        """The in-memory repository backs the tests; a divergence here
        would mean every other test is measuring a different engine from
        the one that runs on the VM."""
        _engine, sqlite_repo, _n = _engine_with_pending()
        stored = sqlite_repo.get(_the_pending(sqlite_repo).proposal_id)
        self.assertEqual(stored.notified_at, _now())

        mem = InMemoryProposalRepository()
        fresh = sqlite_repo.get(stored.proposal_id)
        self.assertEqual(fresh.notified_at, _now())
        self.assertIsNone(
            mem.mark_notified(stored.proposal_id, notified_at=_now()),
            msg="an id the in-memory repo never saw returns None")

    def test_the_column_exists_after_bootstrap(self):
        conn = connect(":memory:")
        bootstrap_schema(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(proposals)")}
        self.assertIn("notified_at", cols)


if __name__ == "__main__":
    unittest.main()
