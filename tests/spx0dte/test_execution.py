import unittest
from typing import Optional

from execution.broker_client import (
    BrokerClient, BrokerOrderState, BrokerSubmissionAmbiguousError,
)
from spx0dte.execution import submit_entry_order


class FakeBroker(BrokerClient):
    def __init__(self, *, result=None, raise_exc=None):
        self._result = result
        self._raise_exc = raise_exc
        self.last_call = None

    def submit_order(self, *, client_order_id, symbol, side, quantity, limit_price):
        self.last_call = dict(
            client_order_id=client_order_id, symbol=symbol, side=side,
            quantity=quantity, limit_price=limit_price,
        )
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._result

    def get_order_by_client_order_id(self, client_order_id):
        raise NotImplementedError

    def cancel_order(self, client_order_id):
        raise NotImplementedError

    def get_cash_balance(self):
        raise NotImplementedError

    def get_account_equity(self):
        raise NotImplementedError

    def is_market_open(self):
        raise NotImplementedError


class TestSubmitEntryOrder(unittest.TestCase):
    def test_success_passes_buy_side_and_occ_symbol_unchanged(self):
        state = BrokerOrderState(
            broker_order_id="B-1", status="accepted", is_terminal=False,
            filled_qty=0, filled_avg_price=None,
        )
        broker = FakeBroker(result=state)
        result = submit_entry_order(
            broker, client_order_id="SPX-1", occ_symbol="SPXW261116C06900000",
            quantity=1, limit_price=1.55,
        )
        self.assertTrue(result.success)
        self.assertEqual(result.order_state, state)
        self.assertEqual(broker.last_call["side"], "buy")
        self.assertEqual(broker.last_call["symbol"], "SPXW261116C06900000")
        self.assertEqual(broker.last_call["quantity"], 1)

    def test_ambiguous_submission_reported_not_raised(self):
        broker = FakeBroker(raise_exc=BrokerSubmissionAmbiguousError("timeout"))
        result = submit_entry_order(
            broker, client_order_id="SPX-1", occ_symbol="SPXW261116C06900000",
            quantity=1, limit_price=1.55,
        )
        self.assertFalse(result.success)
        self.assertIn("ambiguous", result.error)

    def test_client_order_id_is_proposal_id(self):
        state = BrokerOrderState(
            broker_order_id="B-1", status="accepted", is_terminal=False,
            filled_qty=0, filled_avg_price=None,
        )
        broker = FakeBroker(result=state)
        submit_entry_order(
            broker, client_order_id="SPX-abc123", occ_symbol="SPXW261116P06900000",
            quantity=2, limit_price=2.00,
        )
        self.assertEqual(broker.last_call["client_order_id"], "SPX-abc123")


if __name__ == "__main__":
    unittest.main()
