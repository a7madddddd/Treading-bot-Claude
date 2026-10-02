"""Tests for DeepResearchComposer (D-0050 Phase 9)."""

import unittest
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Tuple

from engine.deep_research import (
    DeepResearchComposer, DeepResearchReport,
    _synthesize_verdict,
)


# --- stubs ---------------------------------------------------------------

@dataclass
class _F:  summary: str
@dataclass
class _R:  findings: tuple


class _FredStub:
    def get_series(self, series_id, start, end):
        if series_id == "VIXCLS":
            # 100 values of 15, today at 18 -> ~100th percentile
            return [(date.today() - timedelta(days=i), 15.0) for i in range(99, 0, -1)] \
                   + [(date.today(), 18.0)]
        if series_id == "DFF":
            return [(date.today(), 5.25)]
        if series_id == "T10Y2Y":
            return [(date.today(), 0.42)]
        return []


class _FinnhubStub:
    def basic_financials(self, s):
        return {"metric": {"peBasicExclExtraTTM": 85.2,
                           "marketCapitalization": 800000,
                           "52WeekHigh": 300.0, "52WeekLow": 150.0}}
    def earnings_calendar(self, s, a, b):
        return [{"date": (date.today() + timedelta(days=21)).isoformat(),
                 "epsEstimate": 0.72}]
    def recommendation_trends(self, s):
        return [{"strongBuy": 8, "buy": 6, "hold": 3, "sell": 1, "strongSell": 0}]
    def insider_sentiment(self, s, a, b):
        return {"data": [{"year": 2026, "month": 9, "mspr": 35.0}]}


class _AVStub:
    def rsi(self, s, **kw): return [(date.today(), 72.0)]
    def macd(self, s, **kw):
        return [(date.today() - timedelta(days=1),
                 {"macd": 1.0, "signal": 1.1, "hist": -0.1}),
                (date.today(),
                 {"macd": 1.2, "signal": 1.0, "hist": 0.2})]
    def bbands(self, s, **kw):
        return [(date.today(),
                 {"upper": 260.0, "middle": 250.0, "lower": 240.0})]
    def sma(self, s, time_period=50, **kw):
        return [(date.today(), 235.0 if time_period == 50 else 210.0)]


class _PolyStub:
    def get_ticker_snapshot(self, s):
        return {"day": {"c": 253.40, "h": 255.0, "l": 248.0, "v": 1_200_000},
                "prevDay": {"c": 248.0}}
    def get_ticker_details(self, s):
        return {"name": "Tesla, Inc.", "sic_description": "Motor Vehicles"}
    def get_news(self, s, limit=10):
        return [{"title": "Polygon headline", "publisher": {"name": "AP"}}]


class _TiingoStub:
    def get_news(self, syms, limit=10):
        return [
            {"title": "Tesla cuts prices in China", "source": "Reuters"},
            {"title": "Morgan Stanley raises target", "source": "WSJ"},
            {"title": "FSD v12 launch imminent", "source": "Electrek"},
        ]


class _PxStub:
    def research_stock(self, t, q):
        if "POSITIVE" in q:
            return _R(findings=(
                _F("Beat Q3 earnings expectations"),
                _F("Shanghai factory boosting output"),
            ))
        return _R(findings=(
            _F("NHTSA investigation ongoing"),
            _F("China price cuts may compress margins"),
        ))


# --- tests ---------------------------------------------------------------

class TestDeepResearch(unittest.TestCase):
    def test_full_report_produced(self):
        comp = DeepResearchComposer(
            fred=_FredStub(), finnhub=_FinnhubStub(),
            alpha_vantage=_AVStub(), polygon=_PolyStub(),
            tiingo=_TiingoStub(), perplexity=_PxStub(),
        )
        text = comp.enrich("TSLA")
        self.assertIsNotNone(text)
        # Every section present
        self.assertIn("🌐 Macro", text)
        self.assertIn("🏢 Fundamentals", text)
        self.assertIn("📈 Technicals", text)
        self.assertIn("🎯 Live snapshot", text)
        self.assertIn("📰 News", text)
        self.assertIn("🔍 Catalysts", text)
        self.assertIn("⚠ Risks", text)
        self.assertIn("⚖ Verdict", text)
        # Specific signals bubbled through
        self.assertIn("P/E 85.2", text)
        self.assertIn("$800.0B", text)
        self.assertIn("RSI(14): 72", text)
        self.assertIn("MACD: bullish crossover", text)
        self.assertIn("golden cross", text)
        self.assertIn("$253.40", text)
        self.assertIn("Reuters", text)
        self.assertIn("Beat Q3 earnings", text)
        self.assertIn("NHTSA investigation", text)

    def test_all_clients_none_returns_none(self):
        comp = DeepResearchComposer()
        self.assertIsNone(comp.enrich("TSLA"))

    def test_partial_sources_still_produce_report(self):
        comp = DeepResearchComposer(finnhub=_FinnhubStub())
        text = comp.enrich("TSLA")
        self.assertIsNotNone(text)
        self.assertIn("🏢 Fundamentals", text)
        self.assertNotIn("🌐 Macro", text)
        self.assertNotIn("📈 Technicals", text)

    def test_one_source_raises_others_still_run(self):
        class Boom:
            def basic_financials(self, s): raise RuntimeError("boom")
            def earnings_calendar(self, *a): raise RuntimeError("boom")
            def recommendation_trends(self, s): raise RuntimeError("boom")
            def insider_sentiment(self, *a): raise RuntimeError("boom")
        comp = DeepResearchComposer(finnhub=Boom(), polygon=_PolyStub())
        text = comp.enrich("TSLA")
        self.assertIsNotNone(text)
        self.assertIn("🎯 Live snapshot", text)
        self.assertNotIn("🏢 Fundamentals", text)

    def test_verdict_detects_overbought(self):
        verdict = _synthesize_verdict({"rsi": 80})
        self.assertTrue(any("overbought" in ln for ln in verdict))

    def test_verdict_detects_earnings_soon(self):
        verdict = _synthesize_verdict({"next_earnings_days": 3})
        self.assertTrue(any("Earnings in 3d" in ln for ln in verdict))

    def test_verdict_macro_risk_off(self):
        verdict = _synthesize_verdict({"regime": "risk_off"})
        self.assertTrue(any("RISK_OFF" in ln for ln in verdict))

    def test_verdict_no_signals(self):
        verdict = _synthesize_verdict({})
        self.assertEqual(verdict, ["No strong signals either way."])

    def test_news_falls_back_to_polygon_when_tiingo_empty(self):
        class Empty:
            def get_news(self, syms, limit=10): return []
        comp = DeepResearchComposer(tiingo=Empty(), polygon=_PolyStub())
        text = comp.enrich("TSLA")
        self.assertIn("Polygon headline", text)


if __name__ == "__main__":
    unittest.main()
