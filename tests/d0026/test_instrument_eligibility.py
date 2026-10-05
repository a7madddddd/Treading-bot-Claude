"""Tests for P-021 instrument eligibility (leveraged / inverse exclusion).

Every fund name below is a REAL product name, not an invented string --
the whole filter rests on naming conventions actually used by issuers,
so testing it against made-up names would prove nothing.
"""

import unittest

from d0026.instrument_eligibility import (
    ELIGIBILITY_POLICY_VERSION,
    EligibilityVerdict,
    classify_instrument,
    is_eligible,
)


class TestTheFundThatCausedThisFilter(unittest.TestCase):
    """DXD is the live case: it was selected into the Controller's
    2026-10-03 snapshot on the Oracle VM."""

    def test_dxd_is_excluded(self):
        v = classify_instrument(symbol="DXD",
                                name="ProShares UltraShort Dow30")
        self.assertFalse(v.eligible)
        self.assertIn("ULTRASHORT", v.reason)
        self.assertIn("DXD", v.reason)


class TestLeveragedAndInverseAreExcluded(unittest.TestCase):
    CASES = (
        ("SQQQ", "ProShares UltraPro Short QQQ"),
        ("TQQQ", "ProShares UltraPro QQQ"),
        ("QLD", "ProShares Ultra QQQ"),
        ("SDS", "ProShares UltraShort S&P500"),
        ("PSQ", "ProShares Short QQQ"),
        ("SH", "ProShares Short S&P500"),
        ("SOXL", "Direxion Daily Semiconductor Bull 3X Shares"),
        ("SOXS", "Direxion Daily Semiconductor Bear 3X Shares"),
        ("TSLL", "Direxion Daily TSLA Bull 2X Shares"),
        ("NVDL", "GraniteShares 2x Long NVDA Daily ETF"),
        ("FNGU", "MicroSectors FANG+ Index 3X Leveraged ETN"),
        ("SPXU", "ProShares UltraPro Short S&P500"),
        ("UVXY", "ProShares Ultra VIX Short-Term Futures ETF"),
    )

    def test_all_excluded(self):
        for symbol, name in self.CASES:
            with self.subTest(symbol=symbol):
                v = classify_instrument(symbol=symbol, name=name)
                self.assertFalse(
                    v.eligible,
                    f"{symbol} ({name}) must be excluded, got eligible",
                )
                self.assertTrue(v.reason)

    def test_multiplier_rule_names_the_multiplier(self):
        v = classify_instrument(
            symbol="SOXL",
            name="Direxion Daily Semiconductor Bull 3X Shares")
        self.assertIn("3X", v.reason)
        self.assertIn("multiplier", v.reason)

    def test_fractional_multiplier(self):
        v = classify_instrument(symbol="XX",
                                name="Some Fund 1.5X Daily Long")
        self.assertFalse(v.eligible)
        self.assertIn("1.5X", v.reason)

    def test_negative_multiplier(self):
        v = classify_instrument(symbol="XX", name="Some Fund -1X Daily")
        self.assertFalse(v.eligible)


class TestOrdinaryInstrumentsSurvive(unittest.TestCase):
    """False positives are the real danger here: wrongly excluding a
    normal security silently shrinks the Universe forever, and nothing
    in the pipeline would report it as a mistake.

    The five funds from the Controller's own 2026-10-03 snapshot are
    included on purpose -- whether ordinary funds belong in the Universe
    at all is P-024's deferred question, and this filter must NOT
    pre-empt that decision.
    """

    CASES = (
        # Operating companies from the live snapshot
        ("WBD", "Warner Bros. Discovery, Inc."),
        ("MUFG", "Mitsubishi UFJ Financial Group, Inc."),
        ("VOD", "Vodafone Group Plc"),
        ("PFE", "Pfizer, Inc."),
        # Ordinary funds from the live snapshot -- P-024, not P-021
        ("MAGS", "Roundhill Magnificent Seven ETF"),
        ("QQQI", "NEOS Nasdaq-100 High Income ETF"),
        ("ILF", "iShares Latin America 40 ETF"),
        ("CGGR", "Capital Group Growth ETF"),
        ("BCI", "abrdn Bloomberg All Commodity Strategy K-1 Free ETF"),
        # Ordinary large caps and plain index funds
        ("AAPL", "Apple Inc."),
        ("QQQ", "Invesco QQQ Trust, Series 1"),
        ("SPY", "SPDR S&P 500 ETF Trust"),
        ("TSLA", "Tesla, Inc."),
        ("NVDA", "NVIDIA Corporation"),
    )

    def test_all_eligible(self):
        for symbol, name in self.CASES:
            with self.subTest(symbol=symbol):
                v = classify_instrument(symbol=symbol, name=name)
                self.assertTrue(
                    v.eligible,
                    f"{symbol} ({name}) must stay eligible, "
                    f"got excluded: {v.reason}",
                )


