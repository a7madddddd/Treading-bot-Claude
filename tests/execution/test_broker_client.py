import unittest

from execution.broker_client import BrokerClient, BrokerClientError, BrokerOrderState, BrokerSubmissionAmbiguousError


class TestBrokerOrderState(unittest.TestCase):
    def test_holds_fields(self):
        state = BrokerOrderState(
            broker_order_id="B-1", status="accepted", is_terminal=False, filled_qty=0, filled_avg_price=None
        )
        self.assertEqual(state.broker_order_id, "B-1")
        self.assertEqual(state.status, "accepted")
        self.assertFalse(state.is_terminal)

    def test_is_frozen(self):
        state = BrokerOrderState(
            broker_order_id="B-1", status="accepted", is_terminal=False, filled_qty=0, filled_avg_price=None
        )
        with self.assertRaises(Exception):
            state.status = "filled"  # type: ignore[misc]


class TestExceptionHierarchy(unittest.TestCase):
    def test_ambiguous_error_is_broker_client_error(self):
        self.assertTrue(issubclass(BrokerSubmissionAmbiguousError, BrokerClientError))

    def test_broker_client_error_is_runtime_error(self):
        self.assertTrue(issubclass(BrokerClientError, RuntimeError))


class TestAbstractInterface(unittest.TestCase):
    def test_cannot_instantiate_abc_directly(self):
        with self.assertRaises(TypeError):
            BrokerClient()  # type: ignore[abstract]

    def test_incomplete_subclass_cannot_be_instantiated(self):
        class Incomplete(BrokerClient):
            def submit_order(self, **kwargs):
                raise NotImplementedError

        with self.assertRaises(TypeError):
            Incomplete()  # type: ignore[abstract]

    def test_subclass_missing_cancel_order_cannot_be_instantiated(self):
        class MissingCancel(BrokerClient):
            def submit_order(self, **kwargs):
                raise NotImplementedError

            def get_order_by_client_order_id(self, client_order_id):
                raise NotImplementedError

        with self.assertRaises(TypeError):
            MissingCancel()  # type: ignore[abstract]

    def test_complete_subclass_including_cancel_order_can_be_instantiated(self):
        class Complete(BrokerClient):
            def submit_order(self, **kwargs):
                raise NotImplementedError

            def get_order_by_client_order_id(self, client_order_id):
                raise NotImplementedError

            def cancel_order(self, client_order_id):
                raise NotImplementedError

        Complete()  # must not raise


if __name__ == "__main__":
    unittest.main()
