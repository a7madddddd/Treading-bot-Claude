import sys
import unittest
from datetime import datetime, timedelta, timezone

from execution.models import (
    CREATED,
    SUBMITTED_UNKNOWN,
    SUBMITTING,
    OrderExecution,
    OrderExecutionError,
)


def _now():
    return datetime(2026, 9, 17, 14, 0, 0, tzinfo=timezone.utc)


def _new_execution(**overrides):
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


class TestConstruction(unittest.TestCase):
    def test_valid_construction_defaults(self):
        ex = _new_execution()
        self.assertEqual(ex.proposal_id, "P-1")
        self.assertEqual(ex.execution_id, "E-1")
        self.assertEqual(ex.client_order_id, "C-1")
        self.assertEqual(ex.requested_qty, 10)
        self.assertEqual(ex.status, CREATED)
        self.assertEqual(ex.filled_qty, 0)
        self.assertIsNone(ex.filled_avg_price)
        self.assertIsNone(ex.broker_order_id)
        self.assertIsNone(ex.last_broker_poll_at)
        self.assertFalse(ex.is_broker_terminal)

    def test_explicit_ids_are_required_and_accepted(self):
        ex = _new_execution(execution_id="E-DISTINCT", client_order_id="C-DISTINCT")
        self.assertEqual(ex.execution_id, "E-DISTINCT")
        self.assertEqual(ex.client_order_id, "C-DISTINCT")

    def test_distinct_instances_may_share_no_id_by_caller_choice(self):
        # OrderExecution performs no generation and no uniqueness
        # checking itself (that is a repository-level concern) -- this
        # only confirms two independently constructed instances with
        # caller-chosen distinct ids are in fact distinct.
        a = _new_execution(execution_id="E-A", client_order_id="C-A")
        b = _new_execution(execution_id="E-B", client_order_id="C-B")
        self.assertNotEqual(a.execution_id, b.execution_id)
        self.assertNotEqual(a.client_order_id, b.client_order_id)

    def test_missing_execution_id_rejected(self):
        with self.assertRaises(TypeError):
            OrderExecution(proposal_id="P-1", client_order_id="C-1", requested_qty=10, created_at=_now())

    def test_missing_client_order_id_rejected(self):
        with self.assertRaises(TypeError):
            OrderExecution(proposal_id="P-1", execution_id="E-1", requested_qty=10, created_at=_now())

    def test_missing_proposal_id_rejected(self):
        with self.assertRaises(TypeError):
            OrderExecution(execution_id="E-1", client_order_id="C-1", requested_qty=10, created_at=_now())

    def test_empty_proposal_id_rejected(self):
        with self.assertRaises(OrderExecutionError):
            _new_execution(proposal_id="")

    def test_empty_execution_id_rejected(self):
        with self.assertRaises(OrderExecutionError):
            _new_execution(execution_id="")

    def test_empty_client_order_id_rejected(self):
        with self.assertRaises(OrderExecutionError):
            _new_execution(client_order_id="")

    def test_non_positive_requested_qty_rejected(self):
        with self.assertRaises(OrderExecutionError):
            _new_execution(requested_qty=0)
        with self.assertRaises(OrderExecutionError):
            _new_execution(requested_qty=-5)

    def test_negative_filled_qty_rejected(self):
        with self.assertRaises(OrderExecutionError):
            _new_execution(filled_qty=-1)

    def test_filled_qty_exceeding_requested_rejected(self):
        with self.assertRaises(OrderExecutionError):
            _new_execution(requested_qty=10, filled_qty=11)

    def test_filled_qty_positive_requires_valid_price(self):
        with self.assertRaises(OrderExecutionError):
            _new_execution(
                requested_qty=10,
                filled_qty=5,
                filled_avg_price=None,
                status="filled",
                is_broker_terminal=True,
                broker_order_id="B-1",
                last_broker_poll_at=_now(),
            )
        with self.assertRaises(OrderExecutionError):
            _new_execution(
                requested_qty=10,
                filled_qty=5,
                filled_avg_price=0.0,
                status="filled",
                is_broker_terminal=True,
                broker_order_id="B-1",
                last_broker_poll_at=_now(),
            )
        with self.assertRaises(OrderExecutionError):
            _new_execution(
                requested_qty=10,
                filled_qty=5,
                filled_avg_price=float("nan"),
                status="filled",
                is_broker_terminal=True,
                broker_order_id="B-1",
                last_broker_poll_at=_now(),
            )

    def test_zero_fill_with_price_rejected(self):
        with self.assertRaises(OrderExecutionError):
            _new_execution(filled_qty=0, filled_avg_price=95.0)

    def test_empty_status_rejected(self):
        with self.assertRaises(OrderExecutionError):
            _new_execution(status="")

    def test_local_status_with_broker_order_id_rejected(self):
        with self.assertRaises(OrderExecutionError):
            _new_execution(status=CREATED, broker_order_id="B-1")

    def test_local_status_with_terminal_flag_rejected(self):
        with self.assertRaises(OrderExecutionError):
            _new_execution(status=SUBMITTING, is_broker_terminal=True)

    def test_local_status_with_last_broker_poll_at_rejected(self):
        with self.assertRaises(OrderExecutionError):
            _new_execution(status=SUBMITTING, last_broker_poll_at=_now())

    def test_broker_status_without_broker_order_id_rejected(self):
        with self.assertRaises(OrderExecutionError):
            _new_execution(status="accepted", last_broker_poll_at=_now())

    def test_broker_status_without_last_broker_poll_at_rejected(self):
        with self.assertRaises(OrderExecutionError):
            _new_execution(status="accepted", broker_order_id="B-1")

    def test_last_broker_poll_at_before_created_at_rejected(self):
        with self.assertRaises(OrderExecutionError):
            _new_execution(
                status="accepted",
                broker_order_id="B-1",
                last_broker_poll_at=_now() - timedelta(minutes=1),
            )


