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

D-0093 (2026-10-08) then removed the age-based TTL for INITIAL_ENTRY
entirely -- GME was re-sent twice for no reason but the clock running
out, with its price never leaving D-0007's band. The notified_at/TTL
interaction this file tests is still real, just no longer reachable
through INITIAL_ENTRY: section 3 below now exercises it through a
LADDER proposal, which still age-expires. A dedicated test confirms
INITIAL_ENTRY no longer age-expires at all.
"""

from __future__ import annotations

import threading
import time
import unittest
from datetime import timedelta

from persistence.db import bootstrap_schema, connect
from proposals.models import ApprovalState, TradeAction
from proposals.repository import InMemoryProposalRepository

from proposals.proposal import build_trade_proposal
from proposals.models import FloorContext

from .test_engine import (
    StaticWatchlistSource, _active_trade, _make_engine, _now, _repos,
    _strategy,
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


def _engine_with_pending_ladder(notifier=None):
    """D-0093: a PENDING LADDER proposal for a trade that already holds
    shares. Age-based expiry still applies to this action -- unlike
    INITIAL_ENTRY -- so this is what exercises the notified_at/TTL
    interaction now that INITIAL_ENTRY no longer does."""
    trade_repo, proposal_repo, execution_repo, conn = _repos()
    kwargs = {} if notifier is None else {"notifier": notifier}
    engine, _b, market_data, _d, used_notifier, _es = _make_engine(
        trade_repo, proposal_repo, execution_repo, conn, **kwargs)
    _active_trade(trade_repo, trade_id="T-LIVE", symbol="TSLA",
                  price=100.0, shares=10, now=_now())
    market_data.set_price("TSLA", 100.0)
    engine._lock.acquire(now=_now())
    ladder = build_trade_proposal(
        proposal_id="T-LIVE-ladder_1-aaaa", trade_id="T-LIVE",
        action=TradeAction.LADDER_1, symbol="TSLA",
        current_price=100.0, as_of=_now(), strategy=_strategy(),
        floor_context=FloorContext.known(90.0),
    )
    proposal_repo.save(ladder)
    used_notifier.events.clear()
    return engine, proposal_repo, used_notifier, ladder.proposal_id


# ---------------------------------------------------------------- 3 --
class TestTheClockStartsWhenHeCouldSeeIt(unittest.TestCase):
    """D-0093 moved this mechanism off INITIAL_ENTRY -- it still governs
    LADDER expiry, so the fixture here is a ladder, not an initial
    entry."""

    def _case(self, *, notified):
        """A proposal whose message went out REPORT_GAP after the row was
        written -- the shape of every proposal before change 1.

        The engine now stamps notified_at itself, and mark_notified is
        first-write-wins, so the lagging send is forced at the row level.
        That is the only way to reproduce a pre-change row, which is
        exactly what the sweep must keep handling correctly.
        """
        engine, repo, _n, pid = _engine_with_pending_ladder()
        engine.run_reconciliation_tick(now=_now())
        value = (_now() + REPORT_GAP).isoformat() if notified else None
        engine._proposal_repo._conn.execute(
            "UPDATE proposals SET notified_at = ? WHERE proposal_id = ?",
            (value, pid),
        )
        engine._proposal_repo._conn.commit()
        self.assertEqual(
            repo.get(pid).notified_at,
            (_now() + REPORT_GAP) if notified else None,
            msg="fixture precondition")
        return engine, repo, pid

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
        stays NULL forever and the fix is inert (and P-093's send-gap
        measurement, which still reads notified_at, goes blind).

        Uses the plain INITIAL_ENTRY fixture: notified_at is still
        stamped on every proposal after D-0093, it is just no longer
        read by INITIAL_ENTRY's own (now-removed) age expiry."""
        engine, repo, _n = _engine_with_pending()
        self.assertEqual(_the_pending(repo).notified_at, _now())


class TestInitialEntryNoLongerAgeExpires(unittest.TestCase):
    """D-0093 (Controller, 2026-10-08): "the one hour waiting we didn't
    need it anymore" -- GME was re-sent at 14:38 and 15:37 purely because
    the clock ran out, not because its price had moved. INITIAL_ENTRY now
    retires ONLY on price drift (D-0090's PRICE_BAND_FRACTION, unchanged
    at 0.5%), never on age."""

    def test_an_initial_entry_survives_many_hours_at_a_flat_price(self):
        engine, repo, _n = _engine_with_pending()
        pid = _the_pending(repo).proposal_id
        engine.run_reconciliation_tick(now=_now() + TTL * 10)
        self.assertIs(repo.get(pid).approval_state, ApprovalState.PENDING)

    def test_a_LADDER_still_age_expires_the_same_day(self):
        """The contrast case, in the same file: age-based expiry is not
        gone, it is scoped."""
        engine, repo, _n, pid = _engine_with_pending_ladder()
        engine.run_reconciliation_tick(now=_now() + TTL)
        self.assertIs(repo.get(pid).approval_state, ApprovalState.EXPIRED)


class TestADeliveryFailureDoesNotStartTheClock(unittest.TestCase):
    def test_an_undelivered_proposal_keeps_the_creation_clock(self):
        """He never saw it, so he is not owed an hour from the send --
        and it must not become immortal either. Uses a LADDER proposal
        (D-0093): INITIAL_ENTRY no longer age-expires at all, delivered
        or not."""

        class _FailingNotifier:
            def __init__(self):
                self.events = []

            def send(self, event):
                from notifications.service import NotificationResult
                self.events.append(event)
                return NotificationResult(success=False, attempts=1)

        engine, proposal_repo, _n, pid = _engine_with_pending_ladder(
            notifier=_FailingNotifier())
        engine.run_reconciliation_tick(now=_now())

        proposal = proposal_repo.get(pid)
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
