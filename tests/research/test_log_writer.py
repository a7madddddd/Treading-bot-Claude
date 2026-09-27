import os
import tempfile
import unittest
from datetime import datetime, timezone

from research.log_writer import ResearchLogWriter
from research.models import (
    ComparisonRecord, Confidence, EvidenceSource, Finding, FindingLabel,
    ResearchOperation, ResearchReport,
)


NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


def _report(report_id="rr-a", subject="TSLA"):
    return ResearchReport(
        report_id=report_id,
        operation=ResearchOperation.RESEARCH_STOCK,
        question=f"State of {subject}?",
        summary=f"{subject} looks stable near-term.",
        findings=(Finding(label=FindingLabel.FACT, text="Fact 1"),
                  Finding(label=FindingLabel.HYPOTHESIS, text="Hyp 1")),
        sources=(EvidenceSource(url="https://x", title="X",
                                retrieval_timestamp=NOW),),
        risks=("Macro tightening",),
        confidence=Confidence.MEDIUM,
        recommendation="Hold",
        suggested_experiment="Compare peers",
        model_served="openai/gpt-6-luna",
        generated_at=NOW,
        subject_symbol=subject,
    )


def _comparison(cmp_id="cmp-a"):
    return ComparisonRecord(
        comparison_id=cmp_id, ticker="TSLA",
        capitol_trades_records=(), perplexity_reports=(),
        overlap=("some overlap",), contradictions=(),
        missing=("no news",), hypothesis="HYPOTHESIS: stable",
        confidence=Confidence.MEDIUM,
        suggested_experiment="fill missing",
        synthesized_at=NOW,
    )


class TestLogWriter(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "research-log.md")

    def test_append_new_report(self):
        w = ResearchLogWriter(self.path)
        self.assertTrue(w.append_report(_report()))
        with open(self.path) as f:
            body = f.read()
        self.assertIn("rr-a", body)
        self.assertIn("**Confidence:** MEDIUM", body)
        self.assertIn("**FACT:** Fact 1", body)
        self.assertIn("[X](https://x)", body)

    def test_dedup_by_report_id(self):
        w = ResearchLogWriter(self.path)
        w.append_report(_report())
        self.assertFalse(w.append_report(_report()))

    def test_append_comparison(self):
        w = ResearchLogWriter(self.path)
        self.assertTrue(w.append_comparison(_comparison()))
        with open(self.path) as f:
            body = f.read()
        self.assertIn("cmp-a", body)
        self.assertIn("HYPOTHESIS: stable", body)

    def test_creates_parent_dir(self):
        deep = os.path.join(self._tmp.name, "a", "b", "log.md")
        w = ResearchLogWriter(deep)
        w.append_report(_report())
        self.assertTrue(os.path.exists(deep))


if __name__ == "__main__":
    unittest.main()