class TestCreatedAndSubmitting(unittest.TestCase):
    def test_created_is_default_status(self):
        self.assertEqual(_new_execution().status, CREATED)

    def test_start_submission_transitions_to_submitting(self):
        ex = _new_execution()
        submitting = ex.start_submission(now=_now())
        self.assertEqual(submitting.status, SUBMITTING)
        # original untouched
        self.assertEqual(ex.status, CREATED)

    def test_start_submission_only_from_created(self):
        ex = _new_execution().start_submission(now=_now())
        with self.assertRaises(OrderExecutionError):
            ex.start_submission(now=_now())


class TestSubmittedUnknown(unittest.TestCase):
    def test_mark_submission_unknown_only_from_submitting(self):
        ex = _new_execution()
        with self.assertRaises(OrderExecutionError):
            ex.mark_submission_unknown(now=_now())

    def test_mark_submission_unknown_transitions_correctly(self):
        ex = _new_execution().start_submission(now=_now())
        unknown = ex.mark_submission_unknown(now=_now())
        self.assertEqual(unknown.status, SUBMITTED_UNKNOWN)
        self.assertIsNone(unknown.broker_order_id)

    def test_unknown_outcome_cannot_become_success_without_real_response(self):
        # The only way out of SUBMITTED_UNKNOWN is record_broker_response()
        # with an actual, non-local status string -- there is no method
        # that promotes SUBMITTED_UNKNOWN to a terminal/success state
        # directly.
        ex = _new_execution().start_submission(now=_now()).mark_submission_unknown(now=_now())
        with self.assertRaises(OrderExecutionError):
            ex.record_broker_response(
                broker_order_id="B-1", status=SUBMITTED_UNKNOWN, is_terminal=True, now=_now()
            )

    def test_unknown_outcome_cannot_become_rejection_without_real_response(self):
        ex = _new_execution().start_submission(now=_now()).mark_submission_unknown(now=_now())
        with self.assertRaises(OrderExecutionError):
            ex.record_broker_response(
                broker_order_id="", status="rejected", is_terminal=True, now=_now()
            )


