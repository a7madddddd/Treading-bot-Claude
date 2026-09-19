import sys
import unittest
from datetime import datetime, timedelta, timezone

from execution.models import CREATED, SUBMITTED_UNKNOWN, SUBMITTING, OrderExecution, OrderExecutionError
from execution.serialization import order_execution_to_row, row_to_order_execution


def _now():
    return datetime(2026, 9, 17, 14, 0, 0, tzinfo=timezone.utc)


def _minimal_execution(**overrides):
    kwargs = dict(
        proposal_id="P-1",
        execution_id="E-1",
        trade_id="T-1",
        client_order_id="C-1",
        requested_qty=10,
        created_at=_now(),
    )
    kwargs.update(overrides)
    return OrderExecution(**kwargs)


def _acknowledged_execution(**overrides):
    kwargs = dict(
        proposal_id="P-1",
        execution_id="E-1",
        trade_id="T-1",
        client_order_id="C-1",
        requested_qty=10,
        created_at=_now(),
        broker_order_id="B-1",
        status="accepted",
        last_broker_poll_at=_now() + timedelta(seconds=30),
    )
    kwargs.update(overrides)
    return OrderExecution(**kwargs)


class TestRoundTrip(unittest.TestCase):
    def test_minimal_created_execution_round_trips(self):
        original = _minimal_execution()
        restored = row_to_order_execution(order_execution_to_row(original))
        self.assertEqual(original, restored)

    def test_acknowledged_execution_round_trips(self):
        original = _acknowledged_execution()
        restored = row_to_order_execution(order_execution_to_row(original))
        self.assertEqual(original, restored)

    def test_terminal_filled_execution_round_trips(self):
        original = _acknowledged_execution(
            status="filled",
            is_broker_terminal=True,
            filled_qty=10,
            filled_avg_price=100.25,
        )
        restored = row_to_order_execution(order_execution_to_row(original))
        self.assertEqual(original, restored)

    def test_submitting_execution_round_trips(self):
        original = _minimal_execution(status=SUBMITTING)
        restored = row_to_order_execution(order_execution_to_row(original))
        self.assertEqual(original, restored)

    def test_submitted_unknown_execution_round_trips(self):
        original = _minimal_execution(status=SUBMITTED_UNKNOWN)
        restored = row_to_order_execution(order_execution_to_row(original))
        self.assertEqual(original, restored)


class TestIdentityFields(unittest.TestCase):
    def test_execution_id_preserved(self):
        row = order_execution_to_row(_minimal_execution(execution_id="E-DISTINCT"))
        self.assertEqual(row["execution_id"], "E-DISTINCT")
        self.assertEqual(row_to_order_execution(row).execution_id, "E-DISTINCT")

    def test_proposal_id_preserved(self):
        row = order_execution_to_row(_minimal_execution(proposal_id="P-DISTINCT"))
        self.assertEqual(row["proposal_id"], "P-DISTINCT")
        self.assertEqual(row_to_order_execution(row).proposal_id, "P-DISTINCT")

    def test_client_order_id_preserved(self):
        row = order_execution_to_row(_minimal_execution(client_order_id="C-DISTINCT"))
        self.assertEqual(row["client_order_id"], "C-DISTINCT")
        self.assertEqual(row_to_order_execution(row).client_order_id, "C-DISTINCT")


class TestBrokerOrderId(unittest.TestCase):
    def test_none_preserved(self):
        row = order_execution_to_row(_minimal_execution())
        self.assertIsNone(row["broker_order_id"])
        self.assertIsNone(row_to_order_execution(row).broker_order_id)

    def test_populated_value_preserved(self):
        row = order_execution_to_row(_acknowledged_execution(broker_order_id="B-XYZ"))
        self.assertEqual(row["broker_order_id"], "B-XYZ")
        self.assertEqual(row_to_order_execution(row).broker_order_id, "B-XYZ")


