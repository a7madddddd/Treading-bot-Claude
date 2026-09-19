import unittest
from dataclasses import replace
from datetime import datetime, timezone

from proposals.models import (
    ApprovalState,
    FloorContext,
    InvalidFloorContextError,
    StrategyUnavailableError,
    TradeAction,
    approved_strategy_rule_set,
)
from proposals.proposal import build_trade_proposal


def _now():
    return datetime(2026, 9, 17, 14, 0, 0, tzinfo=timezone.utc)


def _strategy():
    return approved_strategy_rule_set()


def _no_position():
    return FloorContext.no_existing_position()


def _build(**overrides):
    kwargs = dict(
        proposal_id="P-1",
        trade_id="T-1",
        action=TradeAction.INITIAL_ENTRY,
        symbol="TSLA",
        current_price=100.0,
        as_of=_now(),
        strategy=_strategy(),
        floor_context=_no_position(),
    )
    kwargs.update(overrides)
    return build_trade_proposal(**kwargs)


class TestBuildTradeProposal(unittest.TestCase):
    def test_uses_frozen_strategy_values_exactly(self):
        p = _build()
        self.assertAlmostEqual(p.proposed_entry, 100.0)
        self.assertAlmostEqual(p.ladder_1_trigger, 95.0)
        self.assertEqual(p.ladder_1_quantity, 10)
        self.assertAlmostEqual(p.ladder_2_trigger, 92.0)
        self.assertEqual(p.ladder_2_quantity, 20)
        self.assertAlmostEqual(p.floor_trigger, 90.0)
        self.assertEqual(p.maximum_position, 40)

    def test_symbol_is_normalized(self):
        p = _build(symbol=" tsla ")
        self.assertEqual(p.symbol, "TSLA")

    def test_candidate_source_is_fixed_watchlist(self):
        p = _build()
        self.assertEqual(p.candidate_source, "fixed_watchlist")

    def test_starts_pending(self):
        p = _build()
        self.assertEqual(p.approval_state, ApprovalState.PENDING)
        self.assertIsNone(p.approval_received_at)
        self.assertIsNone(p.approval_expires_at)

    def test_no_ranking_or_score_fabricated(self):
        p = _build()
        self.assertFalse(hasattr(p, "score"))
        self.assertFalse(hasattr(p, "rank"))
        self.assertFalse(hasattr(p, "strategy_fit"))
        self.assertFalse(hasattr(p, "regime"))
        joined_assumptions = " ".join(p.assumptions).lower()
        self.assertIn("no universe search", joined_assumptions)

    def test_rejects_non_positive_price(self):
        with self.assertRaises(ValueError):
            _build(current_price=0.0)

    def test_multiple_candidates_independent(self):
        p1 = _build(proposal_id="P-1", trade_id="T-1", symbol="TSLA", current_price=100.0)
        p2 = _build(proposal_id="P-2", trade_id="T-2", symbol="DELL", current_price=50.0)
        self.assertNotEqual(p1.proposal_id, p2.proposal_id)
        self.assertNotEqual(p1.trade_id, p2.trade_id)
        self.assertNotEqual(p1.symbol, p2.symbol)
        self.assertAlmostEqual(p2.ladder_1_trigger, 47.5)

    def test_rejects_missing_trade_id(self):
        with self.assertRaises(ValueError):
            _build(trade_id="")

    def test_proposed_action_is_recorded(self):
        p = _build(action=TradeAction.LADDER_1)
        self.assertEqual(p.proposed_action, TradeAction.LADDER_1)

    def test_rejects_missing_action_argument(self):
        with self.assertRaises(TypeError):
            build_trade_proposal(
                proposal_id="P-1",
                trade_id="T-1",
                symbol="TSLA",
                current_price=100.0,
                as_of=_now(),
                strategy=_strategy(),
                floor_context=_no_position(),
            )  # action omitted entirely

    def test_rejects_wrong_type_for_action(self):
        with self.assertRaises(TypeError):
            _build(action="initial_entry")


