"""D-0086 — the public congressional-trade dataset as the replacement
political source.

The paid provider's key expired and will not be renewed (P-079), so the
political component scored 0.0 for EVERY candidate: 15 of the 90
weighted points unreachable, and the D-0058 reserved slot with nothing
to reserve.

The join is what these tests guard. `politicians.lookup()` matches a
normalized EXACT name and the dataset spells people differently --
Rohit Khanna, Daniel S Sullivan, Thomas H Tuberville, A. Mitchell
McConnell. Emitting the dataset's spelling would leave a source that
looks wired, fetches real rows, and contributes nothing, because every
row would be dropped by the whitelist filter. That is exactly how the
eDisclosure adapter failed (P-074), and it failed silently for weeks.
"""

from __future__ import annotations

import json
import unittest
from datetime import date

from research.congress_dataset_source import (
    CongressDatasetSource, FILER_ID_TO_WHITELIST_NAME, HttpResponse,
    NON_EQUITY_TICKERS,
)
from research.political_aggregator import PoliticalAggregator
from research.politicians import WHITELIST, lookup


def _row(**kw):
    base = {
        "ticker": "NVDA",
        "transaction_type": "Purchase",
        "transaction_date": "2026-09-18",
        "filing_date": "2026-10-05",
        "amount_range_label": "$15,001 - $50,000",
        "chamber": "House",
        "doc_url": "https://example.invalid/doc",
    }
    base.update(kw)
    return base


class _Stub:
    """Serves one body for every filer unless `per_filer` says otherwise."""

    def __init__(self, rows=None, status=200, per_filer=None, raises=None):
        self._rows = rows if rows is not None else [_row()]
        self._status = status
        self._per_filer = per_filer or {}
        self._raises = raises
        self.urls = []

    def __call__(self, url, headers, timeout):
        self.urls.append(url)
        if self._raises is not None:
            raise self._raises
        for key, (status, rows) in self._per_filer.items():
            if f"/{key}.json" in url:
                return HttpResponse(status, json.dumps(rows).encode())
        return HttpResponse(self._status, json.dumps(self._rows).encode())


class TestTheNameJoinSurvivesTheWhitelist(unittest.TestCase):
    """The single most important property: every name this source emits
    must be one `lookup()` recognises, or the whole integration is a
    silent no-op."""

    def test_every_mapped_name_is_on_the_whitelist(self):
        for filer_id, name in FILER_ID_TO_WHITELIST_NAME.items():
            with self.subTest(filer_id=filer_id):
                self.assertIsNotNone(
                    lookup(name),
                    msg=f"{name!r} (from {filer_id}) is not on the "
                        f"whitelist, so every one of this filer's trades "
                        f"would be dropped by the aggregator")

    def test_the_datasets_own_spellings_would_NOT_have_matched(self):
        """Proves the mapping is load-bearing rather than decorative."""
        for raw in ("Rohit Khanna", "Daniel S Sullivan",
                    "Thomas H Tuberville", "A. Mitchell McConnell"):
            with self.subTest(raw=raw):
                self.assertIsNone(
                    lookup(raw),
                    msg=f"{raw!r} now resolves, so this test no longer "
                        f"proves anything -- recheck the join")

    def test_fourteen_of_the_fifteen_are_covered(self):
        mapped = set(FILER_ID_TO_WHITELIST_NAME.values())
        missing = {p.name for p in WHITELIST} - mapped
        self.assertEqual(len(mapped), 14)
        self.assertEqual(missing, {"Chuck Schumer"},
                         msg="the dataset's filer index carries no record "
                             "for Schumer; any OTHER name going missing "
                             "is a regression in the mapping")

    def test_no_two_filers_map_to_the_same_person(self):
        names = list(FILER_ID_TO_WHITELIST_NAME.values())
        self.assertEqual(len(names), len(set(names)),
                         "a duplicate would double-count one politician "
                         "and manufacture a cluster out of one buyer")

    def test_the_green_and_mullin_near_misses_are_right(self):
        """The index also holds Marjorie Taylor Greene, Al Green, and a
        second Mullin record with zero purchases. Picking either wrong
        would score trades we never approved tracking."""
        self.assertEqual(FILER_ID_TO_WHITELIST_NAME["house_markdr_green"],
                         "Mark Green")
        self.assertEqual(
            FILER_ID_TO_WHITELIST_NAME["senate_markwayne_mullin"],
            "Markwayne Mullin")
        self.assertNotIn("oge_markwayne_mullin", FILER_ID_TO_WHITELIST_NAME)


