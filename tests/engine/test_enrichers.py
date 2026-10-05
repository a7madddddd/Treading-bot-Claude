"""Tests for the composite research enrichers (D-0050 Phase 7)."""

import unittest
from dataclasses import dataclass
from datetime import date
from typing import Tuple

from engine.enrichers import (
    CompositeEnricher,
    PerplexityEnricher,
    FinnhubEnricher,
    AlphaVantageEnricher,
    TiingoEnricher,
    PolygonEnricher,
)


# --------------------------------------------------------------------------
# Perplexity
# --------------------------------------------------------------------------

@dataclass
class _Finding:
    summary: str

@dataclass
class _Report:
    findings: Tuple[_Finding, ...]


class _PerplexityStub:
    def __init__(self, report=None, raise_exc=None):
        self._report = report
        self._raise = raise_exc

    def research_stock(self, ticker, question):
        if self._raise:
            raise self._raise
        return self._report


class TestPerplexity(unittest.TestCase):
    def test_none_client(self):
        self.assertIsNone(PerplexityEnricher(None).enrich("TSLA"))

    def test_raises(self):
        e = PerplexityEnricher(_PerplexityStub(raise_exc=RuntimeError("x")))
        self.assertIsNone(e.enrich("TSLA"))

    def test_findings(self):
        rep = _Report(findings=(_Finding("Beat Q3"), _Finding("Upgrade")))
        got = PerplexityEnricher(_PerplexityStub(report=rep)).enrich("TSLA")
        self.assertIn("Beat Q3", got)
        self.assertIn("Upgrade", got)
        self.assertTrue(got.startswith("🔍 Research:"))


# --------------------------------------------------------------------------
# Finnhub
# --------------------------------------------------------------------------

class _FinnhubStub:
    def __init__(self, fin=None, raise_exc=None):
        self._fin = fin
        self._raise = raise_exc

    def basic_financials(self, symbol):
        if self._raise:
            raise self._raise
        return self._fin


class TestFinnhub(unittest.TestCase):
    def test_none_client(self):
        self.assertIsNone(FinnhubEnricher(None).enrich("TSLA"))

    def test_raises(self):
        e = FinnhubEnricher(_FinnhubStub(raise_exc=RuntimeError("x")))
        self.assertIsNone(e.enrich("TSLA"))

    def test_no_metric(self):
        self.assertIsNone(FinnhubEnricher(_FinnhubStub(fin={})).enrich("TSLA"))

    def test_full_metrics(self):
        fin = {"metric": {
            "peBasicExclExtraTTM": 85.2,
            "marketCapitalization": 800000,  # millions → $800B
            "52WeekHigh": 300.0,
            "52WeekLow": 150.0,
        }}
        got = FinnhubEnricher(_FinnhubStub(fin=fin)).enrich("TSLA")
        self.assertIn("P/E 85.2", got)
        self.assertIn("$800.0B", got)
        self.assertIn("52w $150.0-$300.0", got)
        self.assertTrue(got.startswith("💼"))

    def test_negative_pe_ignored(self):
        fin = {"metric": {"peBasicExclExtraTTM": -5.0,
                           "marketCapitalization": 100}}
        got = FinnhubEnricher(_FinnhubStub(fin=fin)).enrich("X")
        self.assertNotIn("P/E", got)
        self.assertIn("MarketCap", got)


# --------------------------------------------------------------------------
# Alpha Vantage
# --------------------------------------------------------------------------

class _AVStub:
    def __init__(self, rsi=None, macd=None, raise_rsi=None, raise_macd=None):
        self._rsi = rsi or []
        self._macd = macd or []
        self._raise_rsi = raise_rsi
        self._raise_macd = raise_macd

    def rsi(self, symbol, **kw):
        if self._raise_rsi:
            raise self._raise_rsi
        return self._rsi

    def macd(self, symbol, **kw):
        if self._raise_macd:
            raise self._raise_macd
        return self._macd


class TestAlphaVantage(unittest.TestCase):
    def test_none_client(self):
        self.assertIsNone(AlphaVantageEnricher(None).enrich("TSLA"))

    def test_both_fail(self):
        e = AlphaVantageEnricher(_AVStub(
            raise_rsi=OSError("x"), raise_macd=OSError("x")))
        self.assertIsNone(e.enrich("TSLA"))

    def test_overbought(self):
        s = _AVStub(rsi=[(date(2026, 10, 1), 75.0)])
        got = AlphaVantageEnricher(s).enrich("TSLA")
        self.assertIn("RSI 75 (overbought)", got)

    def test_oversold(self):
        s = _AVStub(rsi=[(date(2026, 10, 1), 25.0)])
        got = AlphaVantageEnricher(s).enrich("TSLA")
        self.assertIn("oversold", got)

    def test_macd_bullish_crossover(self):
        s = _AVStub(macd=[
            (date(2026, 9, 30), {"macd": 1.0, "signal": 1.1, "hist": -0.1}),
            (date(2026, 10, 1), {"macd": 1.2, "signal": 1.0, "hist": 0.2}),
        ])
        got = AlphaVantageEnricher(s).enrich("TSLA")
        self.assertIn("MACD bullish crossover", got)

    def test_macd_bearish_crossover(self):
        s = _AVStub(macd=[
            (date(2026, 9, 30), {"macd": 1.2, "signal": 1.0, "hist": 0.2}),
            (date(2026, 10, 1), {"macd": 1.0, "signal": 1.1, "hist": -0.1}),
        ])
        got = AlphaVantageEnricher(s).enrich("TSLA")
        self.assertIn("MACD bearish crossover", got)


