"""Tests for SymbolResearchHub.collect() (P-068, 2026-11-22).

No test file existed for this module before -- the real incident ("THE
HANG", 2026-10-06) froze the engine while the market was open because
`collect()`'s `with ThreadPoolExecutor(...)` blocked on `__exit__`'s
`shutdown(wait=True)` even after `fut.result(timeout=...)` had already
raised TimeoutError on a hung source. This file is the first coverage
of that exact mechanism.
"""
import threading
import time
import unittest

from engine.research_hub import SymbolResearchHub


class _HungFinnhub:
    """Blocks for 2s (standing in for "never returns" from the
    perspective of the short deadline used below), then returns
    normally -- so the test process itself never hangs at exit."""

    def basic_financials(self, symbol):
        threading.Event().wait(timeout=2.0)
        return {}

    def earnings_calendar(self, symbol, start, end):
        return {}


class TestCollectDoesNotHangOnAStuckSource(unittest.TestCase):
    def test_collect_returns_well_before_a_hung_source_finishes(self):
        hub = SymbolResearchHub(finnhub=_HungFinnhub(), max_workers=1, deadline_seconds=0.05)
        start = time.monotonic()
        research = hub.collect("AAPL")
        elapsed = time.monotonic() - start

        self.assertLess(elapsed, 1.0)  # comfortably under the stub's 2s block
        self.assertTrue(
            any(f.startswith("finnhub:") for f in research.sources_failed),
            research.sources_failed,
        )


if __name__ == "__main__":
    unittest.main()
