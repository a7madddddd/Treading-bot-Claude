import unittest

from d0026.percentile_utils import (
    drop_bottom_percentile, historical_percentile, rank_percentile,
    top_percentile,
)


class TestTopPercentile(unittest.TestCase):
    def test_top_30_of_10(self):
        items = list(range(10))
        surv, rej = top_percentile(items, key=lambda x: float(x),
                                   top_fraction=0.30)
        self.assertEqual(sorted(surv), [7, 8, 9])
        self.assertEqual(sorted(rej), [0, 1, 2, 3, 4, 5, 6])

    def test_ties_broken_by_input_order(self):
        items = [("a", 1.0), ("b", 1.0), ("c", 2.0)]
        surv, rej = top_percentile(items, key=lambda x: x[1],
                                   top_fraction=0.5)
        # top 50% of 3 = ceil(1.5) = 2 items: c (value 2) then a
        # (value 1, wins over b by input order). Returns in ranked
        # descending order.
        self.assertEqual([x[0] for x in surv], ["c", "a"])
        self.assertEqual([x[0] for x in rej], ["b"])

    def test_none_keyed_go_to_rejected(self):
        items = [("a", 1.0), ("b", None), ("c", 2.0)]
        surv, rej = top_percentile(items, key=lambda x: x[1],
                                   top_fraction=0.5)
        # keyed items sorted: c=2, a=1; top-50% of 2 keyed = 1 (c). b in rejected.
        self.assertEqual([x[0] for x in surv], ["c"])
        self.assertIn(("a", 1.0), rej)
        self.assertIn(("b", None), rej)

    def test_all_none_returns_empty_surv(self):
        items = [("a", None), ("b", None)]
        surv, rej = top_percentile(items, key=lambda x: x[1],
                                   top_fraction=0.5)
        self.assertEqual(surv, ())
        self.assertEqual(rej, (("a", None), ("b", None)))

    def test_invalid_fraction(self):
        with self.assertRaises(ValueError):
            top_percentile([1], lambda x: x, top_fraction=0.0)
        with self.assertRaises(ValueError):
            top_percentile([1], lambda x: x, top_fraction=1.5)


class TestDropBottomPercentile(unittest.TestCase):
    def test_drop_bottom_20_of_10(self):
        items = list(range(10))
        surv, rej = drop_bottom_percentile(items, key=lambda x: float(x),
                                           bottom_fraction=0.20)
        self.assertEqual(sorted(surv), list(range(2, 10)))
        self.assertEqual(sorted(rej), [0, 1])

    def test_zero_bottom_no_drop(self):
        items = [1.0, 2.0, 3.0]
        surv, rej = drop_bottom_percentile(items, key=lambda x: x,
                                           bottom_fraction=0.0)
        self.assertEqual(sorted(surv), items)
        self.assertEqual(rej, ())

    def test_none_keyed_are_rejected(self):
        items = [("a", 1.0), ("b", None), ("c", 3.0)]
        surv, rej = drop_bottom_percentile(items, key=lambda x: x[1],
                                           bottom_fraction=0.5)
        self.assertNotIn(("b", None), surv)
        self.assertIn(("b", None), rej)

    def test_single_item_pool_never_drops_at_small_fraction(self):
        """Regression: pre-fix `drop_bottom_percentile([5], 0.2)` returned
        `((), (5,))` because a `max(1, ...)` clause forced dropping at
        least one item. The corrected behavior uses standard rounding,
        so `round(1 * 0.2) = 0` and the item is kept."""
        surv, rej = drop_bottom_percentile([5], lambda x: x,
                                           bottom_fraction=0.2)
        self.assertEqual(surv, (5,))
        self.assertEqual(rej, ())

    def test_two_item_pool_at_20pct_keeps_both(self):
        surv, rej = drop_bottom_percentile([1, 2], lambda x: x,
                                           bottom_fraction=0.2)
        self.assertEqual(len(surv), 2)
        self.assertEqual(rej, ())

    def test_ten_items_at_20pct_drops_two(self):
        surv, rej = drop_bottom_percentile(list(range(10)),
                                           lambda x: x,
                                           bottom_fraction=0.2)
        self.assertEqual(sorted(surv), list(range(2, 10)))
        self.assertEqual(sorted(rej), [0, 1])


class TestRankPercentile(unittest.TestCase):
    def test_ranks_in_zero_to_one(self):
        items = [1.0, 2.0, 3.0, 4.0]
        r = dict(rank_percentile(items, key=lambda x: x))
        self.assertAlmostEqual(r[1.0], 0.0)
        self.assertAlmostEqual(r[4.0], 1.0)

    def test_ties_average(self):
        items = [1.0, 2.0, 2.0, 3.0]
        r = dict(rank_percentile(items, key=lambda x: x))
        # ranks 1,2.5,2.5,4 -> normalized (0, 0.5, 0.5, 1.0)
        self.assertAlmostEqual(r[1.0], 0.0)
        self.assertAlmostEqual(r[3.0], 1.0)


class TestHistoricalPercentile(unittest.TestCase):
    def test_history_percentile(self):
        self.assertAlmostEqual(
            historical_percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], current=7),
            0.6,
        )

    def test_empty_history_neutral(self):
        self.assertAlmostEqual(historical_percentile([], current=10.0), 0.5)


if __name__ == "__main__":
    unittest.main()