# --------------------------------------------------------------------------
# Tiingo
# --------------------------------------------------------------------------

class _TiingoStub:
    def __init__(self, news=None, raise_exc=None):
        self._news = news or []
        self._raise = raise_exc

    def get_news(self, symbols, limit=10):
        if self._raise:
            raise self._raise
        return self._news


class TestTiingo(unittest.TestCase):
    def test_none_client(self):
        self.assertIsNone(TiingoEnricher(None).enrich("TSLA"))

    def test_no_news(self):
        self.assertIsNone(TiingoEnricher(_TiingoStub(news=[])).enrich("TSLA"))

    def test_news_with_source(self):
        news = [{"title": "Tesla cuts prices in China", "source": "Reuters"}]
        got = TiingoEnricher(_TiingoStub(news=news)).enrich("TSLA")
        self.assertIn("Tesla cuts prices", got)
        self.assertIn("Reuters", got)

    def test_news_without_source(self):
        news = [{"title": "Something happened"}]
        got = TiingoEnricher(_TiingoStub(news=news)).enrich("TSLA")
        self.assertIn("Something happened", got)

    def test_long_title_truncated(self):
        news = [{"title": "x" * 500}]
        got = TiingoEnricher(_TiingoStub(news=news)).enrich("TSLA")
        self.assertTrue(len(got) < 200)
        self.assertIn("…", got)


# --------------------------------------------------------------------------
# Polygon
# --------------------------------------------------------------------------

class _PolyStub:
    def __init__(self, snap=None, raise_exc=None):
        self._snap = snap
        self._raise = raise_exc

    def get_ticker_snapshot(self, symbol):
        if self._raise:
            raise self._raise
        return self._snap


class TestPolygon(unittest.TestCase):
    def test_none_client(self):
        self.assertIsNone(PolygonEnricher(None).enrich("TSLA"))

    def test_no_snapshot(self):
        self.assertIsNone(PolygonEnricher(_PolyStub(snap=None)).enrich("TSLA"))

    def test_full_snapshot(self):
        snap = {
            "day": {"c": 253.4, "h": 255.0, "l": 248.0, "v": 1_200_000},
            "prevDay": {"c": 248.0},
        }
        got = PolygonEnricher(_PolyStub(snap=snap)).enrich("TSLA")
        self.assertIn("$253.40", got)
        self.assertIn("+2.18%", got)
        self.assertIn("Vol 1.2M", got)

    def test_no_prev_close(self):
        snap = {"day": {"c": 100.0}}
        got = PolygonEnricher(_PolyStub(snap=snap)).enrich("TSLA")
        self.assertIn("$100.00", got)
        self.assertNotIn("%", got)


# --------------------------------------------------------------------------
# Composite
# --------------------------------------------------------------------------

class TestComposite(unittest.TestCase):
    def test_empty_subs(self):
        self.assertIsNone(CompositeEnricher([]).enrich("TSLA"))

    def test_all_none_subs(self):
        self.assertIsNone(CompositeEnricher([None, None]).enrich("TSLA"))

    def test_filters_empty_results(self):
        class _Empty:
            def enrich(self, s): return None
        class _OK:
            def enrich(self, s): return "📊 X: hello"
        c = CompositeEnricher([_Empty(), _OK(), _Empty()])
        got = c.enrich("TSLA")
        self.assertIn("📊 X: hello", got)
        self.assertTrue(got.startswith("— Research"))

    def test_sub_that_raises_is_skipped(self):
        class _Raises:
            def enrich(self, s): raise RuntimeError("boom")
        class _OK:
            def enrich(self, s): return "a"
        c = CompositeEnricher([_Raises(), _OK()])
        got = c.enrich("TSLA")
        self.assertEqual(got, "— Research:\na")  # D-0071 shortened header

    def test_order_preserved(self):
        class _A:
            def enrich(self, s): return "A"
        class _B:
            def enrich(self, s): return "B"
        class _C:
            def enrich(self, s): return "C"
        got = CompositeEnricher([_A(), _B(), _C()]).enrich("TSLA")
        self.assertEqual(got.split("\n")[1:], ["A", "B", "C"])


if __name__ == "__main__":
    unittest.main()
