"""D-0084 — the news signal must actually measure something.

Two defects, both found on live data from 2026-10-06 and both fixed
here:

1. The catalyst/risk questions demanded "3 very short bullets" of each
   with no way to answer "there are none", so the model returned three
   of each for every symbol and the polarity
   (catalysts - risks) / (catalysts + risks) was EXACTLY ZERO for every
   candidate in every cycle. MUFG, TX and SMH all scored an identical
   3.50 of 7 on news that day.

2. `SymbolResearchHub` recorded a permanent HTTP 403 on the news
   endpoint and a quiet "this symbol has no headlines" as the same
   string, "tiingo", in `sources_failed`. The 403 went unnoticed until
   a hand-written probe found it.
"""

from __future__ import annotations

import unittest
from datetime import datetime

from engine.research_hub import (
    CATALYST_QUERY, NO_MATERIAL_TOKEN, RISK_QUERY, SymbolResearch,
    SymbolResearchHub, drop_non_material,
)
from engine.trade_evaluator import DEFAULT_CONFIG, _score_news
from marketdata.tiingo_source import TiingoHTTPError


class TestTheNoMaterialFilter(unittest.TestCase):
    """The filter is a pure function on purpose: it is the part that
    must not depend on the model behaving well, so it is tested without
    any API call."""

    def test_the_bare_token_is_dropped(self):
        self.assertEqual(drop_non_material(["NONE"]), [])

    def test_it_is_dropped_however_the_model_decorates_it(self):
        for raw in ("none", " None ", "• NONE", "- none.", '"NONE"',
                    "NONE.", "— None", "*none*".strip("*")):
            with self.subTest(raw=raw):
                self.assertEqual(drop_non_material([raw]), [],
                                 msg=f"{raw!r} should count as nothing")

    def test_a_REAL_finding_that_merely_contains_the_word_is_KEPT(self):
        """Dropping a genuine risk is a worse error than keeping an
        empty one, so the match is deliberately narrow."""
        kept = drop_non_material([
            "None of the three plants have reopened",
            "Guidance withdrawn; none issued for Q4",
        ])
        self.assertEqual(len(kept), 2)

    def test_real_findings_survive_unchanged(self):
        lines = ["Beat Q3 earnings", "Shanghai output rising"]
        self.assertEqual(drop_non_material(lines), lines)

    def test_blank_lines_never_become_findings(self):
        self.assertEqual(drop_non_material(["", "   ", "•", None]), [])


class TestTheQuestionsAskForTheToken(unittest.TestCase):
    def test_both_questions_name_the_exact_token(self):
        for q in (CATALYST_QUERY, RISK_QUERY):
            with self.subTest(q=q[:30]):
                self.assertIn(NO_MATERIAL_TOKEN, q)

    def test_neither_question_demands_three_any_more(self):
        """The old wording ordered '3 very short bullets', which is the
        instruction that pinned the polarity at zero."""
        for q in (CATALYST_QUERY, RISK_QUERY):
            with self.subTest(q=q[:30]):
                self.assertNotIn("3 very short bullets", q)
                self.assertIn("At most 3", q)

    def test_the_polarity_emphasis_survives(self):
        """Callers branch on these words; losing them silently turned a
        catalyst query into a risk query in the report path."""
        self.assertIn("POSITIVE", CATALYST_QUERY)
        self.assertIn("NEGATIVE", RISK_QUERY)


class TestPolarityCanFinallyMove(unittest.TestCase):
    """The whole point: the news component must return different values
    for different inputs."""

    def _score(self, cats, risks):
        r = SymbolResearch(
            symbol="X", collected_at=datetime.utcnow(),
            perplexity_catalysts=["c"] * cats,
            perplexity_risks=["r"] * risks,
        )
        return _score_news(r, DEFAULT_CONFIG)

    def test_three_and_three_still_lands_on_the_midpoint(self):
        """Unchanged behaviour for the balanced case -- this is the
        value every symbol was pinned to before."""
        self.assertAlmostEqual(self._score(3, 3), 3.5)

    def test_all_positive_now_scores_the_full_weight(self):
        self.assertAlmostEqual(self._score(3, 0), 7.0)

    def test_all_negative_now_scores_zero(self):
        self.assertAlmostEqual(self._score(0, 3), 0.0)

    def test_the_component_is_monotone_in_the_balance(self):
        scores = [self._score(c, r) for c, r in
                  ((0, 3), (1, 2), (1, 1), (2, 1), (3, 0))]
        self.assertEqual(scores, sorted(scores))
        self.assertGreater(scores[-1] - scores[0], 6.0,
                           "the range must be wide enough to separate "
                           "candidates, not a pinned constant")


class _NewsStub:
    def __init__(self, result=None, raises=None):
        self._result = result
        self._raises = raises

    def get_news(self, symbols, limit=10):
        if self._raises is not None:
            raise self._raises
        return self._result


