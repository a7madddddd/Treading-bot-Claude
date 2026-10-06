"""Tests for TradeEvaluator (D-0050 Phase 10)."""

import unittest
from datetime import date, datetime, timedelta
from dataclasses import replace

from engine.research_hub import SymbolResearch
from engine.trade_evaluator import (
    TradeEvaluator, EvaluatorConfig, DEFAULT_CONFIG,
    format_evaluation_block, _hard_filter,
    _score_fundamentals, _score_technicals, _score_momentum,
    _score_news, _risk_discount,
)


def _good_research(**overrides) -> SymbolResearch:
    r = SymbolResearch(
        symbol="TSLA",
        collected_at=datetime(2026, 10, 2),
        current_price=250.0,
        day_change_pct=1.5,
        day_volume=5_000_000,
        prev_close=246.3,
        pe_ratio=25.0,
        market_cap_millions=800_000,
        next_earnings_days=30,
        analyst_buys=10,
        analyst_holds=4,
        analyst_sells=1,
        analyst_buy_ratio=10/15,
        insider_mspr=20.0,
        rsi_14=55.0,
        macd_signal="bullish",
        macd_hist=0.2,
        bb_upper=260.0, bb_middle=250.0, bb_lower=240.0, bb_position=0.5,
        sma_50=235.0, sma_200=210.0, golden_cross=True,
        news_count_48h=3,
        news_headlines=[("good news", "Reuters")],
        perplexity_catalysts=["a", "b"],
        perplexity_risks=[],
        regime="risk_on",
        vix_percentile=0.2, vix_level=14.0,
    )
    for k, v in overrides.items():
        setattr(r, k, v)
    return r


class _StubHub:
    def __init__(self, by_symbol):
        self._by = by_symbol
    def collect(self, symbol):
        return self._by[symbol]


# --------------------------------------------------------------------
# Hard filter
# --------------------------------------------------------------------

class TestHardFilter(unittest.TestCase):
    def test_passes_good(self):
        self.assertEqual(_hard_filter(_good_research(), DEFAULT_CONFIG), [])

    def test_no_price_rejected(self):
        r = _good_research(current_price=None)
        self.assertIn("no live price", _hard_filter(r, DEFAULT_CONFIG)[0])

    def test_low_volume_rejected(self):
        r = _good_research(day_volume=50_000)
        reasons = _hard_filter(r, DEFAULT_CONFIG)
        self.assertTrue(any("low volume" in x for x in reasons))

    def test_earnings_soon_rejected(self):
        r = _good_research(next_earnings_days=3)
        reasons = _hard_filter(r, DEFAULT_CONFIG)
        self.assertTrue(any("earnings" in x for x in reasons))

    def test_extreme_rsi_rejected(self):
        r = _good_research(rsi_14=95)
        reasons = _hard_filter(r, DEFAULT_CONFIG)
        self.assertTrue(any("extreme" in x for x in reasons))

    def test_risk_off_regime_rejected(self):
        r = _good_research(regime="risk_off")
        reasons = _hard_filter(r, DEFAULT_CONFIG)
        self.assertTrue(any("RISK_OFF" in x for x in reasons))


# --------------------------------------------------------------------
# Soft scorers
# --------------------------------------------------------------------

