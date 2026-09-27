import unittest

from research.capitol_trades_scraper import (
    CapitolTradesConfigError, CapitolTradesFetchError,
    CapitolTradesParserBrokenError, CapitolTradesScraper, HttpResponse,
    _find_action, _find_dates, _find_size, _find_ticker,
)
from research.models import Confidence


_FIXTURE_HTML = """
<html><body>
  <div class="q-trade-row">
    <span class="politician">Ro Khanna</span>
    <span class="issuer">Apple Inc</span>
    <span class="ticker">AAPL</span>
    <span class="publish">2026-09-25</span>
    <span class="disclosed">2026-09-20</span>
    <span class="action">buy</span>
    <span class="size">$1K - $15K</span>
  </div>
  <div class="q-trade-row">
    <span class="politician">Ro Khanna</span>
    <span class="issuer">Tesla Inc</span>
    <span class="ticker">TSLA</span>
    <span class="publish">2026-09-24</span>
    <span class="action">sell</span>
    <span class="size">$50K - $100K</span>
  </div>
</body></html>
"""


class _FixedTransport:
    def __init__(self, response: HttpResponse) -> None:
        self.response = response
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append({"url": url, "headers": dict(headers),
                           "timeout": timeout})
        return self.response


class TestPureHelpers(unittest.TestCase):
    def test_find_ticker_prefers_all_caps(self):
        self.assertEqual(_find_ticker("The AAPL move today"), "AAPL")

    def test_find_ticker_ignores_common_noise(self):
        self.assertIsNone(_find_ticker("USA USD"))

    def test_find_action_buy(self):
        self.assertEqual(_find_action("she made a buy"), "buy")

    def test_find_action_none(self):
        self.assertIsNone(_find_action("nothing here"))

    def test_find_size(self):
        self.assertEqual(_find_size("$1K - $15K disclosed"), "$1K - $15K")

    def test_find_dates_parses_iso(self):
        dates = _find_dates("published 2026-09-20 and 2026-09-25")
        self.assertEqual(len(dates), 2)
        self.assertEqual(dates[0].year, 2026)


class TestScraper(unittest.TestCase):
    def test_config_rejects_empty_slug(self):
        with self.assertRaises(CapitolTradesConfigError):
            CapitolTradesScraper(politician_slug="")

    def test_non_200_raises_fetch_error(self):
        s = CapitolTradesScraper(
            transport=_FixedTransport(HttpResponse(500, b"srv err")),
        )
        with self.assertRaises(CapitolTradesFetchError):
            s.fetch()

    def test_schema_broken_when_markers_missing(self):
        s = CapitolTradesScraper(
            transport=_FixedTransport(HttpResponse(200, b"<html>hello</html>")),
        )
        with self.assertRaises(CapitolTradesParserBrokenError):
            s.fetch()

    def test_fixture_parses_two_rows(self):
        s = CapitolTradesScraper(
            transport=_FixedTransport(
                HttpResponse(200, _FIXTURE_HTML.encode())),
        )
        records = s.fetch()
        self.assertEqual(len(records), 2)
        by_ticker = {r.ticker: r for r in records}
        self.assertIn("AAPL", by_ticker)
        self.assertIn("TSLA", by_ticker)
        self.assertEqual(by_ticker["AAPL"].transaction_type, "buy")
        self.assertEqual(by_ticker["TSLA"].transaction_type, "sell")
        self.assertEqual(by_ticker["AAPL"].politician, "Ro Khanna")
        self.assertIsNotNone(by_ticker["AAPL"].transaction_size)
        self.assertEqual(by_ticker["AAPL"].parse_confidence,
                         Confidence.HIGH)


if __name__ == "__main__":
    unittest.main()
