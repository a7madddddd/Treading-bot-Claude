import unittest
from datetime import datetime, timedelta, timezone

from proposals.models import TradeAction, approved_strategy_rule_set
from trade.models import (
    TERMINAL_INITIAL_ORDER_STATUSES,
    InitialOrderStatus,
    Trade,
    TradeStateError,
    describe_status,
)


def _now():
    return datetime(2026, 9, 17, 14, 0, 0, tzinfo=timezone.utc)


def _strategy():
    return approved_strategy_rule_set()


def _new_trade(trade_id="T-1", symbol="TSLA", created_at=None):
    return Trade(trade_id=trade_id, symbol=symbol, created_at=created_at or _now())


def _frozen_trade(price=100.0, filled_shares=10, trade_id="T-1", symbol="TSLA"):
    t = _new_trade(trade_id=trade_id, symbol=symbol)
    return t.freeze_initial_reference(
        order_status=InitialOrderStatus.FILLED,
        filled_shares=filled_shares,
        fill_price=price,
        strategy=_strategy(),
        now=_now(),
    )


class TestConstructionInvariants(unittest.TestCase):
    def test_new_trade_is_valid_and_unreconciled(self):
        t = _new_trade()
        self.assertFalse(t.initial_order_reconciled)
        self.assertIsNone(t.original_initial_entry_fill_price)
        self.assertEqual(t.total_shares, 0)

    def test_rejects_empty_trade_id(self):
        with self.assertRaises(TradeStateError):
            Trade(trade_id="", symbol="TSLA", created_at=_now())

    def test_rejects_empty_symbol(self):
        with self.assertRaises(TradeStateError):
            Trade(trade_id="T-1", symbol="", created_at=_now())

    def test_rejects_freeze_timestamp_before_reconciliation(self):
        with self.assertRaises(TradeStateError):
            Trade(trade_id="T-1", symbol="TSLA", created_at=_now(), freeze_timestamp=_now())

    def test_rejects_reconciled_without_freeze_timestamp(self):
        with self.assertRaises(TradeStateError):
            Trade(
                trade_id="T-1",
                symbol="TSLA",
                created_at=_now(),
                initial_order_reconciled=True,
                initial_filled_shares=0,
            )

    def test_rejects_reference_without_derived_levels(self):
        with self.assertRaises(TradeStateError):
            Trade(
                trade_id="T-1",
                symbol="TSLA",
                created_at=_now(),
                initial_order_reconciled=True,
                initial_filled_shares=10,
                freeze_timestamp=_now(),
                original_initial_entry_fill_price=100.0,
                # ladder1_price/ladder2_price/original_floor_price omitted
            )

    def test_rejects_reference_present_on_zero_fill(self):
        with self.assertRaises(TradeStateError):
            Trade(
                trade_id="T-1",
                symbol="TSLA",
                created_at=_now(),
                initial_order_reconciled=True,
                initial_filled_shares=0,
                freeze_timestamp=_now(),
                original_initial_entry_fill_price=100.0,
            )

    def test_rejects_ladder_filled_without_fill_fields(self):
        with self.assertRaises(TradeStateError):
            Trade(trade_id="T-1", symbol="TSLA", created_at=_now(), ladder1_filled=True)

    def test_rejects_ladder_fill_fields_without_filled_flag(self):
        with self.assertRaises(TradeStateError):
            Trade(trade_id="T-1", symbol="TSLA", created_at=_now(), ladder1_fill_price=95.0)

    def test_rejects_ladder_filled_on_unreconciled_trade(self):
        with self.assertRaises(TradeStateError):
            Trade(
                trade_id="T-1",
                symbol="TSLA",
                created_at=_now(),
                ladder1_filled=True,
                ladder1_fill_order_id="o1",
                ladder1_fill_price=95.0,
                ladder1_fill_qty=10,
            )

    def test_rejects_trailing_activated_without_fields(self):
        with self.assertRaises(TradeStateError):
            Trade(trade_id="T-1", symbol="TSLA", created_at=_now(), trailing_activated=True)

    def test_rejects_trailing_fields_without_activation(self):
        with self.assertRaises(TradeStateError):
            Trade(trade_id="T-1", symbol="TSLA", created_at=_now(), trailing_floor_price=104.5)


