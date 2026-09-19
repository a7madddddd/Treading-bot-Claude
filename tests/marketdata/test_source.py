import unittest

from marketdata.source import MarketDataError, MarketDataSource, MarketDataUnavailableError


class TestAbstractInterface(unittest.TestCase):
    def test_cannot_instantiate_abc_directly(self):
        with self.assertRaises(TypeError):
            MarketDataSource()  # type: ignore[abstract]

    def test_concrete_subclass_can_be_instantiated(self):
        class Fake(MarketDataSource):
            def get_last_trade(self, symbol: str) -> float:
                return 100.0

        source = Fake()
        self.assertEqual(source.get_last_trade("TSLA"), 100.0)


class TestExceptionHierarchy(unittest.TestCase):
    def test_unavailable_is_market_data_error(self):
        self.assertTrue(issubclass(MarketDataUnavailableError, MarketDataError))

    def test_market_data_error_is_runtime_error(self):
        self.assertTrue(issubclass(MarketDataError, RuntimeError))


if __name__ == "__main__":
    unittest.main()