class TestD1StrategyRequired(unittest.TestCase):
    """D1 governance fix: strategy must be explicit and validated;
    missing/invalid strategy must BLOCK proposal creation."""

    def test_missing_strategy_argument_raises(self):
        with self.assertRaises(TypeError):
            build_trade_proposal(
                proposal_id="P-1",
                trade_id="T-1",
                action=TradeAction.INITIAL_ENTRY,
                symbol="TSLA",
                current_price=100.0,
                as_of=_now(),
                floor_context=_no_position(),
            )  # strategy omitted entirely

    def test_wrong_type_for_strategy_raises(self):
        with self.assertRaises(TypeError):
            _build(strategy="not-a-strategy-rule-set")

    def test_tampered_strategy_value_cannot_be_constructed(self):
        with self.assertRaises(StrategyUnavailableError):
            replace(_strategy(), ladder_1_pct=-0.06)

    def test_tampered_max_position_cannot_be_constructed(self):
        with self.assertRaises(StrategyUnavailableError):
            replace(_strategy(), maximum_position=100)

    def test_approved_strategy_rule_set_matches_documented_rule_table(self):
        s = approved_strategy_rule_set()
        self.assertEqual(s.ladder_1_pct, -0.05)
        self.assertEqual(s.ladder_1_qty, 10)
        self.assertEqual(s.ladder_2_pct, -0.08)
        self.assertEqual(s.ladder_2_qty, 20)
        self.assertEqual(s.floor_pct, -0.10)
        self.assertEqual(s.initial_qty, 10)
        self.assertEqual(s.maximum_position, 40)


class TestFloorRequiredAtProposalStage(unittest.TestCase):
    """New Controller-approved requirement: a candidate must not be
    proposed unless floor information is available and valid for its
    trade context -- no default, no inference, no substitution."""

    def test_missing_floor_context_argument_raises(self):
        with self.assertRaises(TypeError):
            build_trade_proposal(
                proposal_id="P-1",
                trade_id="T-1",
                action=TradeAction.INITIAL_ENTRY,
                symbol="TSLA",
                current_price=100.0,
                as_of=_now(),
                strategy=_strategy(),
            )  # floor_context omitted entirely

    def test_wrong_type_for_floor_context_raises(self):
        with self.assertRaises(TypeError):
            _build(floor_context=None)

    def test_no_existing_position_is_explicit_and_accepted(self):
        p = _build(floor_context=FloorContext.no_existing_position())
        self.assertIsNone(p.active_floor_at_proposal)

    def test_known_floor_is_recorded_on_the_proposal(self):
        p = _build(floor_context=FloorContext.known(90.0))
        self.assertEqual(p.active_floor_at_proposal, 90.0)

    def test_invalid_floor_price_cannot_be_constructed(self):
        with self.assertRaises(InvalidFloorContextError):
            FloorContext.known(-5.0)

    def test_zero_floor_price_cannot_be_constructed(self):
        with self.assertRaises(InvalidFloorContextError):
            FloorContext.known(0.0)

    def test_existing_position_flag_without_price_cannot_be_constructed(self):
        from proposals.models import FloorContext as FC

        with self.assertRaises(InvalidFloorContextError):
            FC(has_existing_position=True, floor_price=None)

    def test_no_position_flag_with_a_price_cannot_be_constructed(self):
        from proposals.models import FloorContext as FC

        with self.assertRaises(InvalidFloorContextError):
            FC(has_existing_position=False, floor_price=90.0)

    def test_no_floor_is_ever_invented_or_inferred(self):
        # No code path computes a floor value on the candidate's behalf --
        # the recorded active_floor_at_proposal is always exactly what
        # the caller explicitly declared, never derived from current_price.
        p = _build(current_price=123.45, floor_context=FloorContext.known(77.0))
        self.assertEqual(p.active_floor_at_proposal, 77.0)
        self.assertNotAlmostEqual(p.active_floor_at_proposal, 123.45 * 0.90)


if __name__ == "__main__":
    unittest.main()