class TestBrokerResponse(unittest.TestCase):
    def test_record_broker_response_from_submitting(self):
        ex = _new_execution().start_submission(now=_now())
        acked = ex.record_broker_response(
            broker_order_id="B-1", status="accepted", is_terminal=False, now=_now()
        )
        self.assertEqual(acked.broker_order_id, "B-1")
        self.assertEqual(acked.status, "accepted")
        self.assertFalse(acked.is_broker_terminal)
        self.assertEqual(acked.last_broker_poll_at, _now())

    def test_record_broker_response_from_submitted_unknown(self):
        ex = _new_execution().start_submission(now=_now()).mark_submission_unknown(now=_now())
        acked = ex.record_broker_response(
            broker_order_id="B-1", status="accepted", is_terminal=False, now=_now()
        )
        self.assertEqual(acked.broker_order_id, "B-1")

    def test_record_broker_response_requires_submitting_or_unknown(self):
        ex = _new_execution()
        with self.assertRaises(OrderExecutionError):
            ex.record_broker_response(
                broker_order_id="B-1", status="accepted", is_terminal=False, now=_now()
            )

    def test_record_broker_response_with_terminal_status_immediate_fill(self):
        ex = _new_execution(requested_qty=10).start_submission(now=_now())
        filled = ex.record_broker_response(
            broker_order_id="B-1",
            status="filled",
            is_terminal=True,
            now=_now(),
            filled_qty=10,
            filled_avg_price=100.0,
        )
        self.assertTrue(filled.is_broker_terminal)
        self.assertEqual(filled.filled_qty, 10)
        self.assertEqual(filled.filled_avg_price, 100.0)

    def test_record_broker_response_with_open_status_leaves_zero_fill(self):
        ex = _new_execution().start_submission(now=_now())
        acked = ex.record_broker_response(
            broker_order_id="B-1", status="accepted", is_terminal=False, now=_now()
        )
        self.assertEqual(acked.filled_qty, 0)
        self.assertIsNone(acked.filled_avg_price)

    def test_broker_order_id_immutable_second_call_refused(self):
        ex = (
            _new_execution()
            .start_submission(now=_now())
            .record_broker_response(broker_order_id="B-1", status="accepted", is_terminal=False, now=_now())
        )
        # status is no longer SUBMITTING/SUBMITTED_UNKNOWN, so a second
        # record_broker_response() call (e.g. attempting to change
        # broker_order_id) is structurally refused.
        with self.assertRaises(OrderExecutionError):
            ex.record_broker_response(
                broker_order_id="B-2", status="accepted", is_terminal=False, now=_now()
            )

    def test_record_broker_response_rejects_local_status(self):
        ex = _new_execution().start_submission(now=_now())
        with self.assertRaises(OrderExecutionError):
            ex.record_broker_response(
                broker_order_id="B-1", status=SUBMITTING, is_terminal=False, now=_now()
            )