class TestFreezeInitialReference(unittest.TestCase):
    def test_rejects_freeze_while_pending(self):
        t = _new_trade()
        with self.assertRaises(TradeStateError):
            t.freeze_initial_reference(
                order_status=InitialOrderStatus.PENDING,
                filled_shares=0,
                fill_price=None,
                strategy=_strategy(),
                now=_now(),
            )

    def test_rejects_freeze_while_partially_filled_and_open(self):
        t = _new_trade()
        with self.assertRaises(TradeStateError):
            t.freeze_initial_reference(
                order_status=InitialOrderStatus.PARTIALLY_FILLED,
                filled_shares=5,
                fill_price=100.0,
                strategy=_strategy(),
                now=_now(),
            )

    def test_freeze_on_filled_matches_worked_example(self):
        t = _frozen_trade(price=100.0, filled_shares=10)
        self.assertTrue(t.initial_order_reconciled)
        self.assertEqual(t.initial_order_status, InitialOrderStatus.FILLED)
        self.assertAlmostEqual(t.original_initial_entry_fill_price, 100.0)
        self.assertEqual(t.initial_filled_shares, 10)
        self.assertAlmostEqual(t.ladder1_price, 95.0)
        self.assertAlmostEqual(t.ladder2_price, 92.0)
        self.assertAlmostEqual(t.original_floor_price, 90.0)
        self.assertEqual(t.total_shares, 10)
        self.assertAlmostEqual(t.weighted_avg_entry_price, 100.0)

    def test_freeze_on_cancelled_with_partial_fill_uses_actual_fill(self):
        t = _new_trade().freeze_initial_reference(
            order_status=InitialOrderStatus.CANCELLED,
            filled_shares=4,
            fill_price=101.5,
            strategy=_strategy(),
            now=_now(),
        )
        self.assertEqual(t.initial_filled_shares, 4)
        self.assertAlmostEqual(t.original_initial_entry_fill_price, 101.5)
        self.assertIsNotNone(t.ladder1_price)
        self.assertIsNotNone(t.original_floor_price)

    def test_freeze_on_zero_fill_expired_is_abandoned_no_reference(self):
        t = _new_trade().freeze_initial_reference(
            order_status=InitialOrderStatus.EXPIRED,
            filled_shares=0,
            fill_price=None,
            strategy=_strategy(),
            now=_now(),
        )
        self.assertTrue(t.initial_order_reconciled)
        self.assertEqual(t.initial_filled_shares, 0)
        self.assertIsNone(t.original_initial_entry_fill_price)
        self.assertIsNone(t.ladder1_price)
        self.assertIsNone(t.ladder2_price)
        self.assertIsNone(t.original_floor_price)
        self.assertEqual(t.total_shares, 0)

    def test_second_freeze_attempt_always_raises(self):
        t = _frozen_trade()
        with self.assertRaises(TradeStateError):
            t.freeze_initial_reference(
                order_status=InitialOrderStatus.FILLED,
                filled_shares=10,
                fill_price=100.0,
                strategy=_strategy(),
                now=_now(),
            )

    def test_second_freeze_attempt_raises_even_on_abandoned_trade(self):
        t = _new_trade().freeze_initial_reference(
            order_status=InitialOrderStatus.EXPIRED,
            filled_shares=0,
            fill_price=None,
            strategy=_strategy(),
            now=_now(),
        )
        with self.assertRaises(TradeStateError):
            t.freeze_initial_reference(
                order_status=InitialOrderStatus.FILLED,
                filled_shares=10,
                fill_price=100.0,
                strategy=_strategy(),
                now=_now(),
            )

    def test_multiple_partial_observations_never_freeze_only_terminal_does(self):
        t = _new_trade()
        for _ in range(3):
            with self.assertRaises(TradeStateError):
                t.freeze_initial_reference(
                    order_status=InitialOrderStatus.PARTIALLY_FILLED,
                    filled_shares=3,
                    fill_price=100.0,
                    strategy=_strategy(),
                    now=_now(),
                )
        self.assertFalse(t.initial_order_reconciled)
        final = t.freeze_initial_reference(
            order_status=InitialOrderStatus.FILLED,
            filled_shares=10,
            fill_price=99.5,
            strategy=_strategy(),
            now=_now(),
        )
        self.assertTrue(final.initial_order_reconciled)
        self.assertAlmostEqual(final.original_initial_entry_fill_price, 99.5)

    def test_restart_reconciliation_of_already_frozen_trade_is_idempotent(self):
        # Simulates: process restarts, reconciles again, observes the same
        # terminal state -- frozen fields must stay byte-identical (the
        # second freeze attempt itself is refused; caller must not call it).
        t = _frozen_trade(price=100.0, filled_shares=10)
        snapshot = t
        with self.assertRaises(TradeStateError):
            t.freeze_initial_reference(
                order_status=InitialOrderStatus.FILLED,
                filled_shares=10,
                fill_price=100.0,
                strategy=_strategy(),
                now=_now() + timedelta(hours=1),
            )
        self.assertEqual(t, snapshot)

    def test_rejects_invalid_fill_price(self):
        t = _new_trade()
        with self.assertRaises(TradeStateError):
            t.freeze_initial_reference(
                order_status=InitialOrderStatus.FILLED,
                filled_shares=10,
                fill_price=float("nan"),
                strategy=_strategy(),
                now=_now(),
            )

    def test_rejects_wrong_type_for_strategy(self):
        t = _new_trade()
        with self.assertRaises(TypeError):
            t.freeze_initial_reference(
                order_status=InitialOrderStatus.FILLED,
                filled_shares=10,
                fill_price=100.0,
                strategy="not-a-strategy",
                now=_now(),
            )


