"""Tests for the multi-source political aggregator (B.22)."""

import unittest
from datetime import date, datetime
from research.political_aggregator import (
    PoliticalAggregator, PoliticalTrade,
    from_quiverquant, from_capitol_trades,
    _normalize_action, _parse_iso_date,
)


class _QQStub:
    def __init__(self, rows): self._rows = rows
    def recent_congress_trades(self): return self._rows


class _CTStub:
    def __init__(self, records): self._r = records
    def fetch(self): return self._r


class _Rec:
    def __init__(self, ticker, action, trade_date):
        self.ticker = ticker
        self.action = action
        self.trade_date = trade_date
        self.size = "$1,001 - $15,000"
        self.source_url = None


class TestNormalization(unittest.TestCase):
    def test_action_normalization(self):
        self.assertEqual(_normalize_action("Purchase"), "BUY")
        self.assertEqual(_normalize_action("Sale (Full)"), "SELL")
        self.assertEqual(_normalize_action("P"), "BUY")
        self.assertIsNone(_normalize_action("other"))

    def test_date_parse(self):
        self.assertEqual(_parse_iso_date("2026-10-02"), date(2026, 10, 2))
        self.assertIsNone(_parse_iso_date("bad"))
        self.assertIsNone(_parse_iso_date(None))


class TestQuiverQuantAdapter(unittest.TestCase):
    def test_basic_row_mapped(self):
        rows = [{
            "Representative": "Nancy Pelosi",
            "Transaction": "Purchase",
            "Ticker": "NVDA",
            "Range": "$1,000,001 - $5,000,000",
            "TransactionDate": "2026-09-28",
            "Chamber": "House",
        }]
        got = from_quiverquant(_QQStub(rows))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].ticker, "NVDA")
        self.assertEqual(got[0].action, "BUY")
        self.assertEqual(got[0].trade_date, date(2026, 9, 28))
        self.assertEqual(got[0].source, "quiverquant")

    def test_malformed_row_skipped(self):
        rows = [
            {"Representative": "X"},   # missing ticker
            "not a dict",
            {"Representative": "Y", "Ticker": "Z"},  # missing date / action
        ]
        got = from_quiverquant(_QQStub(rows))
        self.assertEqual(got, [])

    def test_none_client_returns_empty(self):
        self.assertEqual(from_quiverquant(None), [])


class TestCapitolAdapter(unittest.TestCase):
    def test_records_mapped(self):
        rec = _Rec("TSLA", "P", datetime(2026, 9, 20))
        got = from_capitol_trades({"Ro Khanna": _CTStub([rec])})
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].ticker, "TSLA")
        self.assertEqual(got[0].politician_name, "Ro Khanna")

    def test_empty_scrapers_returns_empty(self):
        self.assertEqual(from_capitol_trades({}), [])


class TestAggregator(unittest.TestCase):
    def test_dedup_and_filter(self):
        agg = PoliticalAggregator()
        pelosi_qq = PoliticalTrade(
            politician_name="Nancy Pelosi", ticker="NVDA", action="BUY",
            trade_date=date(2026, 9, 28), report_date=None,
            size_range=None, chamber="House", source="quiverquant")
        pelosi_ct = PoliticalTrade(
            politician_name="Nancy Pelosi", ticker="NVDA", action="BUY",
            trade_date=date(2026, 9, 28), report_date=None,
            size_range=None, chamber="House", source="capitoltrades")
        random = PoliticalTrade(
            politician_name="Random Senator", ticker="MSFT", action="BUY",
            trade_date=date(2026, 9, 28), report_date=None,
            size_range=None, chamber="Senate", source="quiverquant")
        got = agg._dedup_and_filter([pelosi_qq, pelosi_ct, random])
        # Pelosi deduped to 1, Random non-whitelisted dropped
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].politician_name, "Nancy Pelosi")

    def test_fetch_all_runs_sources_in_parallel(self):
        rows = [{
            "Representative": "Nancy Pelosi", "Transaction": "P",
            "Ticker": "NVDA", "TransactionDate": "2026-09-28",
            "Chamber": "House",
        }]
        agg = PoliticalAggregator(quiverquant=_QQStub(rows))
        got = agg.fetch_all()
        self.assertEqual(len(got), 1)

    def test_all_sources_down_returns_empty(self):
        agg = PoliticalAggregator()  # no sources
        self.assertEqual(agg.fetch_all(), [])


if __name__ == "__main__":
    unittest.main()
