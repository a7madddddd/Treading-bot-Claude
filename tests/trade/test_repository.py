import unittest
from datetime import datetime, timezone

from trade.models import Trade
from trade.repository import (
    TradeAlreadyExistsError,
    TradeRecord,
    TradeRepository,
    TradeRepositoryError,
    TradeRevisionConflictError,
)


def _now():
    return datetime(2026, 9, 17, 14, 0, 0, tzinfo=timezone.utc)


class TestTradeRecord(unittest.TestCase):
    def test_holds_trade_and_revision(self):
        trade = Trade(trade_id="T-1", symbol="TSLA", created_at=_now())
        record = TradeRecord(trade=trade, revision=0)
        self.assertIs(record.trade, trade)
        self.assertEqual(record.revision, 0)

    def test_is_frozen(self):
        trade = Trade(trade_id="T-1", symbol="TSLA", created_at=_now())
        record = TradeRecord(trade=trade, revision=0)
        with self.assertRaises(Exception):
            record.revision = 1  # type: ignore[misc]


class TestExceptionHierarchy(unittest.TestCase):
    def test_specific_errors_are_trade_repository_errors(self):
        self.assertTrue(issubclass(TradeAlreadyExistsError, TradeRepositoryError))
        self.assertTrue(issubclass(TradeRevisionConflictError, TradeRepositoryError))

    def test_trade_repository_error_is_runtime_error(self):
        self.assertTrue(issubclass(TradeRepositoryError, RuntimeError))


class TestAbstractInterface(unittest.TestCase):
    def test_cannot_instantiate_abc_directly(self):
        with self.assertRaises(TypeError):
            TradeRepository()  # type: ignore[abstract]

    def test_incomplete_subclass_cannot_be_instantiated(self):
        class Incomplete(TradeRepository):
            def save(self, trade, *, now):
                raise NotImplementedError

        with self.assertRaises(TypeError):
            Incomplete()  # type: ignore[abstract]


if __name__ == "__main__":
    unittest.main()
