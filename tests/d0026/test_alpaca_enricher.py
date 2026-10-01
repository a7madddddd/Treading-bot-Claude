"""Tests for AlpacaFeatureEnricher (B24)."""

import json
import unittest
from datetime import date

from d0026.alpaca_enricher import (
    AlpacaFeatureEnricher, AlpacaFeatureEnricherConfigError, HttpResponse,
    _mean_dollar_volume, _mean_range_over_vwap, _mean_true_range,
    _return_over,
)
from d0026.identity import (
    IdentityConfidence, IdentityResolution, ResolutionOutcome,
    SecurityIdentity, TickerAlias,
)
from d0026.models import (
    RawCandidateRef, RegimeLabel, RegimeState, UniverseCandidate,
)


DATE = date(2026, 1, 5)


def _regime():
    return RegimeState(
        as_of_date=DATE, label=RegimeLabel.UNCLASSIFIED_PENDING_CALIBRATION,
        reference_series_values=(("vix_percentile", 0.5),),
        classification_method_version="t",
    )


def _cand(ticker="AAPL"):
    identity = SecurityIdentity(
        security_id=f"sec-{ticker}", display_name=ticker, cik="0",
        confidence=IdentityConfidence.RESOLVED_CIK,
    )
    alias = TickerAlias(security_id=identity.security_id, ticker=ticker,
                        effective_start=None, effective_end=None)
    resolution = IdentityResolution(
        outcome=ResolutionOutcome.RESOLVED, ticker=ticker,
        as_of_date=DATE, identity=identity, alias=alias,
    )
    return UniverseCandidate(
        raw=RawCandidateRef(ticker=ticker, as_of_date=DATE),
        identity_resolution=resolution, bar=None, features=None,
    )


class _StubTransport:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []
    def __call__(self, url, headers, timeout):
        self.calls.append(url)
        return self.resp


def _bars(n, *, close_start=100.0, vol=1_000_000):
    return {"bars": [
        {"c": close_start + i, "h": close_start + i + 1,
         "l": close_start + i - 1, "o": close_start + i,
         "v": vol, "vw": close_start + i}
        for i in range(n)
    ]}


class TestConfig(unittest.TestCase):
    def test_missing_creds(self):
        with self.assertRaises(AlpacaFeatureEnricherConfigError):
            AlpacaFeatureEnricher(key_id="", secret_key="s")

    def test_history_less_than_min_rejected(self):
        with self.assertRaises(AlpacaFeatureEnricherConfigError):
            AlpacaFeatureEnricher(key_id="k", secret_key="s",
                                  history_days=10, min_bars=20)


class TestPureMath(unittest.TestCase):
    def test_mean_dollar_volume(self):
        bars = [{"v": 100.0, "vw": 10.0},
                {"v": 200.0, "vw": 20.0}]
        # (1000 + 4000) / 2 = 2500
        self.assertEqual(_mean_dollar_volume(bars), 2500.0)

    def test_mean_dollar_volume_none_on_empty(self):
        self.assertIsNone(_mean_dollar_volume([]))

    def test_mean_true_range(self):
        # bars: [(h=10, l=9, c=9.5), (h=11, l=10, c=10.5)]
        # TR[0] = 10 - 9 = 1
        # TR[1] = max(11-10, |11-9.5|, |10-9.5|) = max(1, 1.5, 0.5) = 1.5
        # mean = 1.25
        bars = [{"h": 10.0, "l": 9.0, "c": 9.5},
                {"h": 11.0, "l": 10.0, "c": 10.5}]
        self.assertEqual(_mean_true_range(bars), 1.25)

    def test_return_over(self):
        # 30-bar return: (bars[-1].c - bars[-31].c) / bars[-31].c
        bars = [{"c": 100.0 + i} for i in range(35)]
        # last=134, past=104 (index -31 with 35 elements is index 4 → close=104)
        # (134 - 104) / 104
        r = _return_over(bars, 30)
        self.assertAlmostEqual(r, (134.0 - 104.0) / 104.0)

    def test_return_over_insufficient(self):
        bars = [{"c": 100.0} for _ in range(10)]
        self.assertIsNone(_return_over(bars, 30))

    def test_mean_range_over_vwap(self):
        # (10-9)/9.5 = 0.10526
        bars = [{"h": 10.0, "l": 9.0, "vw": 9.5, "c": 9.5}]
        r = _mean_range_over_vwap(bars)
        self.assertAlmostEqual(r, 1.0 / 9.5)