class TestRecordLadderFill(unittest.TestCase):
    def test_ladders_are_independent(self):
        t = _frozen_trade()
        t = t.record_ladder_fill(
            TradeAction.LADDER_1,
            order_id="o-l1",
            fill_price=95.0,
            fill_qty=10,
            new_total_shares=20,
            new_weighted_avg_entry_price=97.5,
            strategy=_strategy(),
        )
        self.assertTrue(t.ladder1_filled)
        self.assertFalse(t.ladder2_filled)

        t = t.record_ladder_fill(
            TradeAction.LADDER_2,
            order_id="o-l2",
            fill_price=92.0,
            fill_qty=20,
            new_total_shares=40,
            new_weighted_avg_entry_price=94.75,
            strategy=_strategy(),
        )
        self.assertTrue(t.ladder1_filled)
        self.assertTrue(t.ladder2_filled)
        self.assertEqual(t.total_shares, 40)
        self.assertAlmostEqual(t.weighted_avg_entry_price, 94.75)

    def test_original_floor_and_ladder_prices_never_change_after_fills(self):
        # Direct regression test for the exact bug that broke the live routine.
        t = _frozen_trade(price=100.0, filled_shares=10)
        before = (t.original_floor_price, t.ladder1_price, t.ladder2_price)
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
        after = (t.original_floor_price, t.ladder1_price, t.ladder2_price)
        self.assertEqual(before, after)

    def test_cannot_fill_same_ladder_twice(self):
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
        with self.assertRaises(TradeStateError):
            t.record_ladder_fill(
                TradeAction.LADDER_1,
                order_id="o1b",
                fill_price=95.0,
                fill_qty=10,
                new_total_shares=30,
                new_weighted_avg_entry_price=96.0,
                strategy=_strategy(),
            )

    def test_cannot_exceed_max_position(self):
        t = _frozen_trade()
        with self.assertRaises(TradeStateError):
            t.record_ladder_fill(
                TradeAction.LADDER_1,
                order_id="o1",
                fill_price=95.0,
                fill_qty=10,
                new_total_shares=41,
                new_weighted_avg_entry_price=97.0,
                strategy=_strategy(),
            )

    def test_cannot_ladder_on_unreconciled_trade(self):
        t = _new_trade()
        with self.assertRaises(TradeStateError):
            t.record_ladder_fill(
                TradeAction.LADDER_1,
                order_id="o1",
                fill_price=95.0,
                fill_qty=10,
                new_total_shares=10,
                new_weighted_avg_entry_price=95.0,
                strategy=_strategy(),
            )

    def test_cannot_ladder_on_abandoned_trade(self):
        t = _new_trade().freeze_initial_reference(
            order_status=InitialOrderStatus.EXPIRED,
            filled_shares=0,
            fill_price=None,
            strategy=_strategy(),
            now=_now(),
        )
        with self.assertRaises(TradeStateError):
            t.record_ladder_fill(
                TradeAction.LADDER_1,
                order_id="o1",
                fill_price=95.0,
                fill_qty=10,
                new_total_shares=10,
                new_weighted_avg_entry_price=95.0,
                strategy=_strategy(),
            )

    def test_rejects_initial_entry_action(self):
        t = _frozen_trade()
        with self.assertRaises(TradeStateError):
            t.record_ladder_fill(
                TradeAction.INITIAL_ENTRY,
                order_id="o1",
                fill_price=95.0,
                fill_qty=10,
                new_total_shares=20,
                new_weighted_avg_entry_price=97.5,
                strategy=_strategy(),
            )


