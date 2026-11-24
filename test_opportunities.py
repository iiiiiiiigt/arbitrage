import unittest
from types import SimpleNamespace
from opportunities import assess_demand
from opportunities import cash_demand_ok
import opportunities
import scan_core
import time


class DemandQualificationTests(unittest.TestCase):
    def test_preference_is_soft_and_never_overrides_liquidity(self):
        bot = SimpleNamespace(canonical_slug=lambda n: '_'.join(sorted(n.lower().split())))
        def option(name, gain, liquid=True):
            return dict(receive=[name], extra_net=gain, liquid=liquid)
        preferred = option('Fallow Deer Fly Ride', 6)
        ordinary = option('Other', 7)
        strongest = option('Another', 10)
        unverified = option('Honey Badger', 100, False)
        rows = [ordinary, preferred, strongest, unverified]
        opportunities.rank_options(bot, rows)
        self.assertEqual(rows, [strongest, preferred, ordinary, unverified])
        self.assertEqual(preferred['extra_net'], 6)
        self.assertTrue(preferred['preferred'])
        self.assertFalse(ordinary['preferred'])
        self.assertEqual(len(rows), 4)

    def test_amvgg_high_demand_is_not_cash_sales(self):
        self.assertFalse(cash_demand_ok({'reliable':True,'demand_signal':{'source':'amvgg_rating','ok':True},'demand':{'median_daily':None}}))

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

    def test_followup_checks_retained_pets_and_both_scanners_agree(self):
        def info(name):
            return {'reliable':True,'demand':{'median_daily':None if name=='B' else 8},
                    'demand_signal':{'ok':True,'source':'amvgg_rating'}}
        result={'trade_id':'next','give':['A'],'receive':['C'],
                'profit_usd':4,'give_usd':10,'recv_usd':14}
        bot=SimpleNamespace(canonical_slug=lambda n:n,pet_price_info=info,
            AMVGG_MAP={'A':.5,'B':.1,'C':.05},
            _safe_pet_names=lambda items:(items,True,False),
            _evaluate_trade=lambda trade,give:(result,None))
        row={'trade_id':'first','give':['X'],'receive':['A','B'],'recv_usd':20}
        observed=[{'id':'next','lookingFor':['A'],'offering':['C'],'_seen_at':time.time()}]
        slow=opportunities.exit_advice(bot,row,observed)
        fast=scan_core.exit_advice_fast(bot,opportunities,row,scan_core.build_index(bot,observed))
        for advice in [slow,fast]:
            self.assertFalse(advice['demand_passed'])
            self.assertFalse(advice['next_step']['liquid'])
            self.assertEqual(advice['next_step']['basket_net_after'],24)
            self.assertEqual(advice['next_step']['extra_pct'],40)
            self.assertEqual(len(advice['downgrade_options']),1)

    def test_followup_cannot_spend_unowned_quantities(self):
        bot=SimpleNamespace(canonical_slug=lambda n:n,
            pet_price_info=lambda n:{'reliable':True,'demand':{'median_daily':None}},
            _safe_pet_names=lambda items:(items,True,False),
            _evaluate_trade=lambda *args: (_ for _ in ()).throw(AssertionError('must not evaluate unaffordable trade')))
        row={'trade_id':'first','receive':['A'],'recv_usd':10}
        advice=opportunities.exit_advice(bot,row,[{'id':'next','lookingFor':['A','A'],'_seen_at':time.time()}])
        self.assertIsNone(advice['next_step'])


if __name__ == '__main__':
    unittest.main()
