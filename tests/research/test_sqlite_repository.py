import os
import tempfile
import unittest
from datetime import datetime, timezone

from persistence.db import bootstrap_schema, connect
from research.models import (
    CapitolTradesRecord, ComparisonRecord, Confidence, EvidenceSource,
    Finding, FindingLabel, ResearchOperation, ResearchReport,
)
from research.sqlite_repository import ResearchRepository


NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


def _report(report_id="rr-1", subject="TSLA"):
    return ResearchReport(
        report_id=report_id,
        operation=ResearchOperation.RESEARCH_STOCK,
        question="Q?",
        summary="summary text",
        findings=(Finding(label=FindingLabel.FACT, text="Fact 1"),),
        sources=(EvidenceSource(url="https://x", title="X",
                                retrieval_timestamp=NOW),),
        risks=("risk-1",),
        confidence=Confidence.HIGH,
        recommendation="rec",
        suggested_experiment="exp",
        model_served="openai/gpt-6-luna",
        generated_at=NOW,
        subject_symbol=subject,
    )


def _ct(record_id="ct-1", ticker="TSLA"):
    return CapitolTradesRecord(
        record_id=record_id, politician="Ro Khanna",
        security_name="Tesla Inc", ticker=ticker,
        transaction_type="buy", disclosed_trade_date=NOW,
        publication_date=NOW, transaction_size="$1K-$15K",
        source_url="https://x", extraction_timestamp=NOW,
        parse_confidence=Confidence.HIGH,
        validation_status="unvalidated",
    )


def _cmp(cmp_id="cmp-1"):
    return ComparisonRecord(
        comparison_id=cmp_id, ticker="TSLA",
        capitol_trades_records=(_ct(),),
        perplexity_reports=(_report(),),
        overlap=("o",), contradictions=("c",), missing=("m",),
        hypothesis="HYPOTHESIS: x", confidence=Confidence.LOW,
        suggested_experiment="e", synthesized_at=NOW,
    )


class TestResearchRepository(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "db.sqlite")
        self.conn = connect(self.path)
        bootstrap_schema(self.conn)
        self.addCleanup(self.conn.close)

    def test_round_trip_report(self):
        repo = ResearchRepository(self.conn)
        original = _report(subject="AAPL")
        repo.save_report(original)
        [restored] = repo.list_reports(subject_symbol="AAPL")
        self.assertEqual(restored.report_id, original.report_id)
        self.assertEqual(restored.confidence, Confidence.HIGH)
        self.assertEqual(restored.findings[0].label, FindingLabel.FACT)
        self.assertEqual(restored.sources[0].url, "https://x")

    def test_list_reports_filter(self):
        repo = ResearchRepository(self.conn)
        repo.save_report(_report(report_id="rr-a", subject="AAPL"))
        repo.save_report(_report(report_id="rr-b", subject="TSLA"))
        self.assertEqual(
            len(repo.list_reports(subject_symbol="AAPL")), 1)
        self.assertEqual(len(repo.list_reports()), 2)

    def test_capitol_trades_round_trip(self):
        repo = ResearchRepository(self.conn)
        n = repo.save_capitol_trades((_ct(record_id="ct-a"),
                                      _ct(record_id="ct-b", ticker="AAPL")))
        self.assertEqual(n, 2)
        tsla = repo.list_capitol_trades(ticker="TSLA")
        self.assertEqual(len(tsla), 1)
        self.assertEqual(tsla[0].record_id, "ct-a")

    def test_save_comparison_persists(self):
        repo = ResearchRepository(self.conn)
        repo.save_comparison(_cmp())
        cur = self.conn.execute(
            "SELECT ticker, hypothesis FROM research_comparisons")
        row = cur.fetchone()
        self.assertEqual(row[0], "TSLA")
        self.assertEqual(row[1], "HYPOTHESIS: x")


if __name__ == "__main__":
    unittest.main()
