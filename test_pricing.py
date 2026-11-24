import unittest

import bot


class PricingTests(unittest.TestCase):
    def test_parrot_does_not_match_dracula_parrot(self):
        record, multiplier = bot._lookup("Parrot Fly Ride")
        self.assertIsNotNone(record)
        self.assertEqual(record["name"], "Parrot")
        self.assertEqual(multiplier, 1.0)
        self.assertAlmostEqual(bot.pet_to_usd("Parrot Fly Ride"), 41.72, places=2)

    def test_fairy_bat_uses_starpets_internal_identity(self):
        record, _ = bot._lookup("Fairy Bat Dragon Neon Fly Ride")
        self.assertIsNotNone(record)
        self.assertEqual(record["realName"], "fairybat")
        self.assertAlmostEqual(
            bot.pet_to_usd("Fairy Bat Dragon Neon Fly Ride"), 21.76, places=2
        )

    def test_goat_is_not_kid_goat(self):
        record, _ = bot._lookup("Goat")
        self.assertIsNotNone(record)
        self.assertEqual(record["name"], "Goat")

    def test_similar_name_is_never_used_as_fallback(self):
        self.assertIsNone(bot._lookup("Parrot Neon Ride")[0])
        self.assertEqual(bot.pet_to_usd("Parrot Neon Ride"), 0.0)

    def test_known_trade_uses_net_exact_prices(self):
        trade = {
            "id": "pricing-regression",
            "authorName": "test",
            "lookingFor": [{"name": "Parrot", "type": "fr"}],
            "offering": [
                {"name": "Fairy Bat Dragon", "type": "nfr"},
                {"name": "Fairy Bat Dragon", "type": "nfr"},
            ],
        }
        result, reason = bot._evaluate_trade(trade, {"Parrot Fly Ride"})
        self.assertIsNone(result)
        self.assertIn("profit margin too small", reason)
        give = bot.pet_to_usd("Parrot Fly Ride")
        receive = 2 * bot.pet_to_usd("Fairy Bat Dragon Neon Fly Ride")
        self.assertAlmostEqual(receive - give, 1.80, places=2)


if __name__ == "__main__":
    unittest.main()
