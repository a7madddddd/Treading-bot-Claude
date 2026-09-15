"""Identity-layer tests, including the DELL scenario
(docs/trading/github-native-data-sources.md §9.4): two unrelated
companies sharing the ticker "DELL" across non-overlapping date ranges
must never be silently merged into one continuous identity.

All fixtures here are synthetic/in-memory — no real data source is used.
"""

import unittest
from datetime import date

from d0026.identity import (
    IdentityConfidence,
    ResolutionOutcome,
    SecurityIdentity,
    StaticAliasIdentityResolver,
    TickerAlias,
)


def _dell_fixture() -> StaticAliasIdentityResolver:
    original_dell = SecurityIdentity(
        security_id="dell-inc-original",
        display_name="Dell Inc. (original)",
        cik=None,
        confidence=IdentityConfidence.RESOLVED_CORROBORATED_RANGE,
    )
    dell_technologies = SecurityIdentity(
        security_id="dell-technologies",
        display_name="Dell Technologies Inc.",
        cik="0001571996",
        confidence=IdentityConfidence.RESOLVED_CIK,
    )
    aliases = [
        TickerAlias(
            security_id="dell-inc-original",
            ticker="DELL",
            effective_start=None,
            effective_end=date(2013, 10, 29),
            source_reference="joeyfife/point-in-time-sp500 + arielNacamulli/pitindex",
        ),
        TickerAlias(
            security_id="dell-technologies",
            ticker="DELL",
            effective_start=date(2024, 9, 23),
            effective_end=None,
            source_reference="joeyfife/point-in-time-sp500 + arielNacamulli/pitindex",
        ),
    ]
    return StaticAliasIdentityResolver([original_dell, dell_technologies], aliases)


class TestDellIdentityBoundary(unittest.TestCase):
    def setUp(self) -> None:
        self.resolver = _dell_fixture()

    def test_pre_boundary_resolves_to_original_dell(self) -> None:
        result = self.resolver.resolve("DELL", date(2010, 1, 4))
        self.assertIs(result.outcome, ResolutionOutcome.RESOLVED)
        self.assertEqual(result.identity.security_id, "dell-inc-original")
        self.assertIsNone(result.identity.cik)

    def test_last_day_before_removal_still_resolves(self) -> None:
        result = self.resolver.resolve("DELL", date(2013, 10, 29))
        self.assertIs(result.outcome, ResolutionOutcome.RESOLVED)
        self.assertEqual(result.identity.security_id, "dell-inc-original")

    def test_post_boundary_resolves_to_dell_technologies_with_cik(self) -> None:
        result = self.resolver.resolve("DELL", date(2025, 1, 2))
        self.assertIs(result.outcome, ResolutionOutcome.RESOLVED)
        self.assertEqual(result.identity.security_id, "dell-technologies")
        self.assertEqual(result.identity.cik, "0001571996")

    def test_first_day_of_reentry_resolves_to_dell_technologies(self) -> None:
        result = self.resolver.resolve("DELL", date(2024, 9, 23))
        self.assertIs(result.outcome, ResolutionOutcome.RESOLVED)
        self.assertEqual(result.identity.security_id, "dell-technologies")

    def test_gap_period_is_unresolved_not_guessed(self) -> None:
        # DELL traded during this window (Dell Technologies, pre-S&P-500
        # re-entry) but neither alias in this fixture covers it — the
        # resolver must say UNRESOLVED, never silently attach it to
        # either identity.
        result = self.resolver.resolve("DELL", date(2020, 6, 15))
        self.assertIs(result.outcome, ResolutionOutcome.UNRESOLVED)
        self.assertIsNone(result.identity)
        self.assertIsNone(result.alias)
        self.assertTrue(result.detail)

    def test_two_boundary_identities_are_never_the_same_object(self) -> None:
        pre = self.resolver.resolve("DELL", date(2013, 10, 29))
        post = self.resolver.resolve("DELL", date(2024, 9, 23))
        self.assertNotEqual(pre.identity.security_id, post.identity.security_id)
        self.assertNotEqual(pre.identity, post.identity)

    def test_unknown_ticker_is_unresolved(self) -> None:
        result = self.resolver.resolve("NOPE", date(2020, 1, 1))
        self.assertIs(result.outcome, ResolutionOutcome.UNRESOLVED)


class TestAmbiguousOverlapDetection(unittest.TestCase):
    def test_overlapping_aliases_for_distinct_identities_raise_at_construction(
        self,
    ) -> None:
        a = SecurityIdentity(security_id="a", display_name="A Corp")
        b = SecurityIdentity(security_id="b", display_name="B Corp")
        alias_a = TickerAlias(
            security_id="a",
            ticker="XYZ",
            effective_start=date(2020, 1, 1),
            effective_end=date(2021, 1, 1),
        )
        alias_b = TickerAlias(
            security_id="b",
            ticker="XYZ",
            effective_start=date(2020, 6, 1),  # overlaps alias_a
            effective_end=None,
        )
        with self.assertRaises(ValueError):
            StaticAliasIdentityResolver([a, b], [alias_a, alias_b])


class TestIdentityResolutionInvariants(unittest.TestCase):
    def test_resolved_outcome_requires_identity_and_alias(self) -> None:
        from d0026.identity import IdentityResolution

        with self.assertRaises(ValueError):
            IdentityResolution(
                outcome=ResolutionOutcome.RESOLVED,
                ticker="X",
                as_of_date=date(2020, 1, 1),
                identity=None,
                alias=None,
            )

    def test_unresolved_outcome_must_not_carry_identity(self) -> None:
        from d0026.identity import IdentityResolution

        identity = SecurityIdentity(security_id="x", display_name="X Corp")
        with self.assertRaises(ValueError):
            IdentityResolution(
                outcome=ResolutionOutcome.UNRESOLVED,
                ticker="X",
                as_of_date=date(2020, 1, 1),
                identity=identity,
                detail="should not be allowed",
            )

    def test_provisional_confidence_forbidden_with_cik(self) -> None:
        with self.assertRaises(ValueError):
            SecurityIdentity(
                security_id="x",
                display_name="X Corp",
                cik="0000000001",
                confidence=IdentityConfidence.PROVISIONAL,
            )


if __name__ == "__main__":
    unittest.main()