class TestFillUpdate(unittest.TestCase):
    def _acknowledged(self):
        return (
            _new_execution(requested_qty=10)
            .start_submission(now=_now())
            .record_broker_response(broker_order_id="B-1", status="accepted", is_terminal=False, now=_now())
        )

    def test_cumulative_fill_increase(self):
        ex = self._acknowledged()
        later = _now() + timedelta(minutes=1)
        partial = ex.record_fill_update(
            filled_qty=4, filled_avg_price=100.0, status="partially_filled", is_terminal=False, now=later
        )
        self.assertEqual(partial.filled_qty, 4)
        self.assertEqual(partial.filled_avg_price, 100.0)
        self.assertEqual(partial.status, "partially_filled")
        self.assertEqual(partial.last_broker_poll_at, later)

        full = partial.record_fill_update(
            filled_qty=10,
            filled_avg_price=100.5,
            status="filled",
            is_terminal=True,
            now=later + timedelta(minutes=1),
        )
        self.assertEqual(full.filled_qty, 10)
        self.assertTrue(full.is_broker_terminal)

    def test_fill_update_requires_broker_response_first(self):
        ex = _new_execution().start_submission(now=_now())
        with self.assertRaises(OrderExecutionError):
            ex.record_fill_update(
                filled_qty=5, filled_avg_price=100.0, status="partially_filled", is_terminal=False, now=_now()
            )

    def test_cumulative_fill_cannot_decrease(self):
        ex = self._acknowledged().record_fill_update(
            filled_qty=6, filled_avg_price=100.0, status="partially_filled", is_terminal=False, now=_now()
        )
        with self.assertRaises(OrderExecutionError):
            ex.record_fill_update(
                filled_qty=5, filled_avg_price=100.0, status="partially_filled", is_terminal=False, now=_now()
            )

    def test_cumulative_fill_cannot_exceed_requested(self):
        ex = self._acknowledged()
        with self.assertRaises(OrderExecutionError):
            ex.record_fill_update(
                filled_qty=11, filled_avg_price=100.0, status="filled", is_terminal=True, now=_now()
            )

    def test_invalid_filled_avg_price_rejected(self):
        ex = self._acknowledged()
        with self.assertRaises(OrderExecutionError):
            ex.record_fill_update(
                filled_qty=5, filled_avg_price=None, status="partially_filled", is_terminal=False, now=_now()
            )
        with self.assertRaises(OrderExecutionError):
            ex.record_fill_update(
                filled_qty=5, filled_avg_price=-1.0, status="partially_filled", is_terminal=False, now=_now()
            )

    def test_terminal_state_cannot_move_backward(self):
        ex = self._acknowledged().record_fill_update(
            filled_qty=10, filled_avg_price=100.0, status="filled", is_terminal=True, now=_now()
        )
        with self.assertRaises(OrderExecutionError):
            ex.record_fill_update(
                filled_qty=10,
                filled_avg_price=100.0,
                status="partially_filled",
                is_terminal=False,
                now=_now() + timedelta(minutes=1),
            )

    def test_terminal_state_refuses_any_further_update(self):
        ex = self._acknowledged().record_fill_update(
            filled_qty=0, filled_avg_price=None, status="rejected", is_terminal=True, now=_now()
        )
        with self.assertRaises(OrderExecutionError):
            ex.record_fill_update(
                filled_qty=0, filled_avg_price=None, status="rejected", is_terminal=True, now=_now()
            )

    def test_fill_update_rejects_local_status(self):
        ex = self._acknowledged()
        with self.assertRaises(OrderExecutionError):
            ex.record_fill_update(
                filled_qty=1, filled_avg_price=100.0, status=CREATED, is_terminal=False, now=_now()
            )