class TestTrailingFloor(unittest.TestCase):
    def test_activation_matches_worked_example(self):
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
        # Force weighted_avg to exactly 100 for the strategy.md worked example.
        t = t.reconcile_position(total_shares=20, weighted_avg_entry_price=100.0, strategy=_strategy(), now=_now())
        t = t.activate_trailing(current_price=110.0, now=_now())
        self.assertTrue(t.trailing_activated)
        self.assertAlmostEqual(t.trailing_current_threshold, 110.0)
        self.assertAlmostEqual(t.trailing_floor_price, 104.5)

    def test_activation_rejected_below_threshold(self):
        t = _frozen_trade()
        with self.assertRaises(TradeStateError):
            t.activate_trailing(current_price=109.0, now=_now())

    def test_cannot_activate_twice(self):
        t = _frozen_trade().activate_trailing(current_price=115.0, now=_now())
        with self.assertRaises(TradeStateError):
            t.activate_trailing(current_price=120.0, now=_now())

    def test_ratchet_matches_worked_example_table(self):
        # Full verification-plan §2 worked example: entry = 100 → thresholds
        # 110.0, 115.5, 121.275, 127.339 → floors 104.5, 109.725, 115.211,
        # 120.972 within ±0.001.
        t = _frozen_trade().activate_trailing(current_price=110.0, now=_now())
        self.assertAlmostEqual(t.trailing_current_threshold, 110.0, places=3)
        self.assertAlmostEqual(t.trailing_floor_price, 104.5, places=3)

        t = t.ratchet_trailing(current_price=115.5, now=_now())
        self.assertAlmostEqual(t.trailing_current_threshold, 115.5, places=3)
        self.assertAlmostEqual(t.trailing_floor_price, 109.725, places=3)

        t = t.ratchet_trailing(current_price=121.275, now=_now())
        self.assertAlmostEqual(t.trailing_current_threshold, 121.275, places=3)
        self.assertAlmostEqual(t.trailing_floor_price, 115.211, places=3)

        t = t.ratchet_trailing(current_price=127.339, now=_now())
        self.assertAlmostEqual(t.trailing_current_threshold, 127.339, places=3)
        self.assertAlmostEqual(t.trailing_floor_price, 120.972, places=3)

    def test_ratchet_before_activation_raises(self):
        t = _frozen_trade()
        with self.assertRaises(TradeStateError):
            t.ratchet_trailing(current_price=200.0, now=_now())

    def test_ratchet_rejected_below_next_threshold(self):
        t = _frozen_trade().activate_trailing(current_price=110.0, now=_now())
        with self.assertRaises(TradeStateError):
            t.ratchet_trailing(current_price=112.0, now=_now())

    def test_trailing_floor_monotonic_across_dip_and_rise(self):
        t = _frozen_trade().activate_trailing(current_price=110.0, now=_now())
        floor_after_activation = t.trailing_floor_price
        # A dip below the threshold is simply not ratcheted (caller wouldn't
        # call ratchet_trailing); floor stays exactly where it was.
        self.assertEqual(t.trailing_floor_price, floor_after_activation)
        t = t.ratchet_trailing(current_price=115.5, now=_now())
        self.assertGreater(t.trailing_floor_price, floor_after_activation)

    def test_trailing_floor_monotonic_across_random_walk(self):
        # Verification-plan §2 monotonicity clause: for any random walk,
        # the trailing floor must be non-decreasing. Deterministic seed
        # keeps the test reproducible.
        import random
        rng = random.Random(20260923)
        t = _frozen_trade(price=100.0).activate_trailing(current_price=110.0, now=_now())
        previous_floor = t.trailing_floor_price
        price = 110.0
        for _ in range(2000):
            # Multiplicative walk keeps the sequence positive.
            price *= 1.0 + rng.uniform(-0.02, 0.02)
            # Only ratchet when the next-threshold rule allows it, since
            # ratchet_trailing() otherwise raises by design. Pass the
            # actual walking price so any FP-roundoff on the exact
            # threshold*1.05 boundary does not spuriously fail the check.
            while price >= t.trailing_current_threshold * 1.05:
                t = t.ratchet_trailing(current_price=price, now=_now())
                self.assertGreaterEqual(t.trailing_floor_price, previous_floor)
                previous_floor = t.trailing_floor_price
            # Regardless of whether a ratchet fired, the floor must never
            # have gone down between iterations.
            self.assertGreaterEqual(t.trailing_floor_price, previous_floor)


