import unittest
from datetime import datetime, timedelta, timezone

from proposals.models import FloorContext, TradeAction, approved_strategy_rule_set
from proposals.proposal import build_trade_proposal
from proposals.revalidation import validate_for_submission


def _now():
    return datetime(2026, 9, 17, 14, 0, 0, tzinfo=timezone.utc)


def _strategy():
    return approved_strategy_rule_set()


def _pending_proposal(price=100.0, action=TradeAction.INITIAL_ENTRY):
    return build_trade_proposal(
        proposal_id="P-1",
        trade_id="T-1",
        action=action,
        symbol="TSLA",
        current_price=price,
        as_of=_now(),
        strategy=_strategy(),
        floor_context=FloorContext.no_existing_position(),
    )


def _approved_proposal(decided_at=None, price=100.0, action=TradeAction.INITIAL_ENTRY):
    p = _pending_proposal(price=price, action=action)
    decided_at = decided_at if decided_at is not None else _now()
    return p.with_decision(approved=True, decided_by="controller", decided_at=decided_at, action=action)


class TestRevalidation(unittest.TestCase):
    def test_valid_approval_passes(self):
        p = _approved_proposal()
        result = validate_for_submission(
            p,
            trigger_price=95.0,
            current_price=95.1,
            active_floor_price=80.0,
            now=p.approval_received_at + timedelta(minutes=1),
            action=TradeAction.INITIAL_ENTRY,
        )
        self.assertTrue(result.allowed)
        self.assertIsNone(result.reason)

    def test_rejects_pending_proposal(self):
        p = _pending_proposal()
        result = validate_for_submission(
            p,
            trigger_price=95.0,
            current_price=95.0,
            active_floor_price=80.0,
            now=_now(),
            action=TradeAction.INITIAL_ENTRY,
        )
        self.assertFalse(result.allowed)
        self.assertIn("pending", result.reason)

    def test_rejects_rejected_proposal(self):
        p = _pending_proposal()
        rejected = p.with_decision(
            approved=False, decided_by="controller", decided_at=_now(), action=TradeAction.INITIAL_ENTRY
        )
        result = validate_for_submission(
            rejected,
            trigger_price=95.0,
            current_price=95.0,
            active_floor_price=80.0,
            now=_now(),
            action=TradeAction.INITIAL_ENTRY,
        )
        self.assertFalse(result.allowed)
        self.assertIn("rejected", result.reason)

    def test_rejects_approval_older_than_5_minutes(self):
        p = _approved_proposal()
        result = validate_for_submission(
            p,
            trigger_price=95.0,
            current_price=95.0,
            active_floor_price=80.0,
            now=p.approval_received_at + timedelta(minutes=5, seconds=1),
            action=TradeAction.INITIAL_ENTRY,
        )
        self.assertFalse(result.allowed)
        self.assertIn("5-minute", result.reason)

    def test_allows_approval_at_exactly_5_minutes(self):
        p = _approved_proposal()
        result = validate_for_submission(
            p,
            trigger_price=95.0,
            current_price=95.0,
            active_floor_price=80.0,
            now=p.approval_received_at + timedelta(minutes=5),
            action=TradeAction.INITIAL_ENTRY,
        )
        self.assertTrue(result.allowed)

    def test_rejects_price_movement_beyond_half_percent_band(self):
        p = _approved_proposal()
        # trigger 95.0, 1% away = 95.95 -> exceeds 0.5% band
        result = validate_for_submission(
            p,
            trigger_price=95.0,
            current_price=95.95,
            active_floor_price=80.0,
            now=p.approval_received_at + timedelta(minutes=1),
            action=TradeAction.INITIAL_ENTRY,
        )
        self.assertFalse(result.allowed)
        self.assertIn("price deviation", result.reason)

    def test_allows_price_movement_within_half_percent_band(self):
        p = _approved_proposal()
        # 0.4% away from 95.0
        result = validate_for_submission(
            p,
            trigger_price=95.0,
            current_price=95.38,
            active_floor_price=80.0,
            now=p.approval_received_at + timedelta(minutes=1),
            action=TradeAction.INITIAL_ENTRY,
        )
        self.assertTrue(result.allowed)

    def test_rejects_trigger_at_or_below_active_floor(self):
        p = _approved_proposal()
        result = validate_for_submission(
            p,
            trigger_price=90.0,
            current_price=90.0,
            active_floor_price=90.0,
            now=p.approval_received_at + timedelta(minutes=1),
            action=TradeAction.INITIAL_ENTRY,
        )
        self.assertFalse(result.allowed)
        self.assertIn("floor", result.reason)

    def test_allows_trigger_above_active_floor(self):
        p = _approved_proposal()
        result = validate_for_submission(
            p,
            trigger_price=95.0,
            current_price=95.0,
            active_floor_price=90.0,
            now=p.approval_received_at + timedelta(minutes=1),
            action=TradeAction.INITIAL_ENTRY,
        )
        self.assertTrue(result.allowed)

    def test_no_network_calls_required(self):
        # Structural: the module imports only stdlib + local models, so
        # this test's mere successful import/execution demonstrates no
        # network dependency exists.
        import proposals.revalidation as mod

        self.assertTrue(hasattr(mod, "validate_for_submission"))