class TestQuantities(unittest.TestCase):
    def test_requested_qty_preserved(self):
        row = order_execution_to_row(_minimal_execution(requested_qty=37))
        self.assertEqual(row["requested_qty"], 37)
        self.assertEqual(row_to_order_execution(row).requested_qty, 37)

    def test_filled_qty_preserved(self):
        original = _acknowledged_execution(filled_qty=4, filled_avg_price=99.5)
        row = order_execution_to_row(original)
        self.assertEqual(row["filled_qty"], 4)
        self.assertEqual(row_to_order_execution(row).filled_qty, 4)


class TestFilledAvgPrice(unittest.TestCase):
    def test_none_preserved(self):
        row = order_execution_to_row(_minimal_execution())
        self.assertIsNone(row["filled_avg_price"])
        self.assertIsNone(row_to_order_execution(row).filled_avg_price)

    def test_populated_value_preserved(self):
        original = _acknowledged_execution(filled_qty=5, filled_avg_price=101.75)
        row = order_execution_to_row(original)
        self.assertEqual(row["filled_avg_price"], 101.75)
        self.assertEqual(row_to_order_execution(row).filled_avg_price, 101.75)


class TestStatus(unittest.TestCase):
    def test_created_preserved(self):
        row = order_execution_to_row(_minimal_execution(status=CREATED))
        self.assertEqual(row["status"], CREATED)
        self.assertEqual(row_to_order_execution(row).status, CREATED)

    def test_submitting_preserved(self):
        row = order_execution_to_row(_minimal_execution(status=SUBMITTING))
        self.assertEqual(row["status"], SUBMITTING)

    def test_submitted_unknown_preserved(self):
        row = order_execution_to_row(_minimal_execution(status=SUBMITTED_UNKNOWN))
        self.assertEqual(row["status"], SUBMITTED_UNKNOWN)

    def test_arbitrary_broker_status_string_preserved_unmodified(self):
        original = _acknowledged_execution(status="partially_filled", filled_qty=3, filled_avg_price=98.0)
        row = order_execution_to_row(original)
        self.assertEqual(row["status"], "partially_filled")
        self.assertEqual(row_to_order_execution(row).status, "partially_filled")


class TestIsBrokerTerminal(unittest.TestCase):
    def test_false_preserved(self):
        row = order_execution_to_row(_acknowledged_execution(is_broker_terminal=False))
        self.assertEqual(row["is_broker_terminal"], 0)
        self.assertFalse(row_to_order_execution(row).is_broker_terminal)

    def test_true_preserved(self):
        original = _acknowledged_execution(
            status="rejected", is_broker_terminal=True, filled_qty=0, filled_avg_price=None
        )
        row = order_execution_to_row(original)
        self.assertEqual(row["is_broker_terminal"], 1)
        self.assertTrue(row_to_order_execution(row).is_broker_terminal)


class TestTimestamps(unittest.TestCase):
    def test_created_at_conversion(self):
        t0 = _now()
        row = order_execution_to_row(_minimal_execution(created_at=t0))
        self.assertEqual(row["created_at"], t0.isoformat())
        self.assertEqual(row_to_order_execution(row).created_at, t0)

    def test_last_broker_poll_at_none_preserved(self):
        row = order_execution_to_row(_minimal_execution())
        self.assertIsNone(row["last_broker_poll_at"])
        self.assertIsNone(row_to_order_execution(row).last_broker_poll_at)

    def test_last_broker_poll_at_populated_preserved(self):
        polled_at = _now() + timedelta(minutes=2)
        original = _acknowledged_execution(last_broker_poll_at=polled_at)
        row = order_execution_to_row(original)
        self.assertEqual(row["last_broker_poll_at"], polled_at.isoformat())
        self.assertEqual(row_to_order_execution(row).last_broker_poll_at, polled_at)