class TestEmptyIsNotFailure(unittest.TestCase):
    def _hub(self, stub):
        return SymbolResearchHub(tiingo=stub)

    def test_an_empty_answer_counts_as_a_SUCCESS_with_zero_news(self):
        research = self._hub(_NewsStub(result=[])).collect("TSLA")
        self.assertIn("tiingo", research.sources_succeeded)
        self.assertEqual([s for s in research.sources_failed
                          if s.startswith("tiingo")], [])
        self.assertEqual(research.news_count_48h, 0)

    def test_a_403_is_recorded_WITH_ITS_STATUS(self):
        research = self._hub(
            _NewsStub(raises=TiingoHTTPError(403))).collect("TSLA")
        self.assertIn("tiingo:http_403", research.sources_failed)
        self.assertNotIn("tiingo", research.sources_succeeded)

    def test_a_403_and_an_empty_answer_are_no_longer_the_same_record(self):
        """The exact confusion that cost a day: both used to read
        'tiingo' and nothing more."""
        forbidden = self._hub(
            _NewsStub(raises=TiingoHTTPError(403))).collect("TSLA")
        quiet = self._hub(_NewsStub(result=[])).collect("TSLA")
        self.assertNotEqual(forbidden.sources_failed, quiet.sources_failed)

    def test_a_quota_error_is_distinguishable_from_a_subscription_one(self):
        """429 clears on its own; 403 never will. Acting on them the
        same way is how a permanent outage gets waited out."""
        quota = self._hub(
            _NewsStub(raises=TiingoHTTPError(429))).collect("TSLA")
        self.assertIn("tiingo:http_429", quota.sources_failed)

    def test_real_headlines_still_land(self):
        stub = _NewsStub(result=[{"title": "A", "source": "Reuters"},
                                 {"title": "B", "source": "WSJ"}])
        research = self._hub(stub).collect("TSLA")
        self.assertEqual(research.news_count_48h, 2)
        self.assertIn("tiingo", research.sources_succeeded)


if __name__ == "__main__":
    unittest.main()


class TestD0088ThePoliticalPushIsCalibrated(unittest.TestCase):
    """D-0088. The political bonus was normalized against a theoretical
    maximum of 25, which needs FIVE distinct whitelisted buyers of the
    same ticker inside fourteen days. Over twelve years of real trades
    for this whitelist -- 179 firings -- that never happened once. The
    highest composite ever observed was 10.8, so a nominal 15-point
    bonus delivered a median of 3.18.
    """

    def _points(self, signal):
        from engine.trade_evaluator import _score_political
        r = SymbolResearch(symbol="X", collected_at=datetime.utcnow())
        r.political_weighted_signal = signal
        return _score_political(r, DEFAULT_CONFIG)

    def test_two_buyers_now_push_about_seven_points(self):
        """Measured signal for two distinct buyers is ~5.0-5.3."""
        self.assertAlmostEqual(self._points(5.3), 7.2, places=1)

    def test_three_buyers_now_push_nearly_the_full_weight(self):
        """Measured signal for three distinct buyers is ~10.6-10.8."""
        self.assertGreater(self._points(10.8), 14.0)

    def test_the_old_scale_delivered_a_fifth_of_that(self):
        """What the numbers were before, so the change is visible in the
        suite rather than only in the decision log."""
        old_two = 5.3 / 25.0 * DEFAULT_CONFIG.weight_political
        old_three = 10.8 / 25.0 * DEFAULT_CONFIG.weight_political
        self.assertAlmostEqual(old_two, 3.18, places=2)
        self.assertAlmostEqual(old_three, 6.48, places=2)
        self.assertGreater(self._points(5.3), old_two * 2)

    def test_the_push_actually_carries_a_symbol_over_the_bar(self):
        """The Controller's own example: a symbol at 53 on the other six
        components, with two political buyers, must reach 60."""
        from engine.engine import Engine
        self.assertGreaterEqual(53.0 + self._points(5.3), Engine._MIN_SCORE)

    def test_no_signal_is_still_no_points(self):
        for sig in (0.0, -1.0, None):
            with self.subTest(sig=sig):
                r = SymbolResearch(symbol="X", collected_at=datetime.utcnow())
                r.political_weighted_signal = sig
                from engine.trade_evaluator import _score_political
                self.assertEqual(_score_political(r, DEFAULT_CONFIG), 0.0)

    def test_it_can_never_exceed_its_weight(self):
        """A widened whitelist could push the raw signal past the
        measured ceiling; the clamp must hold."""
        for sig in (11.0, 25.0, 1000.0):
            with self.subTest(sig=sig):
                self.assertLessEqual(self._points(sig),
                                     DEFAULT_CONFIG.weight_political)
