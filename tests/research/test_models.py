import unittest
from datetime import datetime, timezone

from research.models import (
    CapitolTradesRecord, ComparisonRecord, Confidence, EvidenceSource,
    Finding, FindingLabel, ResearchOperation, ResearchReport,
)


NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


class TestEvidenceSource(unittest.TestCase):
    def test_valid(self):
        s = EvidenceSource(url="https://x", title="X", retrieval_timestamp=NOW)
        self.assertEqual(s.url, "https://x")

    def test_empty_url_rejected(self):
        with self.assertRaises(ValueError):
            EvidenceSource(url="", title="X", retrieval_timestamp=NOW)

    def test_naive_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            EvidenceSource(url="https://x", title="X",
                           retrieval_timestamp=datetime(2026, 1, 1))


class TestFinding(unittest.TestCase):
    def test_valid_no_sources(self):
        f = Finding(label=FindingLabel.FACT, text="something")
        self.assertEqual(f.label, FindingLabel.FACT)
        self.assertEqual(f.supporting_sources, ())

    def test_empty_text_rejected(self):
        with self.assertRaises(ValueError):
            Finding(label=FindingLabel.FACT, text="   ")


class TestResearchReport(unittest.TestCase):
    def _valid(self, **overrides):
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
            model_served="openai/gpt-6-luna",
            generated_at=NOW,
        )
        base.update(overrides)
        return ResearchReport(**base)

    def test_valid(self):
        self._valid()

    def test_missing_report_id(self):
        with self.assertRaises(ValueError):
            self._valid(report_id="")

    def test_naive_generated_at(self):
        with self.assertRaises(ValueError):
            self._valid(generated_at=datetime(2026, 1, 1))


class TestCapitolTradesRecord(unittest.TestCase):
    def test_valid(self):
        r = CapitolTradesRecord(
            record_id="ct-1", politician="Ro Khanna", security_name="Apple",
            ticker="AAPL", transaction_type="buy",
            disclosed_trade_date=NOW, publication_date=NOW,
            transaction_size="$1K-$15K",
            source_url="https://ct/x",
            extraction_timestamp=NOW,
            parse_confidence=Confidence.HIGH,
            validation_status="unvalidated",
        )
        self.assertEqual(r.ticker, "AAPL")

    def test_naive_publication_rejected(self):
        with self.assertRaises(ValueError):
            CapitolTradesRecord(
                record_id="ct-1", politician="Ro", security_name="X",
                ticker="X", transaction_type="buy",
                disclosed_trade_date=None,
                publication_date=datetime(2026, 1, 1),
                transaction_size=None,
                source_url="https://x", extraction_timestamp=NOW,
                parse_confidence=Confidence.LOW,
                validation_status="unvalidated",
            )


class TestComparisonRecord(unittest.TestCase):
    def test_valid(self):
        c = ComparisonRecord(
            comparison_id="cmp-1", ticker="TSLA",
            capitol_trades_records=(), perplexity_reports=(),
            overlap=(), contradictions=(), missing=(),
            hypothesis=None, confidence=Confidence.LOW,
            suggested_experiment=None, synthesized_at=NOW,
        )
        self.assertEqual(c.ticker, "TSLA")

    def test_missing_ticker(self):
        with self.assertRaises(ValueError):
            ComparisonRecord(
                comparison_id="cmp-1", ticker="",
                capitol_trades_records=(), perplexity_reports=(),
                overlap=(), contradictions=(), missing=(),
                hypothesis=None, confidence=Confidence.LOW,
                suggested_experiment=None, synthesized_at=NOW,
            )


if __name__ == "__main__":
    unittest.main()