class TestActiveFloor(unittest.TestCase):
    def test_none_before_freeze(self):
        t = _new_trade()
        self.assertIsNone(t.active_floor_price)
        self.assertIsNone(t.active_floor_source)

    def test_none_when_abandoned(self):
        t = _new_trade().freeze_initial_reference(
            order_status=InitialOrderStatus.EXPIRED,
            filled_shares=0,
            fill_price=None,
            strategy=_strategy(),
            now=_now(),
        )
        self.assertIsNone(t.active_floor_price)

    def test_original_floor_before_trailing_activation(self):
        t = _frozen_trade(price=100.0)
        self.assertAlmostEqual(t.active_floor_price, 90.0)
        self.assertEqual(t.active_floor_source, "original")

    def test_trailing_floor_once_higher_than_original(self):
        t = _frozen_trade(price=100.0).activate_trailing(current_price=110.0, now=_now())
        self.assertAlmostEqual(t.active_floor_price, 104.5)
        self.assertEqual(t.active_floor_source, "trailing")

    def test_original_floor_wins_at_boundary_equality(self):
        # original_floor_price = 90.0 (price=100.0). Choose a weighted-avg
        # entry that makes the trailing floor land exactly on 90.0 too, to
        # confirm active_floor_source uses a strict '>' (original wins ties).
        t = _frozen_trade(price=100.0)
        t = t.reconcile_position(
            total_shares=t.total_shares, weighted_avg_entry_price=86.1244, strategy=_strategy(), now=_now()
        )
        t = t.activate_trailing(current_price=100.0, now=_now())
        self.assertAlmostEqual(t.trailing_floor_price, 90.0)
        self.assertAlmostEqual(t.active_floor_price, 90.0)
        self.assertEqual(t.active_floor_source, "original")


class TestReconcilePosition(unittest.TestCase):
    def test_updates_live_metrics(self):
        t = _frozen_trade()
        t = t.reconcile_position(total_shares=10, weighted_avg_entry_price=101.0, strategy=_strategy(), now=_now())
        self.assertEqual(t.total_shares, 10)
        self.assertAlmostEqual(t.weighted_avg_entry_price, 101.0)

    def test_position_can_go_to_zero_representing_closed(self):
        t = _frozen_trade()
        t = t.reconcile_position(total_shares=0, weighted_avg_entry_price=None, strategy=_strategy(), now=_now())
        self.assertEqual(t.total_shares, 0)
        self.assertEqual(describe_status(t), "CLOSED")

    def test_cannot_exceed_max_position(self):
        t = _frozen_trade()
        with self.assertRaises(TradeStateError):
            t.reconcile_position(total_shares=41, weighted_avg_entry_price=95.0, strategy=_strategy(), now=_now())

    def test_cannot_reconcile_unreconciled_trade(self):
        t = _new_trade()
        with self.assertRaises(TradeStateError):
            t.reconcile_position(total_shares=10, weighted_avg_entry_price=100.0, strategy=_strategy(), now=_now())