class TestScorers(unittest.TestCase):
    def test_fundamentals_zero_when_no_data(self):
        r = _good_research(pe_ratio=None, analyst_buy_ratio=None,
                           insider_mspr=None)
        self.assertEqual(_score_fundamentals(r, DEFAULT_CONFIG), 0.0)

    def test_fundamentals_high_when_sweet_spot(self):
        r = _good_research(pe_ratio=20, analyst_buy_ratio=0.9,
                           insider_mspr=50)
        s = _score_fundamentals(r, DEFAULT_CONFIG)
        # weight_fundamentals rebalanced in B.26; perfect inputs → >=10
        self.assertGreater(s, 10)
        self.assertLessEqual(s, DEFAULT_CONFIG.weight_fundamentals)

    def test_technicals_high_on_crossover(self):
        r = _good_research(macd_signal="bullish_crossover", rsi_14=55,
                           golden_cross=True)
        s = _score_technicals(r, DEFAULT_CONFIG)
        # weight_technicals rebalanced in B.26; perfect signals → full weight
        self.assertGreater(s, 15)
        self.assertLessEqual(s, DEFAULT_CONFIG.weight_technicals)

    def test_technicals_low_on_bearish(self):
        r = _good_research(macd_signal="bearish_crossover", rsi_14=80,
                           golden_cross=False)
        s = _score_technicals(r, DEFAULT_CONFIG)
        self.assertLess(s, 10)

    def test_momentum_zero_without_data(self):
        r = _good_research(day_change_pct=None, current_price=None, sma_50=None)
        self.assertEqual(_score_momentum(r, DEFAULT_CONFIG), 0.0)

    def test_news_counts_scale(self):
        r = _good_research(news_count_48h=5, perplexity_catalysts=["x","y","z"])
        s1 = _score_news(r, DEFAULT_CONFIG)
        r2 = _good_research(news_count_48h=0, perplexity_catalysts=[])
        s2 = _score_news(r2, DEFAULT_CONFIG)
        self.assertGreater(s1, s2)

    def test_the_number_of_risk_BULLETS_costs_nothing(self):
        """D-0083. The research prompt asks for exactly 3 bullets and
        caps the list at 3, so a per-bullet charge was paid identically
        by every candidate in every cycle (-9.0 measured live on
        2026-10-06) and could never separate a safe symbol from a risky
        one. Counting bullets is not measuring risk."""
        none_ = _good_research(perplexity_risks=[])
        three = _good_research(perplexity_risks=["a", "b", "c"])
        self.assertEqual(_risk_discount(none_, DEFAULT_CONFIG),
                         _risk_discount(three, DEFAULT_CONFIG))

    def test_a_bearish_crossover_still_costs_exactly_five_more(self):
        """The rules that DISCRIMINATE are untouched by D-0083: the gap
        between a clean and a risky candidate must be the same 5.0 it
        was before the bullet-count rule was removed."""
        clean = _good_research(perplexity_risks=["a", "b", "c"])
        risky = _good_research(perplexity_risks=["a", "b", "c"],
                               macd_signal="bearish_crossover")
        self.assertAlmostEqual(
            _risk_discount(risky, DEFAULT_CONFIG)
            - _risk_discount(clean, DEFAULT_CONFIG),
            5.0)


# --------------------------------------------------------------------
# Evaluator end-to-end
# --------------------------------------------------------------------

class TestEvaluator(unittest.TestCase):
    def test_pass_returns_score_breakdown(self):
        hub = _StubHub({"TSLA": _good_research()})
        ev = TradeEvaluator(hub)
        res = ev.evaluate("TSLA")
        self.assertTrue(res.passes_hard_filter)
        self.assertGreater(res.soft_score, 40)
        self.assertIn("fundamentals", res.score_breakdown)
        self.assertIn("technicals", res.score_breakdown)

    def test_rejected_candidate_has_zero_score(self):
        hub = _StubHub({"BAD": _good_research(next_earnings_days=1,
                                              symbol="BAD")})
        ev = TradeEvaluator(hub)
        res = ev.evaluate("BAD")
        self.assertFalse(res.passes_hard_filter)
        self.assertEqual(res.soft_score, 0.0)

    def test_rank_sorts_best_first(self):
        a = _good_research(symbol="A", rsi_14=55, macd_signal="bullish_crossover",
                            golden_cross=True, perplexity_risks=[])
        b = _good_research(symbol="B", rsi_14=68, macd_signal="bearish",
                            golden_cross=False,
                            perplexity_risks=["r1","r2"])
        c_rejected = _good_research(symbol="C", next_earnings_days=1)
        hub = _StubHub({"A": a, "B": b, "C": c_rejected})
        ev = TradeEvaluator(hub)
        order = ev.rank(["B", "A", "C"])
        self.assertEqual(order[0].symbol, "A")
        self.assertEqual(order[1].symbol, "B")
        self.assertEqual(order[2].symbol, "C")  # rejected last

    def test_format_block_rejected(self):
        hub = _StubHub({"TSLA": _good_research(next_earnings_days=2)})
        ev = TradeEvaluator(hub)
        text = format_evaluation_block(ev.evaluate("TSLA"))
        self.assertIn("REJECTED", text)
        self.assertIn("earnings", text)

    def test_format_block_passing(self):
        hub = _StubHub({"TSLA": _good_research()})
        text = format_evaluation_block(TradeEvaluator(hub).evaluate("TSLA"))
        self.assertIn("Evaluator score", text)
        self.assertIn("P/E", text)
        self.assertIn("RSI", text)


if __name__ == "__main__":
    unittest.main()
