import unittest
from datetime import datetime, timedelta, timezone

from proposals.models import TradeAction, approved_strategy_rule_set
from trade.models import InitialOrderStatus, Trade, TradeStateError
from trade.serialization import row_to_trade, trade_to_row


def _now():
    return datetime(2026, 9, 17, 14, 0, 0, tzinfo=timezone.utc)


def _strategy():
    return approved_strategy_rule_set()


def _new_trade(trade_id="T-1", symbol="TSLA"):
    return Trade(trade_id=trade_id, symbol=symbol, created_at=_now())


def _frozen_trade(price=100.0, filled_shares=10, trade_id="T-1", symbol="TSLA"):
    return _new_trade(trade_id, symbol).freeze_initial_reference(
        order_status=InitialOrderStatus.FILLED,
        filled_shares=filled_shares,
        fill_price=price,
        strategy=_strategy(),
        now=_now(),
    )


class TestRoundTripByStatus(unittest.TestCase):
    """describe_status()'s four cases, each round-tripped."""

    def test_awaiting_initial_fill(self):
        t = _new_trade()
        self.assertEqual(row_to_trade(trade_to_row(t)), t)

    def test_abandoned(self):
        t = _new_trade().freeze_initial_reference(
            order_status=InitialOrderStatus.EXPIRED,
            filled_shares=0,
            fill_price=None,
            strategy=_strategy(),
            now=_now(),
        )
        self.assertEqual(row_to_trade(trade_to_row(t)), t)

    def test_active(self):
        t = _frozen_trade()
        self.assertEqual(row_to_trade(trade_to_row(t)), t)

    def test_closed(self):
        t = _frozen_trade().reconcile_position(
            total_shares=0, weighted_avg_entry_price=None, strategy=_strategy(), now=_now()
        )
        self.assertEqual(row_to_trade(trade_to_row(t)), t)


class TestRoundTripAcrossTransitions(unittest.TestCase):
    def test_after_partial_cancelled_fill(self):
        t = _new_trade().freeze_initial_reference(
            order_status=InitialOrderStatus.CANCELLED,
            filled_shares=4,
            fill_price=101.5,
            strategy=_strategy(),
            now=_now(),
        )
        self.assertEqual(row_to_trade(trade_to_row(t)), t)

    def test_after_both_ladder_fills(self):
        t = _frozen_trade()
        t = t.record_ladder_fill(
            TradeAction.LADDER_1,
            order_id="o1",
            fill_price=95.0,
            fill_qty=10,
            new_total_shares=20,
            new_weighted_avg_entry_price=97.5,
            strategy=_strategy(),
        )
        t = t.record_ladder_fill(
            TradeAction.LADDER_2,
            order_id="o2",
            fill_price=92.0,
            fill_qty=20,
            new_total_shares=40,
            new_weighted_avg_entry_price=94.75,
            strategy=_strategy(),
        )
        self.assertEqual(row_to_trade(trade_to_row(t)), t)

    def test_after_trailing_activation_and_ratchet(self):
        t = _frozen_trade().activate_trailing(current_price=110.0, now=_now())
        t = t.ratchet_trailing(current_price=115.5, now=_now())
        self.assertEqual(row_to_trade(trade_to_row(t)), t)

    def test_after_reconciliation_bookkeeping_with_error(self):
        t = _frozen_trade().record_reconciliation(now=_now(), error="broker timeout")
        self.assertEqual(row_to_trade(trade_to_row(t)), t)

    def test_after_reconciliation_bookkeeping_error_cleared(self):
        t = _frozen_trade().record_reconciliation(now=_now(), error="x")
        t = t.record_reconciliation(now=_now() + timedelta(minutes=1))
        self.assertEqual(row_to_trade(trade_to_row(t)), t)

    def test_different_symbol_and_trade_id_round_trip_independently(self):
        t1 = _frozen_trade(price=100.0, trade_id="T-A", symbol="TSLA")
        t2 = _frozen_trade(price=50.0, trade_id="T-B", symbol="DELL")
        self.assertEqual(row_to_trade(trade_to_row(t1)), t1)
        self.assertEqual(row_to_trade(trade_to_row(t2)), t2)
        self.assertNotEqual(row_to_trade(trade_to_row(t1)), t2)


