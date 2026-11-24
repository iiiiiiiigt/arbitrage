import unittest
from types import SimpleNamespace
from opportunities import assess_demand


class DemandQualificationTests(unittest.TestCase):
    def assess(self, median, margin=True):
        bot = SimpleNamespace(
            canonical_slug=lambda name: name,
            pet_price_info=lambda name: {
                'reliable': True, 'sales_per_day': median or 0,
                'demand': {'median_daily': median}},
            with_demand=lambda row: row)
        trade = {'trade_id': 'test', 'give': ['Parrot'],
                 'receive': ['Giant Panda'], 'recv_usd': 50}
        return assess_demand(bot, trade, [], margin)

    def test_missing_demand_cannot_qualify_positive_margin(self):
        row = self.assess(None)
        self.assertFalse(row['qualified'])
        self.assertTrue(row['margin_qualified'])
        self.assertIn('Giant Panda', row['reason'])

    def test_confirmed_low_demand_requires_review(self):
        row = self.assess(1)
        self.assertFalse(row['qualified'])
        self.assertNotIn('unavailable', row['reason'])

    def test_verified_liquid_pet_passes(self):
        self.assertTrue(self.assess(8)['qualified'])

    def test_demand_cannot_override_failed_margin(self):
        self.assertFalse(self.assess(8, margin=False)['qualified'])


if __name__ == '__main__':
    unittest.main()
