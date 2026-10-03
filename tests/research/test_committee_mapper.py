"""Tests for committee → sector relevance (D-0050 Phase B.23)."""

import unittest
from research.committee_mapper import (
    committee_matches_sector, committee_match_count,
)


class TestCommitteeMatch(unittest.TestCase):
    def test_sullivan_energy_matches_oil(self):
        self.assertTrue(committee_matches_sector(
            "Dan Sullivan", "CRUDE PETROLEUM & NATURAL GAS"))

    def test_crenshaw_defense_matches_aircraft(self):
        self.assertTrue(committee_matches_sector(
            "Dan Crenshaw", "AIRCRAFT ENGINES & ENGINE PARTS"))

    def test_pelosi_intel_matches_semiconductors(self):
        self.assertTrue(committee_matches_sector(
            "Nancy Pelosi", "SEMICONDUCTORS & RELATED DEVICES"))

    def test_pelosi_intel_does_not_match_restaurant(self):
        self.assertFalse(committee_matches_sector(
            "Nancy Pelosi", "RETAIL-EATING PLACES"))

    def test_missing_sector_returns_false(self):
        self.assertFalse(committee_matches_sector("Nancy Pelosi", None))
        self.assertFalse(committee_matches_sector("Nancy Pelosi", ""))

    def test_unknown_politician_returns_false(self):
        self.assertFalse(committee_matches_sector(
            "Not A Senator", "SEMICONDUCTORS"))

    def test_match_count_counts_only_buys(self):
        trades = {
            "Nancy Pelosi": "BUY",
            "Dan Crenshaw": "SELL",   # SELL ignored
        }
        count = committee_match_count(trades, "SEMICONDUCTORS")
        self.assertEqual(count, 1)

    def test_match_count_multi_match(self):
        trades = {
            "Nancy Pelosi": "BUY",
            "Ro Khanna":    "BUY",
        }
        count = committee_match_count(trades, "SEMICONDUCTORS")
        self.assertEqual(count, 2)


if __name__ == "__main__":
    unittest.main()