class TestEnricher(unittest.TestCase):
    def test_insufficient_bars_returns_unenriched(self):
        transport = _StubTransport(HttpResponse(200,
                                                json.dumps(_bars(5)).encode()))
        e = AlpacaFeatureEnricher(key_id="k", secret_key="s",
                                  transport=transport)
        c = _cand()
        result = e(c, DATE, _regime())
        self.assertIsNone(result.bar)
        self.assertIsNone(result.features)

    def test_enough_bars_enriches(self):
        transport = _StubTransport(HttpResponse(200,
                                                json.dumps(_bars(35)).encode()))
        e = AlpacaFeatureEnricher(key_id="k", secret_key="s",
                                  transport=transport)
        c = _cand()
        result = e(c, DATE, _regime())
        self.assertIsNotNone(result.bar)
        self.assertIsNotNone(result.features)
        self.assertTrue(result.features.warm_up_sufficient)
        self.assertIsNotNone(result.features.liquidity_measure)
        self.assertIsNotNone(result.features.atr_measure)
        self.assertIsNotNone(result.features.momentum_measure)
        self.assertIsNotNone(result.features.execution_quality_proxy)
        self.assertFalse(result.features.execution_quality_proxy_is_true_quote)

    def test_http_error_returns_unenriched(self):
        transport = _StubTransport(HttpResponse(429, b"rate-limit"))
        e = AlpacaFeatureEnricher(key_id="k", secret_key="s",
                                  transport=transport)
        c = _cand()
        result = e(c, DATE, _regime())
        # Never crashes; returns unenriched so Stage B rejects.
        self.assertIsNone(result.bar)
        self.assertIsNone(result.features)

    def test_url_contains_symbol_and_feed(self):
        transport = _StubTransport(HttpResponse(200,
                                                json.dumps(_bars(35)).encode()))
        e = AlpacaFeatureEnricher(key_id="k", secret_key="s",
                                  transport=transport, feed="iex")
        e(_cand("TSLA"), DATE, _regime())
        self.assertIn("/v2/stocks/TSLA/bars", transport.calls[0])
        self.assertIn("feed=iex", transport.calls[0])

    def test_sector_provider_appends_sector_fragment(self):
        """Regression for 2026-09-30 Bug #2: in production the enricher
        never consulted any sector provider so stage G's D-0048
        concentration cap was dormant. On the fix, a supplied
        SectorProvider's result is spliced into source_reference as
        '; sector=<name>' -- the exact shape the Concentration stage's
        own parser already recognizes."""
        from d0026.sector_provider import StaticSectorProvider
        provider = StaticSectorProvider({"TSLA": "consumer_discretionary"})
        transport = _StubTransport(HttpResponse(200,
                                                json.dumps(_bars(35)).encode()))
        e = AlpacaFeatureEnricher(key_id="k", secret_key="s",
                                  transport=transport,
                                  sector_provider=provider)
        result = e(_cand("TSLA"), DATE, _regime())
        self.assertIsNotNone(result.features)
        self.assertIn("sector=consumer_discretionary",
                      result.features.source_reference)

    def test_sector_provider_absent_leaves_source_reference_unchanged(self):
        """Without a provider the source_reference must stay
        'alpaca-<feed>' with NO sector fragment -- the backward-
        compatible default path."""
        transport = _StubTransport(HttpResponse(200,
                                                json.dumps(_bars(35)).encode()))
        e = AlpacaFeatureEnricher(key_id="k", secret_key="s",
                                  transport=transport)
        result = e(_cand("TSLA"), DATE, _regime())
        self.assertNotIn("sector=", result.features.source_reference)


if __name__ == "__main__":
    unittest.main()
