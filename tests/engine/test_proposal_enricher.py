"""Tests for proposal_enricher (D-0050 Phase 2)."""

import unittest
from dataclasses import dataclass
from typing import Tuple

from engine.proposal_enricher import ProposalEnricher, append_enrichment


@dataclass
class _Finding:
    summary: str


@dataclass
class _Report:
    findings: Tuple[_Finding, ...]


class _StubClient:
    def __init__(self, report=None, *, raise_exc=None):
        self._report = report
        self._raise = raise_exc
        self.calls = []

    def research_stock(self, ticker, question):
        self.calls.append((ticker, question))
        if self._raise is not None:
            raise self._raise
        return self._report


class TestProposalEnricher(unittest.TestCase):
    def test_none_client_returns_none(self):
        self.assertIsNone(ProposalEnricher(None).enrich("TSLA"))

    def test_client_raises_returns_none(self):
        e = ProposalEnricher(_StubClient(raise_exc=RuntimeError("x")))
        self.assertIsNone(e.enrich("TSLA"))

    def test_valid_findings_rendered_as_bullets(self):
        rep = _Report(findings=(
            _Finding(summary="Beat Q3 earnings"),
            _Finding(summary="Analyst upgrade from Morgan"),
        ))
        got = ProposalEnricher(_StubClient(report=rep)).enrich("TSLA")
        self.assertIn("Beat Q3 earnings", got)
        self.assertIn("Analyst upgrade", got)
        self.assertEqual(got.count("•"), 2)

    def test_empty_findings_returns_none(self):
        rep = _Report(findings=())
        self.assertIsNone(ProposalEnricher(_StubClient(report=rep)).enrich("TSLA"))

    def test_truncates_long_output(self):
        long_summary = "x" * 1000
        rep = _Report(findings=(_Finding(summary=long_summary),))
        got = ProposalEnricher(_StubClient(report=rep)).enrich("TSLA")
        self.assertLessEqual(len(got), 400)
        self.assertTrue(got.endswith("…"))

    def test_append_enrichment_none_returns_message_unchanged(self):
        self.assertEqual(append_enrichment("hello", None), "hello")
        self.assertEqual(append_enrichment("hello", ""), "hello")

    def test_append_enrichment_adds_divider(self):
        """append_enrichment adds a blank-line separator; the enrichment
        itself supplies its own header (CompositeEnricher does)."""
        out = append_enrichment("hello", "— Research (advisory):\n• news")
        self.assertIn("hello", out)
        self.assertIn("— Research (advisory):", out)
        self.assertIn("• news", out)
        # blank line between base message and enrichment
        self.assertIn("hello\n\n—", out)


if __name__ == "__main__":
    unittest.main()
