import unittest
from datetime import date,timedelta
from demand import weekly_demand

class DemandTests(unittest.TestCase):
    def record(self, counts):
        return {'demand_scope':'variant_daily_totals','daily_sales':[
            {'date':(date(2026,11,24)-timedelta(days=i+1)).isoformat(),'count':c}
            for i,c in enumerate(counts)]}
    def test_median_and_actual_total(self):
        d=weekly_demand(self.record([7,8,9,10,11,12,70]),date(2026,11,24))
        self.assertEqual(d['median_daily'],10)
        self.assertEqual(d['weekly_total'],127)
    def test_saved_complete_week_can_be_used_without_disabling_refresh(self):
        r=self.record([7,8,9,10,11,12,70])
        today=date(2026,11,27)
        self.assertIsNone(weekly_demand(r,today)['median_daily'])
        d=weekly_demand(r,today,allow_stale=True)
        self.assertEqual(d['median_daily'],10)
        self.assertTrue(d['stale'])
        self.assertEqual(d['period_end'],'2026-11-23')
        self.assertIsNone(weekly_demand(self.record([7]*6),today,allow_stale=True)['median_daily'])

    def test_missing_day_not_zero(self):
        self.assertIsNone(weekly_demand(self.record([7]*6),date(2026,11,24))['median_daily'])
    def test_verified_zeros(self):
        self.assertEqual(weekly_demand(self.record([0]*7),date(2026,11,24))['median_daily'],0)
    def test_old_mean_not_a_median(self):
        self.assertIsNone(weekly_demand({'avg_daily_sales':8.86})['median_daily'])
    def test_duplicate_days_rejected(self):
        r=self.record([7]*7);r['daily_sales'].append(r['daily_sales'][0])
        self.assertIsNone(weekly_demand(r,date(2026,11,24))['median_daily'])

if __name__=='__main__':unittest.main()
