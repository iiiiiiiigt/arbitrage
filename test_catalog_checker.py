import unittest
from unittest.mock import patch
from flask import Flask
import catalog_checker as checker


class CatalogTests(unittest.TestCase):
    def test_shortlist_and_missing_values(self):
        rows = checker.snapshot()['rows']
        self.assertEqual(sum(r['shortlist'] for r in rows), 8)
        for r in rows:
            if r['median_daily'] is None:
                self.assertIsNone(r['weekly_total'])
        self.assertTrue(any(r['frost'] is None for r in rows))

    def test_queue_validation_and_deduplication(self):
        app = Flask(__name__)
        checker.register(app)
        client = app.test_client()
        key = next(iter(checker.bot.CATALOG))
        with patch.object(checker.bot.sales_collector, 'start') as start, patch.object(checker.bot.sales_collector, 'enqueue') as enqueue:
            for keys in [[], ['missing'], [key] * 13, [None]]:
                self.assertEqual(client.post('/catalog-check', json={'keys': keys}).status_code, 400)
            start.assert_not_called()
            self.assertEqual(client.post('/catalog-check', json={'keys': [key, key]}).status_code, 200)
            enqueue.assert_called_once()
