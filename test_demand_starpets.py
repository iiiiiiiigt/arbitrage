"""Tests for the StarPets sales-per-listing demand model."""
import unittest

from demand_starpets import compute, price_tiers


def row(name, price, daily, supply, days=None):
    return {"name": name, "price": price, "daily_sales": daily,
            "supply": supply, "days_with_sales": days}


class SalesPerListingTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            # expensive, low volume, thin supply -> should be High
            row("Bat Dragon", 500.0, 3.0, 4),
            # cheap, high volume, deep supply -> must NOT outrank it
            row("Common Dog", 2.0, 50.0, 900),
            # cheap and genuinely hot
            row("Hot Cheap", 1.0, 40.0, 100),
            # expensive and dead
            row("Dead Expensive", 400.0, 0.0, 3),
            # one listing, 0.3/day: high ratio but below the noise floor
            row("Noisy", 300.0, 0.3, 1),
            # missing sales data
            row("Mystery", 10.0, None, 5),
        ]
        self.scored = compute(self.records)

    def test_expensive_thin_supply_beats_cheap_high_volume(self):
        """The whole point of the model."""
        bat, dog = self.scored["Bat Dragon"], self.scored["Common Dog"]
        self.assertEqual(bat["rating"], "High")
        self.assertEqual(dog["rating"], "Low")
        self.assertGreater(bat["sales_per_listing"], dog["sales_per_listing"])
        self.assertLess(bat["days_per_sale"], dog["days_per_sale"])
        # and it wins despite 16x lower absolute volume
        self.assertLess(bat["daily_sales"], dog["daily_sales"])

    def test_metric_math(self):
        self.assertAlmostEqual(self.scored["Bat Dragon"]["sales_per_listing"], 0.75, places=4)
        self.assertAlmostEqual(self.scored["Common Dog"]["sales_per_listing"], 50/900, places=4)
        self.assertAlmostEqual(self.scored["Bat Dragon"]["days_per_sale"], 4/3.0, places=2)
        self.assertAlmostEqual(self.scored["Common Dog"]["days_per_sale"], 18.0, places=1)

    def test_cheap_and_tight_is_high(self):
        self.assertEqual(self.scored["Hot Cheap"]["rating"], "High")

    def test_zero_sales_is_low(self):
        dead = self.scored["Dead Expensive"]
        self.assertEqual(dead["rating"], "Low")
        self.assertIsNone(dead["days_per_sale"])
        self.assertEqual(dead["sales_per_listing"], 0.0)

    def test_noise_floor_blocks_a_single_listing_sale(self):
        noisy = self.scored["Noisy"]
        self.assertEqual(noisy["rating"], "Medium")
        self.assertIn("noise floor", noisy["reason"])

    def test_single_listing_is_low_confidence(self):
        self.assertEqual(self.scored["Noisy"]["confidence"], "low")
        self.assertIn("page-capped", self.scored["Noisy"]["reason"])

    def test_missing_data_is_unknown(self):
        self.assertEqual(self.scored["Mystery"]["rating"], "Unknown")

    def test_scope_is_labelled_honestly(self):
        self.assertEqual(self.scored["Bat Dragon"]["demand_scope"],
                         "starpets_sales_per_listing")

    def test_rating_monotonic_in_supply(self):
        order = {"Low": 0, "Medium": 1, "High": 2}
        base = compute([row("X", 100.0, 5.0, 10)])["X"]["rating"]
        tighter = compute([row("X", 100.0, 5.0, 2)])["X"]["rating"]
        looser = compute([row("X", 100.0, 5.0, 500)])["X"]["rating"]
        self.assertGreaterEqual(order[tighter], order[base])
        self.assertLessEqual(order[looser], order[base])

    def test_rating_monotonic_in_sales(self):
        order = {"Low": 0, "Medium": 1, "High": 2}
        low = compute([row("X", 100.0, 1.0, 50)])["X"]["rating"]
        high = compute([row("X", 100.0, 20.0, 50)])["X"]["rating"]
        self.assertGreaterEqual(order[high], order[low])

    def test_tier_relative_reported(self):
        self.assertIsNotNone(self.scored["Hot Cheap"]["tier_relative"])

    def test_same_name_different_variants_do_not_collide(self):
        """'Frost Dragon' has 12 variants; each must get its own score."""
        scored = compute([
            {"key": "frost_dragon|default|1|1", "name": "Frost Dragon",
             "price": 100.0, "daily_sales": 3.0, "supply": 6},
            {"key": "frost_dragon|neon|1|1", "name": "Frost Dragon",
             "price": 400.0, "daily_sales": 1.0, "supply": 2},
        ])
        self.assertEqual(len(scored), 2)
        self.assertAlmostEqual(scored["frost_dragon|default|1|1"]["sales_per_listing"], 0.5)
        self.assertAlmostEqual(scored["frost_dragon|neon|1|1"]["sales_per_listing"], 0.5)


class TierTests(unittest.TestCase):
    def test_tiers_split_prices(self):
        tier = price_tiers([1, 2, 3, 40, 50, 60, 700, 800, 900, 5000], 5)
        self.assertLess(tier(1), tier(900))
        self.assertLess(tier(2), tier(5000))

    def test_zero_price_lands_in_first_tier(self):
        self.assertEqual(price_tiers([10, 20, 30], 3)(0), 0)


if __name__ == "__main__":
    unittest.main()
