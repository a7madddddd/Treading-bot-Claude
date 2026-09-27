"""Integration test: ExecutionService with a wired risk enforcer
raises PortfolioRiskViolatedError instead of submitting to the
broker when a portfolio rule fails. Without an enforcer wired
(default), behavior is unchanged from before D-0047."""

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from typing import Optional

from execution.broker_client import BrokerClient, BrokerOrderState
from execution.service import (
    ExecutionService, PortfolioRiskViolatedError,
)
from persistence.db import bootstrap_schema, connect
from proposals.proposal import build_trade_proposal
from proposals.repository import ApprovalState
from proposals.sqlite_repository import SqliteProposalRepository
from execution.sqlite_repository import SqliteOrderExecutionRepository
from trade.models import Trade, TradeAction
from trade.sqlite_repository import SqliteTradeRepository
from proposals.models import FloorContext, approved_strategy_rule_set
from risk.enforcer import PortfolioRiskEnforcer
from risk.models import (
    PortfolioRiskLimits, PortfolioSnapshot, PositionView,
)


NOW = datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc)


class _StubBroker:
    def submit_order(self, **kwargs):  # pragma: no cover -- must not be called
        raise AssertionError("broker.submit_order must NOT be called when "
                             "risk enforcer rejects")

    def get_order_by_client_order_id(self, cid):
        return None

    def cancel_order(self, cid):  # pragma: no cover
        return None


def _static_snapshot_builder(snapshot):
    return lambda: snapshot


class TestExecutionServiceRiskIntegration(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = connect(os.path.join(self._tmp.name, "db.sqlite"))
        bootstrap_schema(self.conn)
        self.addCleanup(self.conn.close)

        self.trades = SqliteTradeRepository(self.conn)
        self.props = SqliteProposalRepository(self.conn)
        self.execs = SqliteOrderExecutionRepository(self.conn)

        trade = self.trades.save(
            Trade(trade_id="T-1", symbol="TSLA", created_at=NOW), now=NOW,
        )
        strategy = approved_strategy_rule_set()
        proposal = build_trade_proposal(
            proposal_id="P-1", trade_id="T-1",
            action=TradeAction.INITIAL_ENTRY, symbol="TSLA",
            current_price=370.0, as_of=NOW, strategy=strategy,
            floor_context=FloorContext.no_existing_position(),
        )
        self.props.save(proposal)
        self.props.record_decision(
            "P-1", approved=True, decided_by="ctrl", decided_at=NOW,
            action=TradeAction.INITIAL_ENTRY,
        )

    def _service(self, *, enforcer=None):
        return ExecutionService(
            self.execs, self.props, self.trades, _StubBroker(),
            risk_enforcer=enforcer,
        )

    def test_rejected_when_single_symbol_cap_would_break(self):
        snapshot = PortfolioSnapshot(
            equity_current=50000.0, equity_at_day_open=50000.0,
            positions=(PositionView("TSLA", 5, 4800.0),),
        )
        enforcer = PortfolioRiskEnforcer(
            limits=PortfolioRiskLimits(),
            snapshot_builder=_static_snapshot_builder(snapshot),
        )
        service = self._service(enforcer=enforcer)
        with self.assertRaises(PortfolioRiskViolatedError) as ctx:
            service.submit_approved_proposal(
                "P-1", current_price=370.0,
                active_floor_price=50.0, now=NOW,
            )
        self.assertIn("TSLA", str(ctx.exception))

    def test_allowed_when_within_limits(self):
        snapshot = PortfolioSnapshot(
            equity_current=50000.0, equity_at_day_open=50000.0,
            positions=(),  # empty portfolio; adding one 370*qty TSLA
        )
        enforcer = PortfolioRiskEnforcer(
            limits=PortfolioRiskLimits(),
            snapshot_builder=_static_snapshot_builder(snapshot),
        )
        service = self._service(enforcer=enforcer)
        # Without a real broker, submission will fail elsewhere -- but
        # the risk check itself passes and we do NOT see PortfolioRisk...
        try:
            service.submit_approved_proposal(
                "P-1", current_price=370.0,
                active_floor_price=50.0, now=NOW,
            )
        except PortfolioRiskViolatedError:
            self.fail("risk enforcer should NOT have rejected a clean state")
        except Exception:
            pass  # any downstream broker/submit failure is fine here

    def test_no_enforcer_wired_means_no_check(self):
        # Even with a snapshot that would fail, absence of enforcer
        # means no PortfolioRiskViolatedError.
        service = self._service(enforcer=None)
        try:
            service.submit_approved_proposal(
                "P-1", current_price=370.0,
                active_floor_price=50.0, now=NOW,
            )
        except PortfolioRiskViolatedError:
            self.fail("no enforcer should mean no risk error")
        except Exception:
            pass  # downstream broker failure is fine


if __name__ == "__main__":
    unittest.main()
