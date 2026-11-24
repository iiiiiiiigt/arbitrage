"""Tests for the three-source demand resolver and its priority order."""
import json
import os
import tempfile
import unittest

import demand_resolver as dr


def write(dirpath, name, blob):
    p = os.path.join(dirpath, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(blob, f)
    return p


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.amvgg = write(self.tmp, "amvgg.json", {"pets": [
            {"name": "Frost Dragon", "demand": {"regular": "High", "neon": "Low", "mega": "Medium"},
             "values": {"regular": 1.76}},
            {"name": "Parrot", "demand": {"regular": "Low", "neon": "Low", "mega": "Low"},
             "values": {"regular": 1.085}},
        ]})
        self.starpets = write(self.tmp, "starpets.json", {"variants": {
            "frost_dragon|default|1|1": {"rating": "High", "confidence": "high",
                                         "sales_per_listing": 5.75, "listings": 4,
                                         "daily_sales": 23.0},
            "frost_dragon|neon|1|1": {"rating": "Medium", "confidence": "low",
                                      "sales_per_listing": 0.3, "listings": 1,
                                      "daily_sales": 0.3},
            "parrot|default|1|1": {"rating": "Low", "confidence": "high",
                                   "sales_per_listing": 0.01, "listings": 900,
                                   "daily_sales": 9.0},
        }})
        dr.reset_cache()
        dr.load_amvgg(self.amvgg)
        dr.load_starpets(self.starpets)

    def tearDown(self):
        dr.reset_cache()

    def test_starpets_beats_amvgg_when_strong(self):
        v = dr.resolve("Frost Dragon Fly Ride", {"_key": "frost_dragon|default|1|1"})
        self.assertEqual(v["source"], "starpets_sales_per_listing")
        self.assertEqual(v["level"], "High")
        self.assertTrue(v["ok"])
        self.assertIn("sales_per_listing", v["details"])

    def test_thin_starpets_falls_through_to_amvgg(self):
        # StarPets says Medium but confidence is low -> amvgg decides
        v = dr.resolve("Frost Dragon Neon", {"_key": "frost_dragon|neon|1|1"})
        self.assertEqual(v["source"], "amvgg_rating")
        self.assertEqual(v["level"], "Low")     # neon demand is Low for Frost Dragon
        self.assertFalse(v["ok"])

    def test_amvgg_high_passes_without_starpets(self):
        v = dr.resolve("Frost Dragon", None)
        self.assertEqual(v["source"], "amvgg_rating")
        self.assertEqual(v["level"], "High")
        self.assertTrue(v["ok"])

    def test_amvgg_low_fails(self):
        v = dr.resolve("Parrot", None)
        self.assertEqual(v["level"], "Low")
        self.assertFalse(v["ok"])

    def test_verified_sales_outrank_everything(self):
        v = dr.resolve("Frost Dragon Fly Ride",
                       {"_key": "frost_dragon|default|1|1"},
                       verified_demand={"median_daily": 3.0, "weekly_total": 21})
        self.assertEqual(v["source"], "variant_daily_totals")
        self.assertTrue(v["ok"])
        self.assertIn("verified", v["label"])

    def test_verified_zero_sales_vetoes_rated_sources(self):
        # an actual measurement of no sales must not be overridden by a rating
        v = dr.resolve("Frost Dragon Fly Ride",
                       {"_key": "frost_dragon|default|1|1"},
                       verified_demand={"median_daily": 1.0, "weekly_total": 7})
        self.assertEqual(v["source"], "variant_daily_totals")
        self.assertFalse(v["ok"])

    def test_nothing_known(self):
        v = dr.resolve("Unknown Pet Fly Ride", None)
        self.assertEqual(v["source"], "none")
        self.assertFalse(v["ok"])
        self.assertEqual(v["level"], None)

    def test_parrot_starpets_low_does_not_pass(self):
        v = dr.resolve("Parrot Fly Ride", {"_key": "parrot|default|1|1"})
        # StarPets Low is not a pass; amvgg Low is not a pass either
        self.assertFalse(v["ok"])

    def test_split_name(self):
        self.assertEqual(dr.split_name("Frost Dragon Fly Ride"),
                         ("Frost Dragon", "default", True, True))
        self.assertEqual(dr.split_name("Frost Dragon Neon")[1], "neon")
        self.assertEqual(dr.split_name("Frost Dragon Mega Fly")[1], "mega_neon")
        self.assertEqual(dr.split_name("Parrot")[1], "default")

    def test_pumping_selects_the_right_amvgg_field(self):
        self.assertEqual(dr.resolve("Frost Dragon", None)["level"], "High")     # regular
        self.assertEqual(dr.resolve("Frost Dragon Neon", None)["level"], "Low")  # neon
        self.assertEqual(dr.resolve("Frost Dragon Mega", None)["level"], "Medium")  # mega


if __name__ == "__main__":
    unittest.main()
