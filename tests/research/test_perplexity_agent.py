import json
import unittest
from typing import List

from research.models import Confidence, FindingLabel, ResearchOperation
from research.perplexity_agent import (
    HttpResponse, PerplexityAgentClient, PerplexityConfigError,
    PerplexityMigrationRequiredError, PerplexityTransportError,
)


def _fake_response(text: str, *, model: str = "openai/gpt-6-luna",
                   annotations=None) -> bytes:
    content = [{"type": "output_text", "text": text}]
    if annotations:
        content[0]["annotations"] = annotations
    payload = {
        "id": "resp_1",
        "model": model,
        "output": [{"content": content}],
        "usage": {"input_tokens": 5, "output_tokens": 5},
        "status": "completed",
    }
    return json.dumps(payload).encode()


class _RecordingTransport:
    def __init__(self, responses: List[HttpResponse]) -> None:
        self.responses = list(responses)
        self.calls: List[dict] = []

    def __call__(self, url, data, headers, timeout):
        self.calls.append({"url": url, "data": data,
                           "headers": dict(headers), "timeout": timeout})
        return self.responses.pop(0)


class TestConfig(unittest.TestCase):
    def test_missing_key(self):
        with self.assertRaises(PerplexityConfigError):
            PerplexityAgentClient(api_key="")

    def test_no_preset_no_model(self):
        with self.assertRaises(PerplexityConfigError):
            PerplexityAgentClient(api_key="k", preset=None, model=None)


class TestHappyPath(unittest.TestCase):
    def test_basic_response_parses_all_sections(self):
        answer = ("SUMMARY: TSLA is volatile with EV headwinds.\n"
                  "FINDINGS:\n"
                  "- FACT Q2 deliveries missed estimates by 5%.\n"
                  "- HYPOTHESIS Margin compression may continue.\n"
                  "- SOURCE https://x reports declining ASP.\n"
                  "RISKS:\n"
                  "- Rate-cut delay could reduce discretionary spend.\n"
                  "CONFIDENCE: MEDIUM\n"
                  "RECOMMENDATION: Hold and monitor Q3 print.\n"
                  "SUGGESTED_EXPERIMENT: Compare with F and GM YoY delta.\n")
        transport = _RecordingTransport([
            HttpResponse(200, _fake_response(
                answer,
                annotations=[{"url": "https://x", "title": "X report"}],
            )),
        ])
        client = PerplexityAgentClient(api_key="k", transport=transport,
                                       sleep_fn=lambda s: None)
        report = client.research_stock("TSLA", "State of TSLA?")

        self.assertEqual(report.operation, ResearchOperation.RESEARCH_STOCK)
        self.assertEqual(report.subject_symbol, "TSLA")
        self.assertEqual(report.model_served, "openai/gpt-6-luna")
        self.assertEqual(report.confidence, Confidence.MEDIUM)
        self.assertIn("volatile", report.summary)
        labels = [f.label for f in report.findings]
        self.assertIn(FindingLabel.FACT, labels)
        self.assertIn(FindingLabel.HYPOTHESIS, labels)
        self.assertIn(FindingLabel.SOURCE, labels)
        self.assertEqual(len(report.risks), 1)
        self.assertEqual(report.recommendation, "Hold and monitor Q3 print.")
        self.assertEqual(len(report.sources), 1)
        self.assertEqual(report.sources[0].url, "https://x")

    def test_payload_uses_preset_by_default(self):
        transport = _RecordingTransport([
            HttpResponse(200, _fake_response("SUMMARY: ok\nCONFIDENCE: LOW\n")),
        ])
        client = PerplexityAgentClient(api_key="k", transport=transport,
                                       sleep_fn=lambda s: None)
        client.research_market("What is up?")
        sent = json.loads(transport.calls[0]["data"].decode())
        self.assertEqual(sent["preset"], "medium")
        self.assertNotIn("model", sent)

    def test_explicit_model_overrides_preset(self):
        transport = _RecordingTransport([
            HttpResponse(200, _fake_response("SUMMARY: ok\nCONFIDENCE: LOW\n",
                                             model="openai/gpt-5.6-terra")),
        ])
        client = PerplexityAgentClient(api_key="k",
                                       model="openai/gpt-5.6-terra",
                                       transport=transport,
                                       sleep_fn=lambda s: None)
        client.research_strategy("Momentum vs mean-reversion?")
        sent = json.loads(transport.calls[0]["data"].decode())
        self.assertEqual(sent["model"], "openai/gpt-5.6-terra")
        self.assertNotIn("preset", sent)


class TestErrors(unittest.TestCase):
    def test_migration_required_is_critical(self):
        body = json.dumps({"error": {"code": "agent_api_migration_required",
                                     "message": "migrate"}}).encode()
        transport = _RecordingTransport([HttpResponse(400, body)])
        client = PerplexityAgentClient(api_key="k", transport=transport,
                                       sleep_fn=lambda s: None)
        with self.assertRaises(PerplexityMigrationRequiredError):
            client.research_market("Q?")

    def test_permanent_401_raises(self):
        body = json.dumps({"error": {"message": "bad key"}}).encode()
        transport = _RecordingTransport([HttpResponse(401, body)])
        client = PerplexityAgentClient(api_key="k", transport=transport,
                                       sleep_fn=lambda s: None)
        with self.assertRaises(PerplexityTransportError):
            client.research_market("Q?")

    def test_retries_on_500_then_succeeds(self):
        good = HttpResponse(200, _fake_response("SUMMARY: ok\nCONFIDENCE: LOW\n"))
        transport = _RecordingTransport([
            HttpResponse(500, b'{"error": {"message": "server"}}'),
            HttpResponse(500, b'{"error": {"message": "server"}}'),
            good,
        ])
        sleeps: list = []
        client = PerplexityAgentClient(api_key="k", transport=transport,
                                       max_attempts=3,
                                       sleep_fn=sleeps.append)
        report = client.research_market("Q?")
        self.assertEqual(report.confidence, Confidence.LOW)
        self.assertEqual(len(transport.calls), 3)
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_gives_up_after_max_attempts(self):
        transport = _RecordingTransport([
            HttpResponse(500, b"") for _ in range(3)
        ])
        client = PerplexityAgentClient(api_key="k", transport=transport,
                                       max_attempts=3,
                                       sleep_fn=lambda s: None)
        with self.assertRaises(PerplexityTransportError):
            client.research_market("Q?")


if __name__ == "__main__":
    unittest.main()
