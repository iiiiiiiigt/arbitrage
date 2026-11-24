import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
from sales_history import HistoryCollector, HistoryError, parse_statistics
from demand import weekly_demand
import scrape_starpets


class HistoryTests(unittest.TestCase):
    def test_errors_and_missing_schema_never_become_zero(self):
        for payload in [{}, {'data': None}, {'error': 'limited'}, {'data': {'statistics': [{'day': '2026-11-23', 'count': -1}]}}]:
            with self.assertRaises(HistoryError):
                parse_statistics(payload)

    def test_empty_success_is_distinct_from_failure(self):
        self.assertEqual(parse_statistics({'data': {'statistics': []}}), {})

    def test_duplicate_days_fail(self):
        row = {'day': '2026-11-23', 'count': 4}
        with self.assertRaises(HistoryError):
            parse_statistics({'data': {'statistics': [row, row]}})

    def test_exact_variant_aggregates_all_enumerated_product_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            collector = HistoryCollector(Path(temp) / 'history.json')
            products = [dict(id=i, realName='panda', pumping='default', flyable=False, rideable=(i==3)) for i in (1,2,3)]
            day = (datetime.now(timezone.utc).date()-timedelta(days=1)).isoformat()
            replies = [{'status': True, 'count': 3, 'items': products},
                       {'data': {'statistics': [{'day': day, 'count': 7}]}},
                       {'data': {'statistics': [{'day': day, 'count': 3}]}}]
            with patch.object(collector, 'request', side_effect=replies) as request:
                result = collector.fetch('panda|default|0|0', {'name': 'Panda'})
            self.assertEqual(result['product_ids'], ['1','2'])
            self.assertEqual(weekly_demand(result)['weekly_total'], 10)
            self.assertTrue(weekly_demand(result)['observed_only'])
            self.assertEqual(request.call_count, 3)

    def test_failed_product_blocks_aggregate(self):
        with tempfile.TemporaryDirectory() as temp:
            collector=HistoryCollector(Path(temp)/'history.json')
            products=[dict(id=1,realName='panda',pumping='default')]
            with patch.object(collector,'request',side_effect=[{'status':True,'count':1,'items':products},HistoryError('HTTP 429')]):
                with self.assertRaises(HistoryError):
                    collector.fetch('panda|default|0|0',{'name':'Panda'})
            self.assertEqual(collector.records,{})

    def test_bulk_scraper_failure_never_becomes_verified_zeros(self):
        pool=type('Pool',(),{'acquire':AsyncMock(return_value=object()),'release':AsyncMock()})()
        with patch.object(scrape_starpets,'fetch_listing_stats',new=AsyncMock(return_value=None)):
            result=asyncio.run(scrape_starpets.build_demand(pool, {'panda|default|0|0':[{'id':1}]},True))
        record=result['panda|default|0|0']
        self.assertEqual(record['demand_scope'],'unavailable')
        self.assertEqual(record['daily_sales'],[])


if __name__=='__main__':unittest.main()