class TestProtectiveOrderLineageThreading(unittest.TestCase):
    def test_lineage_supplied_by_caller_not_reconstructed_from_row(self):
        t = _frozen_trade()
        t = t.update_protective_order(order_id="stop-1", stop_price=90.0, status="open", now=_now())
        t = t.update_protective_order(order_id="stop-2", stop_price=104.5, status="open", now=_now())
        row = trade_to_row(t)
        # protective_order_lineage is not a row key at all.
        self.assertNotIn("protective_order_lineage", row)
        # The caller must supply the lineage explicitly.
        rebuilt = row_to_trade(row, protective_order_lineage=("stop-1",))
        self.assertEqual(rebuilt.protective_order_lineage, ("stop-1",))
        self.assertEqual(rebuilt.protective_order_id, "stop-2")
        self.assertEqual(rebuilt, t)

    def test_default_lineage_is_empty_tuple(self):
        t = _frozen_trade().update_protective_order(order_id="stop-1", stop_price=90.0, status="open", now=_now())
        rebuilt = row_to_trade(trade_to_row(t))
        self.assertEqual(rebuilt.protective_order_lineage, ())
        self.assertEqual(rebuilt, t)


class TestFieldTypeMapping(unittest.TestCase):
    def test_enum_serialized_as_value_string(self):
        t = _frozen_trade()
        row = trade_to_row(t)
        self.assertEqual(row["initial_order_status"], "filled")
        self.assertIsInstance(row["initial_order_status"], str)

    def test_booleans_serialized_as_integers(self):
        t = _frozen_trade().activate_trailing(current_price=110.0, now=_now())
        row = trade_to_row(t)
        self.assertEqual(row["initial_order_reconciled"], 1)
        self.assertEqual(row["trailing_activated"], 1)
        self.assertIsInstance(row["initial_order_reconciled"], int)
        self.assertNotIsInstance(row["initial_order_reconciled"], bool)

    def test_datetimes_serialized_as_iso_strings(self):
        t = _frozen_trade()
        row = trade_to_row(t)
        self.assertIsInstance(row["created_at"], str)
        self.assertIsInstance(row["freeze_timestamp"], str)
        self.assertEqual(datetime.fromisoformat(row["freeze_timestamp"]), t.freeze_timestamp)

    def test_none_datetimes_serialize_to_none(self):
        t = _new_trade()
        row = trade_to_row(t)
        self.assertIsNone(row["freeze_timestamp"])
        self.assertIsNone(row["trailing_last_updated_at"])

    def test_optional_numeric_fields_pass_through_as_none(self):
        t = _new_trade()
        row = trade_to_row(t)
        self.assertIsNone(row["original_initial_entry_fill_price"])
        self.assertIsNone(row["initial_filled_shares"])
        self.assertIsNone(row["ladder1_price"])


class TestNeverPersistedFields(unittest.TestCase):
    def test_no_derived_active_floor_keys(self):
        t = _frozen_trade().activate_trailing(current_price=110.0, now=_now())
        row = trade_to_row(t)
        self.assertNotIn("active_floor_price", row)
        self.assertNotIn("active_floor_source", row)

    def test_no_status_key(self):
        t = _frozen_trade()
        row = trade_to_row(t)
        self.assertNotIn("status", row)

    def test_derived_property_recomputed_correctly_after_round_trip(self):
        t = _frozen_trade(price=100.0).activate_trailing(current_price=110.0, now=_now())
        rebuilt = row_to_trade(trade_to_row(t))
        self.assertEqual(rebuilt.active_floor_price, t.active_floor_price)
        self.assertEqual(rebuilt.active_floor_source, t.active_floor_source)


class TestCorruptedRowRejection(unittest.TestCase):
    def test_row_to_trade_always_goes_through_real_constructor(self):
        t = _frozen_trade()
        row = trade_to_row(t)
        # Corrupt: claim reconciled with a filled position but strip the
        # frozen reference -- violates Trade's own __post_init__ invariant.
        row["original_initial_entry_fill_price"] = None
        with self.assertRaises(TradeStateError):
            row_to_trade(row)

    def test_invalid_enum_value_raises(self):
        t = _new_trade()
        row = trade_to_row(t)
        row["initial_order_status"] = "not-a-real-status"
        with self.assertRaises(ValueError):
            row_to_trade(row)

    def test_inconsistent_ladder_fill_flag_raises(self):
        t = _frozen_trade()
        row = trade_to_row(t)
        row["ladder1_filled"] = 1  # fill fields all still None -> invariant violation
        with self.assertRaises(TradeStateError):
            row_to_trade(row)


if __name__ == "__main__":
    unittest.main()