class TestRowsBecomeTrades(unittest.TestCase):
    def _one(self, **kw):
        src = CongressDatasetSource(
            transport=_Stub(rows=[_row(**kw)]),
            filer_ids={"house_rohit_khanna": "Ro Khanna"})
        return src.fetch()

    def test_a_purchase_maps_with_our_name_and_both_dates(self):
        trades = self._one()
        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertEqual(t.politician_name, "Ro Khanna")
        self.assertEqual(t.ticker, "NVDA")
        self.assertEqual(t.action, "BUY")
        self.assertEqual(t.trade_date, date(2026, 9, 18))
        self.assertEqual(t.report_date, date(2026, 10, 5))

    def test_a_sale_maps_to_SELL(self):
        for raw in ("Sale (Full)", "Sale (Partial)", "Sale"):
            with self.subTest(raw=raw):
                trades = self._one(transaction_type=raw)
                self.assertEqual(trades[0].action, "SELL")

    def test_treasuries_and_placeholders_are_dropped(self):
        for ticker in sorted(NON_EQUITY_TICKERS):
            with self.subTest(ticker=ticker):
                self.assertEqual(self._one(ticker=ticker), [])

    def test_a_row_with_no_usable_date_is_dropped_not_guessed(self):
        self.assertEqual(self._one(transaction_date=None), [])
        self.assertEqual(self._one(transaction_date="not-a-date"), [])

    def test_an_exchange_is_neither_a_buy_nor_a_sell(self):
        self.assertEqual(self._one(transaction_type="Exchange"), [])

    def test_the_ticker_is_normalised(self):
        self.assertEqual(self._one(ticker=" nvda ")[0].ticker, "NVDA")


class TestItFailsOpenPerFiler(unittest.TestCase):
    def test_one_broken_filer_does_not_lose_the_others(self):
        src = CongressDatasetSource(
            transport=_Stub(per_filer={
                "house_rohit_khanna": (404, []),
                "house_nancy_pelosi": (200, [_row()]),
            }),
            filer_ids={"house_rohit_khanna": "Ro Khanna",
                       "house_nancy_pelosi": "Nancy Pelosi"})
        trades = src.fetch()
        self.assertEqual([t.politician_name for t in trades],
                         ["Nancy Pelosi"])

    def test_a_total_outage_returns_nothing_and_raises_nothing(self):
        src = CongressDatasetSource(
            transport=_Stub(raises=OSError("network down")),
            filer_ids={"house_rohit_khanna": "Ro Khanna"})
        self.assertEqual(src.fetch(), [])

    def test_the_report_makes_a_dead_source_VISIBLE(self):
        """A source that quietly stops working must not look like a
        quiet month in Congress -- that is P-074's failure mode."""
        src = CongressDatasetSource(
            transport=_Stub(raises=OSError("network down")),
            filer_ids={"house_rohit_khanna": "Ro Khanna"})
        src.fetch()
        self.assertFalse(src.last_report.healthy())
        self.assertEqual(src.last_report.filers_succeeded, 0)
        self.assertEqual(len(src.last_report.failures), 1)

    def test_a_healthy_fetch_reports_healthy(self):
        src = CongressDatasetSource(
            transport=_Stub(rows=[_row()]),
            filer_ids={"house_rohit_khanna": "Ro Khanna"})
        src.fetch()
        self.assertTrue(src.last_report.healthy())
        self.assertEqual(src.last_report.trades, 1)


class TestItReachesTheAggregator(unittest.TestCase):
    def test_trades_survive_the_aggregators_whitelist_filter(self):
        """End to end, the property the whole decision rests on."""
        src = CongressDatasetSource(
            transport=_Stub(rows=[_row()]),
            filer_ids={"house_rohit_khanna": "Ro Khanna"})
        agg = PoliticalAggregator(congress_dataset=src)
        trades = agg.fetch_all()
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].politician_name, "Ro Khanna")

    def test_a_non_whitelisted_person_is_still_dropped(self):
        src = CongressDatasetSource(
            transport=_Stub(rows=[_row()]),
            filer_ids={"house_someone_else": "Someone Else"})
        self.assertEqual(PoliticalAggregator(congress_dataset=src).fetch_all(),
                         [])

    def test_the_same_trade_from_two_sources_is_counted_once(self):
        """Dedup is what stops a symbol looking like a two-person
        cluster when it is one person reported twice."""
        a = CongressDatasetSource(
            transport=_Stub(rows=[_row()]),
            filer_ids={"house_rohit_khanna": "Ro Khanna"})
        b = CongressDatasetSource(
            transport=_Stub(rows=[_row()]),
            filer_ids={"house_rohit_khanna": "Ro Khanna"})

        class _Twice:
            def fetch(self):
                return a.fetch() + b.fetch()

        self.assertEqual(
            len(PoliticalAggregator(congress_dataset=_Twice()).fetch_all()), 1)


if __name__ == "__main__":
    unittest.main()
