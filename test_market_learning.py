import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from market_learning import MarketHistory, basket_key

class MarketLearningTests(unittest.TestCase):
    def test_history_is_deduplicated_persistent_and_quantity_specific(self):
        bot=SimpleNamespace(canonical_slug=lambda n:n.lower(),AMVGG_MAP={'a':1,'b':1.2})
        row=dict(trade_id='1',give=['A'],receive=['B'],give_usd=10,recv_usd=12)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'history.db';h=MarketHistory(path)
            h.update(bot,[row],now=1000000)
            h.update(bot,[row],now=1000040)
            self.assertEqual(h.summary['offers'],1)
            self.assertEqual(h.profiles['a']['wanted_offers'],1)
            self.assertAlmostEqual(h.profiles['a']['quoted_frost_premium_pct'],20)
            h2=MarketHistory(path);h2.update(bot,[],now=1000080)
            self.assertEqual(h2.summary['offers'],1) # disappearing is not a completed sale
            self.assertIn(basket_key(['A'],bot.canonical_slug),h2.baskets)
            self.assertNotIn(basket_key(['A','A'],bot.canonical_slug),h2.baskets)
            self.assertNotIn('sales',h2.profiles['a'])
            h2.update(bot,[],now=1000080+8*86400)
            self.assertEqual(h2.summary['offers'],0)

    def test_bundle_premium_is_not_attributed_to_each_pet(self):
        bot=SimpleNamespace(canonical_slug=lambda n:n.lower(),AMVGG_MAP={'a':1,'b':1,'c':3})
        with tempfile.TemporaryDirectory() as temp:
            h=MarketHistory(Path(temp)/'history.db')
            h.update(bot,[dict(trade_id='x',give=['A','B'],receive=['C'],give_usd=20,recv_usd=30)],now=1000000)
            self.assertIsNone(h.profiles['a']['quoted_frost_premium_pct'])
            self.assertEqual(h.profiles['a']['wanted_offers'],1)
