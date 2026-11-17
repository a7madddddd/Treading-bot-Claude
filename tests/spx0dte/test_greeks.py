import unittest

from spx0dte.greeks import (
    ImpliedVolatilityError, OptionType, black_scholes_greeks, black_scholes_price,
    implied_volatility, minutes_to_years, reprice_from_stale_quote,
)


class TestBlackScholesGreeks(unittest.TestCase):
    def test_atm_call_matches_known_worked_example(self):
        # From this project's earlier scratchpad verification:
        # ATM SPX call, 2h to expiry, IV 15% -> delta=0.502, theta=-$32.37/day
        g = black_scholes_greeks(
            S=5900, K=5900, T=minutes_to_years(120), r=0.045, sigma=0.15,
            option_type=OptionType.CALL,
        )
        self.assertAlmostEqual(g.delta, 0.502, places=2)
        self.assertAlmostEqual(g.theta, -32.37, delta=0.5)

    def test_call_put_parity(self):
        S, K, T, r, sigma = 5900, 5900, minutes_to_years(120), 0.045, 0.15
        call = black_scholes_price(S, K, T, r, sigma, OptionType.CALL)
        put = black_scholes_price(S, K, T, r, sigma, OptionType.PUT)
        # C - P = S - K*e^(-rT)
        import math
        self.assertAlmostEqual(call - put, S - K * math.exp(-r * T), places=4)

    def test_deep_itm_call_delta_near_one(self):
        g = black_scholes_greeks(S=6000, K=5000, T=minutes_to_years(60), r=0.045, sigma=0.15, option_type=OptionType.CALL)
        self.assertGreater(g.delta, 0.99)

    def test_deep_otm_call_delta_near_zero(self):
        g = black_scholes_greeks(S=5000, K=6000, T=minutes_to_years(60), r=0.045, sigma=0.15, option_type=OptionType.CALL)
        self.assertLess(g.delta, 0.01)

    def test_zero_time_returns_intrinsic_value(self):
        g = black_scholes_greeks(S=5910, K=5900, T=0, r=0.045, sigma=0.15, option_type=OptionType.CALL)
        self.assertEqual(g.price, 10.0)
        self.assertEqual(g.delta, 1.0)
        self.assertEqual(g.gamma, 0.0)

    def test_zero_time_otm_call_is_worthless(self):
        g = black_scholes_greeks(S=5890, K=5900, T=0, r=0.045, sigma=0.15, option_type=OptionType.CALL)
        self.assertEqual(g.price, 0.0)
        self.assertEqual(g.delta, 0.0)

    def test_rejects_nonpositive_underlying(self):
        with self.assertRaises(ValueError):
            black_scholes_greeks(S=0, K=5900, T=0.1, r=0.045, sigma=0.15, option_type=OptionType.CALL)

    def test_rejects_nonpositive_strike(self):
        with self.assertRaises(ValueError):
            black_scholes_greeks(S=5900, K=-1, T=0.1, r=0.045, sigma=0.15, option_type=OptionType.CALL)

    def test_rejects_negative_time(self):
        with self.assertRaises(ValueError):
            black_scholes_greeks(S=5900, K=5900, T=-0.1, r=0.045, sigma=0.15, option_type=OptionType.CALL)


class TestMinutesToYears(unittest.TestCase):
    def test_conversion(self):
        self.assertAlmostEqual(minutes_to_years(60 * 24 * 365), 1.0, places=6)


class TestImpliedVolatility(unittest.TestCase):
    def test_round_trip_recovers_known_sigma(self):
        S, K, T, r = 5900, 5900, minutes_to_years(120), 0.045
        true_sigma = 0.18
        price = black_scholes_price(S, K, T, r, true_sigma, OptionType.CALL)
        recovered = implied_volatility(price, S, K, T, r, OptionType.CALL)
        self.assertAlmostEqual(recovered, true_sigma, places=3)

    def test_round_trip_put(self):
        S, K, T, r = 5900, 5950, minutes_to_years(90), 0.045
        true_sigma = 0.22
        price = black_scholes_price(S, K, T, r, true_sigma, OptionType.PUT)
        recovered = implied_volatility(price, S, K, T, r, OptionType.PUT)
        self.assertAlmostEqual(recovered, true_sigma, places=3)

    def test_price_below_intrinsic_raises(self):
        with self.assertRaises(ImpliedVolatilityError):
            implied_volatility(0.0001, S=6000, K=5000, T=minutes_to_years(60), r=0.045, option_type=OptionType.CALL)

    def test_nonpositive_price_raises(self):
        with self.assertRaises(ImpliedVolatilityError):
            implied_volatility(0, S=5900, K=5900, T=minutes_to_years(60), r=0.045, option_type=OptionType.CALL)

    def test_zero_time_raises(self):
        with self.assertRaises(ImpliedVolatilityError):
            implied_volatility(10.0, S=5900, K=5900, T=0, r=0.045, option_type=OptionType.CALL)


class TestRepriceFromStaleQuote(unittest.TestCase):
    def test_reprices_with_live_underlying_same_sigma(self):
        K, r = 5900, 0.045
        stale_T = minutes_to_years(120)
        true_sigma = 0.15
        stale_S = 5900
        stale_price = black_scholes_price(stale_S, K, stale_T, r, true_sigma, OptionType.CALL)

        # 16 minutes later, underlying moved up, less time remains.
        live_S = 5910
        live_T = minutes_to_years(104)

        result = reprice_from_stale_quote(
            stale_market_price=stale_price, stale_S=stale_S, stale_T=stale_T,
            live_S=live_S, live_T=live_T, K=K, r=r, option_type=OptionType.CALL,
        )
        self.assertIsNotNone(result)
        expected = black_scholes_price(live_S, K, live_T, r, true_sigma, OptionType.CALL)
        # Bisection solver's own tolerance (1e-4 in price space) bounds
        # how exactly the recovered sigma reproduces true_sigma -- 3
        # decimal places is well within that, 6 is tighter than the
        # solver promises.
        self.assertAlmostEqual(result.price, expected, places=3)

    def test_returns_none_on_bad_stale_quote(self):
        result = reprice_from_stale_quote(
            stale_market_price=0.0001, stale_S=6000, stale_T=minutes_to_years(60),
            live_S=6000, live_T=minutes_to_years(45), K=5000, r=0.045, option_type=OptionType.CALL,
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
