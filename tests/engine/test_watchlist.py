import unittest

from engine.watchlist import StaticWatchlistSource, WatchlistSource


class TestAbstractInterface(unittest.TestCase):
    def test_cannot_instantiate_abc_directly(self):
        with self.assertRaises(TypeError):
            WatchlistSource()  # type: ignore[abstract]


class TestStaticWatchlistSource(unittest.TestCase):
    def test_returns_configured_symbols(self):
        source = StaticWatchlistSource(("TSLA",))
        self.assertEqual(source.get_active_symbols(), ("TSLA",))

    def test_normalizes_case_and_whitespace(self):
        source = StaticWatchlistSource((" tsla ",))
        self.assertEqual(source.get_active_symbols(), ("TSLA",))

    def test_empty_list_is_valid(self):
        source = StaticWatchlistSource(())
        self.assertEqual(source.get_active_symbols(), ())

    def test_returns_same_tuple_each_call(self):
        source = StaticWatchlistSource(("TSLA", "AAPL"))
        self.assertEqual(source.get_active_symbols(), source.get_active_symbols())


if __name__ == "__main__":
    unittest.main()