class TestShortMeansDurationNotDirection(unittest.TestCase):
    """The one genuine ambiguity in the rule set. In these names SHORT
    is a MATURITY, not an inverse position, so they must survive."""

    CASES = (
        ("SHV", "iShares Short Treasury Bond ETF"),
        ("BSV", "Vanguard Short-Term Bond ETF"),
        ("IGSB", "iShares 1-5 Year Investment Grade Corporate Bond ETF"),
        ("VGSH", "Vanguard Short-Term Treasury ETF"),
        ("SCHO", "Schwab Short-Term U.S. Treasury ETF"),
    )

    def test_short_duration_funds_survive(self):
        for symbol, name in self.CASES:
            with self.subTest(symbol=symbol):
                v = classify_instrument(symbol=symbol, name=name)
                self.assertTrue(
                    v.eligible,
                    f"{symbol} ({name}) is a duration fund, not inverse; "
                    f"got excluded: {v.reason}",
                )

    def test_genuine_inverse_short_is_still_caught(self):
        """The exception must not become a hole: a real inverse fund
        whose name says SHORT with no duration context still goes."""
        v = classify_instrument(symbol="PSQ", name="ProShares Short QQQ")
        self.assertFalse(v.eligible)
        self.assertIn("SHORT", v.reason)

    def test_word_boundary_prevents_substring_false_positive(self):
        """SHORT must match as a word. A name merely CONTAINING the
        letters must not trip the rule."""
        for name in ("Shortline Rail Holdings Inc.",
                     "Overshort Capital Corp"):
            with self.subTest(name=name):
                self.assertTrue(is_eligible(symbol="XX", name=name))


class TestMultiplierBoundaries(unittest.TestCase):
    def test_x_inside_a_ticker_like_token_is_not_a_multiplier(self):
        for name in ("Technology Select Sector SPDR Fund XLK",
                     "ProShares S&P 500 Ex-Energy ETF",
                     "Max Holdings Corp"):
            with self.subTest(name=name):
                self.assertTrue(is_eligible(symbol="XX", name=name))

    def test_digit_then_x_without_boundary_is_ignored(self):
        self.assertTrue(is_eligible(symbol="XX", name="Fund 2XYZ Holdings"))


class TestMissingDataNeverRejects(unittest.TestCase):
    """'We could not tell' is not evidence of leverage."""

    def test_none_name_is_eligible(self):
        self.assertTrue(is_eligible(symbol="AAPL", name=None))

    def test_empty_name_is_eligible(self):
        self.assertTrue(is_eligible(symbol="AAPL", name=""))


class TestVerdictInvariants(unittest.TestCase):
    def test_eligible_verdict_cannot_carry_a_reason(self):
        with self.assertRaises(ValueError):
            EligibilityVerdict(eligible=True, reason="nope")

    def test_ineligible_verdict_must_carry_a_reason(self):
        with self.assertRaises(ValueError):
            EligibilityVerdict(eligible=False)

    def test_verdict_is_frozen(self):
        v = EligibilityVerdict(eligible=True)
        with self.assertRaises(Exception):
            v.eligible = False  # type: ignore[misc]

    def test_policy_version_is_pinned(self):
        """The version string must change deliberately, with its own
        decisions.md entry -- never as a silent edit."""
        self.assertEqual(ELIGIBILITY_POLICY_VERSION, "D0026-INSTR-ELIG-001")


class TestCaseInsensitivity(unittest.TestCase):
    def test_lowercase_name_still_excluded(self):
        self.assertFalse(is_eligible(symbol="DXD",
                                     name="proshares ultrashort dow30"))

    def test_mixed_case_multiplier_still_excluded(self):
        self.assertFalse(is_eligible(symbol="XX",
                                     name="Direxion Daily Bull 3x Shares"))


if __name__ == "__main__":
    unittest.main()
