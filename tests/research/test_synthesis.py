import unittest
from datetime import datetime, timezone

from research.models import (
    CapitolTradesRecord, Confidence, ResearchOperation, ResearchReport,
)
from research.synthesis import synthesize


NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


def _report(**overrides):
    base = dict(
        report_id="rr-1",
        operation=ResearchOperation.RESEARCH_STOCK,
        question="What?",
        summary="ok",
        findings=(),
        sources=(),
        risks=(),
        confidence=Confidence.MEDIUM,
        recommendation=None,
        suggested_experiment=None,
        model_served="test",
        generated_at=NOW,
        subject_symbol="TSLA",
    )
    base.update(overrides)
    return ResearchReport(**base)


def _ct(ticker: str, action: str, **overrides):
    base = dict(
        record_id="ct-1", politician="Ro Khanna", security_name="X",
        ticker=ticker, transaction_type=action,
        disclosed_trade_date=NOW, publication_date=NOW,
        transaction_size=None, source_url="https://x",
        extraction_timestamp=NOW,
        parse_confidence=Confidence.HIGH,
        validation_status="unvalidated",
    )
    base.update(overrides)
    return CapitolTradesRecord(**base)


class TestSynthesize(unittest.TestCase):
    def test_no_data_low_confidence(self):
        c = synthesize(ticker="TSLA", perplexity_reports=[],
                       capitol_trades_records=[])
        self.assertEqual(c.confidence, Confidence.LOW)
        self.assertIn("no Perplexity report available for this ticker",
                      c.missing)
        self.assertIn("no CapitolTrades disclosure available for this ticker",
                      c.missing)

    def test_perplexity_only_medium(self):
        c = synthesize(ticker="TSLA",
                       perplexity_reports=[_report(summary="TSLA looks weak")],
                       capitol_trades_records=[])
        self.assertEqual(c.confidence, Confidence.MEDIUM)
        self.assertTrue(c.hypothesis.startswith("HYPOTHESIS:"))

    def test_overlap_detected_boosts_confidence(self):
        report = _report(summary="Analysts expect a further sell on TSLA")
        ct = _ct("TSLA", "sell")
        c = synthesize(ticker="TSLA", perplexity_reports=[report],
                       capitol_trades_records=[ct])
        self.assertEqual(c.confidence, Confidence.HIGH)
        self.assertTrue(any("sell" in o for o in c.overlap))

    def test_contradiction_flags_low(self):
        report = _report(summary="Neutral view", recommendation="Sell")
        ct = _ct("TSLA", "buy")
        c = synthesize(ticker="TSLA", perplexity_reports=[report],
                       capitol_trades_records=[ct])
        self.assertEqual(c.confidence, Confidence.LOW)
        self.assertEqual(len(c.contradictions), 1)
        self.assertIsNotNone(c.suggested_experiment)

    def test_ticker_case_normalized(self):
        c = synthesize(ticker="tsla", perplexity_reports=[],
                       capitol_trades_records=[_ct("TSLA", "buy")])
        self.assertEqual(c.ticker, "TSLA")
        self.assertEqual(len(c.capitol_trades_records), 1)


if __name__ == "__main__":
    unittest.main()