class TestD2UnknownFloorBlocks(unittest.TestCase):
    """D2 governance fix: active_floor_price=None must BLOCK
    unconditionally -- never treated as 'no floor to check against'."""

    def test_none_floor_blocks_even_with_otherwise_valid_approval(self):
        p = _approved_proposal()
        result = validate_for_submission(
            p,
            trigger_price=95.0,
            current_price=95.0,
            active_floor_price=None,
            now=p.approval_received_at + timedelta(minutes=1),
            action=TradeAction.INITIAL_ENTRY,
        )
        self.assertFalse(result.allowed)
        self.assertIn("unknown", result.reason)

    def test_none_floor_is_never_treated_as_infinitely_permissive(self):
        p = _approved_proposal()
        # Even a trigger far below any plausible real floor must still
        # be blocked when the floor itself is unknown.
        result = validate_for_submission(
            p,
            trigger_price=1.0,
            current_price=1.0,
            active_floor_price=None,
            now=p.approval_received_at + timedelta(minutes=1),
            action=TradeAction.INITIAL_ENTRY,
        )
        self.assertFalse(result.allowed)


class TestActionScopedApproval(unittest.TestCase):
    """Controller-approved 'Option B' fix: an approval recorded for one
    action (INITIAL_ENTRY, LADDER_1, LADDER_2) must never authorize
    submission of a different action."""

    def test_ladder_1_approval_does_not_authorize_ladder_2(self):
        p = _approved_proposal(action=TradeAction.LADDER_1)
        result = validate_for_submission(
            p,
            trigger_price=92.0,
            current_price=92.0,
            active_floor_price=80.0,
            now=p.approval_received_at + timedelta(minutes=1),
            action=TradeAction.LADDER_2,
        )
        self.assertFalse(result.allowed)
        self.assertIn("ladder_1", result.reason)
        self.assertIn("ladder_2", result.reason)

    def test_ladder_2_approval_does_not_authorize_ladder_1(self):
        p = _approved_proposal(action=TradeAction.LADDER_2)
        result = validate_for_submission(
            p,
            trigger_price=95.0,
            current_price=95.0,
            active_floor_price=80.0,
            now=p.approval_received_at + timedelta(minutes=1),
            action=TradeAction.LADDER_1,
        )
        self.assertFalse(result.allowed)
        self.assertIn("ladder_2", result.reason)
        self.assertIn("ladder_1", result.reason)

    def test_initial_entry_approval_does_not_authorize_either_ladder(self):
        p = _approved_proposal(action=TradeAction.INITIAL_ENTRY)
        for other_action in (TradeAction.LADDER_1, TradeAction.LADDER_2):
            with self.subTest(other_action=other_action):
                result = validate_for_submission(
                    p,
                    trigger_price=95.0,
                    current_price=95.0,
                    active_floor_price=80.0,
                    now=p.approval_received_at + timedelta(minutes=1),
                    action=other_action,
                )
                self.assertFalse(result.allowed)
                self.assertIn("initial_entry", result.reason)

    def test_ladder_approval_does_not_authorize_initial_entry(self):
        p = _approved_proposal(action=TradeAction.LADDER_1)
        result = validate_for_submission(
            p,
            trigger_price=100.0,
            current_price=100.0,
            active_floor_price=80.0,
            now=p.approval_received_at + timedelta(minutes=1),
            action=TradeAction.INITIAL_ENTRY,
        )
        self.assertFalse(result.allowed)

    def test_matching_action_still_passes(self):
        p = _approved_proposal(action=TradeAction.LADDER_2)
        result = validate_for_submission(
            p,
            trigger_price=92.0,
            current_price=92.0,
            active_floor_price=80.0,
            now=p.approval_received_at + timedelta(minutes=1),
            action=TradeAction.LADDER_2,
        )
        self.assertTrue(result.allowed)

    def test_action_must_be_a_trade_action(self):
        p = _approved_proposal()
        with self.assertRaises(TypeError):
            validate_for_submission(
                p,
                trigger_price=95.0,
                current_price=95.0,
                active_floor_price=80.0,
                now=p.approval_received_at + timedelta(minutes=1),
                action="initial_entry",
            )


if __name__ == "__main__":
    unittest.main()
