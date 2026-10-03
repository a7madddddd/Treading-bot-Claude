"""Tests for the politician whitelist (D-0050 Phase B.19)."""

import unittest
from research.politicians import (
    WHITELIST, lookup, is_whitelisted, all_profiles, _normalize_name,
)


class TestWhitelist(unittest.TestCase):
    def test_whitelist_non_empty(self):
        self.assertGreaterEqual(len(WHITELIST), 10)

    def test_each_entry_has_committees(self):
        for p in WHITELIST:
            self.assertTrue(p.name)
            self.assertIn(p.chamber, ("House", "Senate"))
            self.assertIn(p.party, ("D", "R", "I"))
            self.assertGreater(len(p.committees), 0)
            self.assertGreater(p.alpha_weight, 0)

    def test_lookup_exact(self):
        p = lookup("Nancy Pelosi")
        self.assertIsNotNone(p)
        self.assertEqual(p.chamber, "House")

    def test_lookup_case_insensitive(self):
        self.assertIsNotNone(lookup("nancy pelosi"))
        self.assertIsNotNone(lookup("NANCY PELOSI"))

    def test_lookup_last_first_format(self):
        self.assertIsNotNone(lookup("Pelosi, Nancy"))

    def test_lookup_missing_returns_none(self):
        self.assertIsNone(lookup("Nobody Here"))

    def test_lookup_empty_returns_none(self):
        self.assertIsNone(lookup(""))
        self.assertIsNone(lookup(None))

    def test_is_whitelisted_passthrough(self):
        self.assertTrue(is_whitelisted("Nancy Pelosi"))
        self.assertFalse(is_whitelisted("Random Senator"))

    def test_all_profiles_returns_copy(self):
        copy = all_profiles()
        self.assertEqual(len(copy), len(WHITELIST))
        copy.append(None)  # mutating returned list must not break caller state
        self.assertEqual(len(all_profiles()), len(WHITELIST))

    def test_normalize_punctuation(self):
        self.assertEqual(_normalize_name("O'Brien, John"), "obrienjohn")


if __name__ == "__main__":
    unittest.main()
