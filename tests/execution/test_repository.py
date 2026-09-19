import unittest
from datetime import datetime, timezone

from execution.models import OrderExecution
from execution.repository import (
    OrderExecutionAlreadyExistsError,
    OrderExecutionRecord,
    OrderExecutionRepository,
    OrderExecutionRepositoryError,
    OrderExecutionRevisionConflictError,
    ProposalDoesNotExistError,
)


def _now():
    return datetime(2026, 9, 17, 14, 0, 0, tzinfo=timezone.utc)


def _execution(**overrides):
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


class TestOrderExecutionRecord(unittest.TestCase):
    def test_holds_execution_and_revision(self):
        execution = _execution()
        record = OrderExecutionRecord(execution=execution, revision=0)
        self.assertIs(record.execution, execution)
        self.assertEqual(record.revision, 0)

    def test_is_frozen(self):
        record = OrderExecutionRecord(execution=_execution(), revision=0)
        with self.assertRaises(Exception):
            record.revision = 1  # type: ignore[misc]


class TestExceptionHierarchy(unittest.TestCase):
    def test_specific_errors_are_order_execution_repository_errors(self):
        self.assertTrue(issubclass(OrderExecutionAlreadyExistsError, OrderExecutionRepositoryError))
        self.assertTrue(issubclass(OrderExecutionRevisionConflictError, OrderExecutionRepositoryError))
        self.assertTrue(issubclass(ProposalDoesNotExistError, OrderExecutionRepositoryError))

    def test_order_execution_repository_error_is_runtime_error(self):
        self.assertTrue(issubclass(OrderExecutionRepositoryError, RuntimeError))


class TestAbstractInterface(unittest.TestCase):
    def test_cannot_instantiate_abc_directly(self):
        with self.assertRaises(TypeError):
            OrderExecutionRepository()  # type: ignore[abstract]

    def test_incomplete_subclass_cannot_be_instantiated(self):
        class Incomplete(OrderExecutionRepository):
            def save(self, execution, *, now):
                raise NotImplementedError

        with self.assertRaises(TypeError):
            Incomplete()  # type: ignore[abstract]


if __name__ == "__main__":
    unittest.main()
