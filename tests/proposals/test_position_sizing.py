"""Tests for the D-0051 PositionSizingPolicy."""

from __future__ import annotations

import pytest

from proposals.position_sizing import (
    APPROVED_D0051_POLICY,
    PositionSizingPolicy,
    PositionSizingPolicyError,
    SizingResult,
)


# -------------------- construction / validation --------------------

class TestConstruction:
    def test_approved_defaults_build(self):
        p = PositionSizingPolicy()
        assert p.trade_budget_pct == 0.05
        assert p.initial_size_pct == 0.25
        assert p.ladder_1_size_pct == 0.25
        assert p.ladder_2_size_pct == 0.50
        assert p.min_shares == 1

    def test_approved_singleton_matches_defaults(self):
        assert APPROVED_D0051_POLICY == PositionSizingPolicy()

    def test_sizes_must_sum_to_one(self):
        with pytest.raises(PositionSizingPolicyError, match="sum to 1.0"):
            PositionSizingPolicy(
                initial_size_pct=0.3,
                ladder_1_size_pct=0.3,
                ladder_2_size_pct=0.3,  # 0.9 != 1.0
            )

    def test_fraction_bounds_rejected(self):
        with pytest.raises(PositionSizingPolicyError):
            PositionSizingPolicy(trade_budget_pct=0)
        with pytest.raises(PositionSizingPolicyError):
            PositionSizingPolicy(trade_budget_pct=-0.1)
        with pytest.raises(PositionSizingPolicyError):
            PositionSizingPolicy(trade_budget_pct=1.5)

    def test_min_shares_must_be_non_negative_int(self):
        with pytest.raises(PositionSizingPolicyError):
            PositionSizingPolicy(min_shares=-1)
        # 0 is allowed (disables the floor)
        PositionSizingPolicy(min_shares=0)


# -------------------- compute_shares --------------------

class TestComputeShares:
    def test_small_price_many_shares(self):
        """WBD at $12 with $100k equity → 5% = $5,000 → ~104 shares init."""
        r = APPROVED_D0051_POLICY.compute_shares(equity=100_000, price=12.0)
        # 100_000 * 0.05 = 5_000 trade budget
        # Initial = 5000 * 0.25 = 1250 / 12 = 104
        # Ladder 1 = same = 104
        # Ladder 2 = 5000 * 0.50 = 2500 / 12 = 208
        assert r.initial_qty == 104
        assert r.ladder_1_qty == 104
        assert r.ladder_2_qty == 208
        assert r.maximum_position == 416
        assert r.trade_budget_dollars == 5000.00

    def test_high_price_few_shares(self):
        """QQQ at $750 with $100k equity → 5% = $5,000 → 1-3 shares each."""
        r = APPROVED_D0051_POLICY.compute_shares(equity=100_000, price=750.0)
        # Initial = 1250 / 750 = 1 (floor)
        # Ladder 1 = 1250 / 750 = 1
        # Ladder 2 = 2500 / 750 = 3
        assert r.initial_qty == 1
        assert r.ladder_1_qty == 1
        assert r.ladder_2_qty == 3
        assert r.maximum_position == 5

    def test_mid_price_realistic_exposure(self):
        """TSLA at $370 with $100k equity → ~16 shares total ~ $6k."""
        r = APPROVED_D0051_POLICY.compute_shares(equity=100_000, price=370.0)
        assert r.initial_qty == 3   # 1250/370 = 3.38 → 3
        assert r.ladder_1_qty == 3
        assert r.ladder_2_qty == 6  # 2500/370 = 6.75 → 6
        assert r.maximum_position == 12
        # Dollar exposure check: 12 * 370 = $4,440 ~ 4.4% of equity
        assert 12 * 370 < 100_000 * 0.055  # under 5.5% safety band

    def test_scales_with_equity(self):
        """When account doubles, Initial doubles too (allowing +/-1 share
        for int-floor rounding when the ideal lands between integers)."""
        r1 = APPROVED_D0051_POLICY.compute_shares(equity=100_000, price=100.0)
        r2 = APPROVED_D0051_POLICY.compute_shares(equity=200_000, price=100.0)
        assert abs(r2.initial_qty - 2 * r1.initial_qty) <= 1
        assert abs(r2.maximum_position - 2 * r1.maximum_position) <= 3

    def test_min_shares_floor(self):
        """When math produces 0, min_shares=1 kicks in."""
        policy = PositionSizingPolicy(min_shares=1)
        # $100 equity, $500 price → 5 * 0.25 = $1.25, 0 raw shares → 1
        r = policy.compute_shares(equity=100, price=500.0)
        assert r.initial_qty == 1
        assert r.ladder_1_qty == 1
        assert r.ladder_2_qty == 1
        assert r.maximum_position == 3

    def test_min_shares_zero_still_valid_result(self):
        """SizingResult rejects zero quantities, so a min_shares=0
        policy on an un-affordable symbol raises when it tries to
        build the result."""
        policy = PositionSizingPolicy(min_shares=0)
        with pytest.raises(PositionSizingPolicyError):
            policy.compute_shares(equity=10, price=1_000.0)

    def test_zero_equity_rejected(self):
        with pytest.raises(PositionSizingPolicyError, match="equity"):
            APPROVED_D0051_POLICY.compute_shares(equity=0, price=100.0)

    def test_negative_price_rejected(self):
        with pytest.raises(PositionSizingPolicyError, match="price"):
            APPROVED_D0051_POLICY.compute_shares(equity=1000, price=-5.0)


# -------------------- is_tradable --------------------

class TestIsTradable:
    def test_affordable_symbol_passes(self):
        assert APPROVED_D0051_POLICY.is_tradable(equity=100_000, price=50)

    def test_barely_affordable_passes(self):
        # Initial dollars = 100k * 0.05 * 0.25 = $1,250
        # 2x tolerance = $2,500 → price up to $2,500 passes
        assert APPROVED_D0051_POLICY.is_tradable(equity=100_000, price=2_500)

    def test_too_expensive_rejected(self):
        # Price $3,000 > $2,500 cap
        assert not APPROVED_D0051_POLICY.is_tradable(equity=100_000, price=3_000)

    def test_zero_equity_rejected(self):
        assert not APPROVED_D0051_POLICY.is_tradable(equity=0, price=10)