class TestClientOrderIdImmutability(unittest.TestCase):
    def test_ids_unchanged_across_full_lifecycle(self):
        ex = _new_execution(execution_id="E-FIXED", client_order_id="C-FIXED")
        ex = ex.start_submission(now=_now())
        ex = ex.record_broker_response(broker_order_id="B-1", status="accepted", is_terminal=False, now=_now())
        ex = ex.record_fill_update(
            filled_qty=10, filled_avg_price=100.0, status="filled", is_terminal=True, now=_now()
        )
        self.assertEqual(ex.execution_id, "E-FIXED")
        self.assertEqual(ex.client_order_id, "C-FIXED")

    def test_ids_unchanged_through_unknown_path(self):
        ex = _new_execution(execution_id="E-FIXED", client_order_id="C-FIXED")
        ex = ex.start_submission(now=_now())
        ex = ex.mark_submission_unknown(now=_now())
        ex = ex.record_broker_response(broker_order_id="B-1", status="accepted", is_terminal=False, now=_now())
        self.assertEqual(ex.execution_id, "E-FIXED")
        self.assertEqual(ex.client_order_id, "C-FIXED")

    def test_ids_unchanged_across_repeated_fill_updates(self):
        # "Repeated transitions do not change them" -- several
        # consecutive record_fill_update() calls (simulating multiple
        # reconciliation polls) must never alter either id.
        ex = (
            _new_execution(execution_id="E-FIXED", client_order_id="C-FIXED")
            .start_submission(now=_now())
            .record_broker_response(broker_order_id="B-1", status="accepted", is_terminal=False, now=_now())
        )
        for qty in (2, 5, 8):
            ex = ex.record_fill_update(
                filled_qty=qty, filled_avg_price=100.0, status="partially_filled", is_terminal=False, now=_now()
            )
            self.assertEqual(ex.execution_id, "E-FIXED")
            self.assertEqual(ex.client_order_id, "C-FIXED")


class TestTimestamps(unittest.TestCase):
    def test_last_broker_poll_at_updates_on_each_call(self):
        t0 = _now()
        t1 = t0 + timedelta(seconds=30)
        t2 = t0 + timedelta(minutes=2)
        ex = _new_execution(created_at=t0).start_submission(now=t0)
        ex = ex.record_broker_response(broker_order_id="B-1", status="accepted", is_terminal=False, now=t1)
        self.assertEqual(ex.last_broker_poll_at, t1)
        ex = ex.record_fill_update(
            filled_qty=5, filled_avg_price=100.0, status="partially_filled", is_terminal=False, now=t2
        )
        self.assertEqual(ex.last_broker_poll_at, t2)

    def test_created_at_never_changes(self):
        t0 = _now()
        ex = _new_execution(created_at=t0).start_submission(now=t0)
        ex = ex.record_broker_response(
            broker_order_id="B-1", status="accepted", is_terminal=False, now=t0 + timedelta(minutes=5)
        )
        self.assertEqual(ex.created_at, t0)


class TestEqualityAndImmutability(unittest.TestCase):
    def test_transitions_return_new_instances(self):
        ex = _new_execution()
        submitting = ex.start_submission(now=_now())
        self.assertIsNot(ex, submitting)
        self.assertNotEqual(ex, submitting)

    def test_equal_field_values_produce_equal_instances(self):
        t0 = _now()
        a = OrderExecution(
            proposal_id="P-1", trade_id="T-1", requested_qty=10, created_at=t0, execution_id="E-1", client_order_id="C-1"
        )
        b = OrderExecution(
            proposal_id="P-1", trade_id="T-1", requested_qty=10, created_at=t0, execution_id="E-1", client_order_id="C-1"
        )
        self.assertEqual(a, b)

    def test_original_instance_never_mutated_by_transition(self):
        ex = _new_execution()
        before = ex.status
        ex.start_submission(now=_now())
        self.assertEqual(ex.status, before)


class TestNoExternalDependencies(unittest.TestCase):
    def test_module_imports_no_trade_proposal_or_persistence(self):
        module = sys.modules["execution.models"]
        source_modules = {
            getattr(value, "__module__", None)
            for value in vars(module).values()
            if hasattr(value, "__module__")
        }
        for forbidden_prefix in ("trade", "proposals", "persistence", "sqlite3"):
            for mod_name in source_modules:
                if mod_name and mod_name.startswith(forbidden_prefix):
                    self.fail(f"execution.models unexpectedly depends on {mod_name!r}")

    def test_no_alpaca_dependency(self):
        module = sys.modules["execution.models"]
        self.assertNotIn("alpaca", module.__dict__)
        for name in dir(module):
            self.assertNotIn("alpaca", name.lower())


if __name__ == "__main__":
    unittest.main()