class TestBooleanIntegerConversion(unittest.TestCase):
    def test_true_becomes_one(self):
        original = _acknowledged_execution(status="filled", is_broker_terminal=True, filled_qty=10, filled_avg_price=100.0)
        self.assertEqual(order_execution_to_row(original)["is_broker_terminal"], 1)

    def test_false_becomes_zero(self):
        original = _acknowledged_execution(is_broker_terminal=False)
        self.assertEqual(order_execution_to_row(original)["is_broker_terminal"], 0)

    def test_one_becomes_true(self):
        row = order_execution_to_row(
            _acknowledged_execution(status="filled", is_broker_terminal=True, filled_qty=10, filled_avg_price=100.0)
        )
        row["is_broker_terminal"] = 1
        self.assertIs(row_to_order_execution(row).is_broker_terminal, True)

    def test_zero_becomes_false(self):
        row = order_execution_to_row(_acknowledged_execution(is_broker_terminal=False))
        row["is_broker_terminal"] = 0
        self.assertIs(row_to_order_execution(row).is_broker_terminal, False)


class TestCorruptedRowRejection(unittest.TestCase):
    def test_empty_execution_id_raises_through_real_constructor(self):
        row = order_execution_to_row(_minimal_execution())
        row["execution_id"] = ""
        with self.assertRaises(OrderExecutionError):
            row_to_order_execution(row)

    def test_filled_qty_exceeding_requested_raises(self):
        row = order_execution_to_row(_minimal_execution())
        row["filled_qty"] = 999
        with self.assertRaises(OrderExecutionError):
            row_to_order_execution(row)

    def test_local_status_with_broker_order_id_raises(self):
        row = order_execution_to_row(_minimal_execution(status=SUBMITTING))
        row["broker_order_id"] = "B-1"
        with self.assertRaises(OrderExecutionError):
            row_to_order_execution(row)

    def test_broker_status_missing_broker_order_id_raises(self):
        row = order_execution_to_row(_acknowledged_execution())
        row["broker_order_id"] = None
        with self.assertRaises(OrderExecutionError):
            row_to_order_execution(row)

    def test_filled_qty_positive_without_price_raises(self):
        row = order_execution_to_row(_acknowledged_execution())
        row["filled_qty"] = 5
        row["filled_avg_price"] = None
        with self.assertRaises(OrderExecutionError):
            row_to_order_execution(row)

    def test_no_silent_repair_of_missing_required_field(self):
        row = order_execution_to_row(_minimal_execution())
        del row["proposal_id"]
        with self.assertRaises(KeyError):
            row_to_order_execution(row)


class TestNoExternalDependencies(unittest.TestCase):
    def test_no_trade_proposal_persistence_alpaca_dependency(self):
        module = sys.modules["execution.serialization"]
        source_modules = {
            getattr(value, "__module__", None)
            for value in vars(module).values()
            if hasattr(value, "__module__")
        }
        for forbidden_prefix in ("trade", "proposals", "persistence", "sqlite3", "alpaca"):
            for mod_name in source_modules:
                if mod_name and mod_name.startswith(forbidden_prefix):
                    self.fail(f"execution.serialization unexpectedly depends on {mod_name!r}")

    def test_no_alpaca_name_anywhere_in_module(self):
        module = sys.modules["execution.serialization"]
        for name in dir(module):
            self.assertNotIn("alpaca", name.lower())


class TestNoDerivedFieldsEmitted(unittest.TestCase):
    def test_row_contains_exactly_the_expected_keys_no_more_no_less(self):
        row = order_execution_to_row(_minimal_execution())
        expected_keys = {
            "execution_id",
            "proposal_id",
            "trade_id",
            "client_order_id",
            "side",
            "broker_order_id",
            "requested_qty",
            "filled_qty",
            "filled_avg_price",
            "status",
            "is_broker_terminal",
            "created_at",
            "last_broker_poll_at",
        }
        self.assertEqual(set(row.keys()), expected_keys)

    def test_no_revision_key(self):
        row = order_execution_to_row(_minimal_execution())
        self.assertNotIn("revision", row)

    def test_no_derived_label_keys(self):
        row = order_execution_to_row(_minimal_execution())
        self.assertNotIn("is_unresolved", row)
        self.assertNotIn("is_partially_filled", row)


if __name__ == "__main__":
    unittest.main()
