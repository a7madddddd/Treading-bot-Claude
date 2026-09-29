"""Tests for sector_provider (B30)."""

import json
import os
import tempfile
import unittest

from d0026.sector_provider import (
    StaticSectorProvider, load_default_sector_provider,
    sector_fragment,
)


class TestStaticSectorProvider(unittest.TestCase):
    def test_direct_mapping(self):
        p = StaticSectorProvider(mapping={"AAPL": "information_technology"})
        self.assertEqual(p.sector_of("AAPL"), "information_technology")

    def test_lookup_case_insensitive(self):
        p = StaticSectorProvider(mapping={"AAPL": "information_technology"})
        self.assertEqual(p.sector_of("aapl"), "information_technology")
        self.assertEqual(p.sector_of("Aapl"), "information_technology")

    def test_unknown_returns_none(self):
        p = StaticSectorProvider(mapping={"AAPL": "information_technology"})
        self.assertIsNone(p.sector_of("NOTREAL"))


class TestFromJSONFile(unittest.TestCase):
    def _write(self, obj):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(obj, f)
        f.close()
        return f.name

    def test_load_valid(self):
        path = self._write({"sectors": {"AAPL": "tech", "TSLA": "auto"}})
        try:
            p = StaticSectorProvider.from_json_file(path)
            self.assertEqual(p.sector_of("AAPL"), "tech")
            self.assertEqual(p.sector_of("TSLA"), "auto")
        finally:
            os.unlink(path)

    def test_missing_sectors_key_rejected(self):
        path = self._write({"not_sectors": {}})
        try:
            with self.assertRaises(ValueError):
                StaticSectorProvider.from_json_file(path)
        finally:
            os.unlink(path)

    def test_non_string_value_rejected(self):
        path = self._write({"sectors": {"AAPL": 42}})
        try:
            with self.assertRaises(ValueError):
                StaticSectorProvider.from_json_file(path)
        finally:
            os.unlink(path)

    def test_empty_value_rejected(self):
        path = self._write({"sectors": {"AAPL": ""}})
        try:
            with self.assertRaises(ValueError):
                StaticSectorProvider.from_json_file(path)
        finally:
            os.unlink(path)


class TestDefault(unittest.TestCase):
    def test_default_file_loads(self):
        p = load_default_sector_provider()
        # Known entries from data/sectors.json
        self.assertEqual(p.sector_of("AAPL"), "information_technology")
        self.assertEqual(p.sector_of("TSLA"), "consumer_discretionary")
        self.assertEqual(p.sector_of("SPY"), "etf_broad_market")

    def test_default_covers_backtest_universe(self):
        # Every symbol we exercise in the live backtest should be
        # covered so stage G actually has data to enforce on.
        p = load_default_sector_provider()
        universe = ["TSLA", "AAPL", "SPY", "NVDA", "MSFT", "AMZN",
                    "GOOGL", "META", "QQQ", "IWM", "NFLX", "COST", "V"]
        missing = [s for s in universe if p.sector_of(s) is None]
        self.assertEqual(missing, [], f"missing: {missing}")


class TestSectorFragment(unittest.TestCase):
    def test_none_returns_empty(self):
        self.assertEqual(sector_fragment(None), "")

    def test_empty_returns_empty(self):
        self.assertEqual(sector_fragment(""), "")

    def test_normal_value(self):
        self.assertEqual(sector_fragment("financials"), "sector=financials")


if __name__ == "__main__":
    unittest.main()
