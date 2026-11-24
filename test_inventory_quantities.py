import unittest
from unittest.mock import patch
import bot
import app

class InventoryQuantityTests(unittest.TestCase):
    def test_parser_preserves_duplicates_and_variants(self):
        self.assertEqual(app.parse_inventory('["Cow Fly Ride","Cow Fly Ride","Cow Ride"]'),
                         ['Cow Fly Ride', 'Cow Fly Ride', 'Cow Ride'])
        with self.assertRaises(ValueError):
            app.parse_inventory('{"Cow": 2}')

    def test_trade_requires_enough_of_exact_variant(self):
        trade = {'id':'test','lookingFor':[{'name':'Cow','type':'fr'}]*2,
                 'offering':[{'name':'Parrot','type':'fr'}]}
        for inventory in [['Cow Fly Ride'], ['Cow Fly Ride','Cow Ride']]:
            result, reason = bot._evaluate_trade(trade, inventory)
            self.assertIsNone(result)
            self.assertEqual(reason, 'not in your inventory')
        _, reason = bot._evaluate_trade(trade, ['Cow Fly Ride']*2)
        self.assertNotEqual(reason, 'not in your inventory')