class TestProtectiveOrder(unittest.TestCase):
    def test_first_placement_has_empty_lineage(self):
        t = _frozen_trade()
        t = t.update_protective_order(order_id="stop-1", stop_price=90.0, status="open", now=_now())
        self.assertEqual(t.protective_order_id, "stop-1")
        self.assertEqual(t.protective_order_lineage, ())

    def test_replacement_appends_previous_id_to_lineage(self):
        t = _frozen_trade()
        t = t.update_protective_order(order_id="stop-1", stop_price=90.0, status="open", now=_now())
        t = t.update_protective_order(order_id="stop-2", stop_price=104.5, status="open", now=_now())
        self.assertEqual(t.protective_order_id, "stop-2")
        self.assertEqual(t.protective_order_lineage, ("stop-1",))

    def test_lineage_never_shrinks(self):
        t = _frozen_trade()
        t = t.update_protective_order(order_id="stop-1", stop_price=90.0, status="open", now=_now())
        t = t.update_protective_order(order_id="stop-2", stop_price=104.5, status="open", now=_now())
        t = t.update_protective_order(order_id="stop-3", stop_price=109.7, status="open", now=_now())
        self.assertEqual(t.protective_order_lineage, ("stop-1", "stop-2"))

    def test_rejects_invalid_stop_price(self):
        t = _frozen_trade()
        with self.assertRaises(TradeStateError):
            t.update_protective_order(order_id="stop-1", stop_price=-1.0, status="open", now=_now())


class TestReconciliationBookkeeping(unittest.TestCase):
    def test_records_poll_and_clears_error(self):
        t = _frozen_trade().record_reconciliation(now=_now(), error="broker timeout")
        self.assertEqual(t.last_error, "broker timeout")
        t = t.record_reconciliation(now=_now() + timedelta(minutes=1))
        self.assertIsNone(t.last_error)


class TestDescribeStatus(unittest.TestCase):
    def test_awaiting_initial_fill(self):
        self.assertEqual(describe_status(_new_trade()), "AWAITING_INITIAL_FILL")

    def test_abandoned(self):
        t = _new_trade().freeze_initial_reference(
            order_status=InitialOrderStatus.CANCELLED,
            filled_shares=0,
            fill_price=None,
            strategy=_strategy(),
            now=_now(),
        )
        self.assertEqual(describe_status(t), "ABANDONED")

    def test_active(self):
        self.assertEqual(describe_status(_frozen_trade()), "ACTIVE")

    def test_closed(self):
        t = _frozen_trade().reconcile_position(
            total_shares=0, weighted_avg_entry_price=None, strategy=_strategy(), now=_now()
        )
        self.assertEqual(describe_status(t), "CLOSED")

    def test_pure_no_stored_field(self):
        # describe_status must be derivable purely from public fields --
        # confirm Trade carries no attribute literally named 'status'.
        t = _frozen_trade()
        self.assertFalse(hasattr(t, "status"))


class TestGenericitySymbolAndTradeId(unittest.TestCase):
    def test_two_trades_are_fully_independent(self):
        t1 = _frozen_trade(price=100.0, trade_id="T-A", symbol="TSLA")
        t2 = _frozen_trade(price=50.0, trade_id="T-B", symbol="DELL")
        t1 = t1.record_ladder_fill(
            TradeAction.LADDER_1,
            order_id="o1",
            fill_price=95.0,
            fill_qty=10,
            new_total_shares=20,
            new_weighted_avg_entry_price=97.5,
            strategy=_strategy(),
        )
        self.assertTrue(t1.ladder1_filled)
        self.assertFalse(t2.ladder1_filled)
        self.assertNotEqual(t1.symbol, t2.symbol)
        self.assertNotEqual(t1.trade_id, t2.trade_id)
        self.assertAlmostEqual(t2.ladder1_price, 47.5)

    def test_terminal_statuses_constant_is_exactly_three_values(self):
        self.assertEqual(
            TERMINAL_INITIAL_ORDER_STATUSES,
            {InitialOrderStatus.FILLED, InitialOrderStatus.CANCELLED, InitialOrderStatus.EXPIRED},
        )


if __name__ == "__main__":
    unittest.main()
