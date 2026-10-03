"""Tests for news sentiment polarity (D-0050 Phase 17)."""

import unittest
from datetime import datetime
from engine.research_hub import SymbolResearch
from engine.news_sentiment import compute_news_polarity, polarity_label


def _r(cats, risks):
    return SymbolResearch(symbol="X", collected_at=datetime.utcnow(),
                          perplexity_catalysts=cats,
                          perplexity_risks=risks)


class TestPolarity(unittest.TestCase):
    def test_balanced_zero(self):
        self.assertAlmostEqual(compute_news_polarity(
            _r(["a"], ["b"])), 0.0)

    def test_all_catalysts_plus_one(self):
        self.assertAlmostEqual(compute_news_polarity(
            _r(["a", "b", "c"], [])), 1.0)

    def test_all_risks_minus_one(self):
        self.assertAlmostEqual(compute_news_polarity(
            _r([], ["a", "b"])), -1.0)

    def test_mixed(self):
        # 2 catalysts, 1 risk → (2-1)/3 = 0.333
        self.assertAlmostEqual(compute_news_polarity(
            _r(["a", "b"], ["x"])), 1.0 / 3.0, places=4)

    def test_empty_returns_none(self):
        self.assertIsNone(compute_news_polarity(_r([], [])))


class TestLabel(unittest.TestCase):
    def test_strongly_positive(self):
        self.assertEqual(polarity_label(0.8), "strongly-positive")

    def test_positive(self):
        self.assertEqual(polarity_label(0.25), "positive")

    def test_neutral(self):
        self.assertEqual(polarity_label(0.0), "neutral")
        self.assertEqual(polarity_label(-0.1), "neutral")

    def test_negative(self):
        self.assertEqual(polarity_label(-0.25), "negative")

    def test_strongly_negative(self):
        self.assertEqual(polarity_label(-0.9), "strongly-negative")

    def test_none_is_unknown(self):
        self.assertEqual(polarity_label(None), "unknown")


class TestNewsScoreIntegration(unittest.TestCase):
    """Verify the evaluator's news component picks up polarity."""
    def test_positive_news_scores_higher_than_negative(self):
        from engine.trade_evaluator import _score_news, DEFAULT_CONFIG
        r_pos = _r(["a", "b"], [])
        r_neg = _r([], ["x", "y"])
        s_pos = _score_news(r_pos, DEFAULT_CONFIG)
        s_neg = _score_news(r_neg, DEFAULT_CONFIG)
        self.assertGreater(s_pos, s_neg)


if __name__ == "__main__":
    unittest.main()
