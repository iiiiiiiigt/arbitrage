"""Targeted public StarPets history checks; one worker, no proxy rotation.

Counts cover the enumerated product IDs, not a claim about every marketplace
sale. Errors never become zero sales. Prices and credentials are not modified.
"""
import json
import math
import queue
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

BASE = 'https://market.apineural.com'
PATH = Path(__file__).resolve().parent / 'data' / 'starpets_sales_history.json'
TTL = 6 * 3600


class HistoryError(Exception):
    def __init__(self, message, delay=900):
        super().__init__(message)
        self.delay = delay


def parse_statistics(payload):
    if not isinstance(payload, dict) or payload.get('error'):
        raise HistoryError('Statistics API returned an error')
    data = payload.get('data')
    rows = data.get('statistics') if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise HistoryError('Statistics schema is not recognized')
    result = {}
    for row in rows:
        try:
            day = date.fromisoformat(row['day']).isoformat()
            count = row['count']
            if isinstance(count, bool) or not isinstance(count, (int, float)):
                raise ValueError()
            if not math.isfinite(count) or count < 0 or int(count) != count or day in result:
                raise ValueError()
            result[day] = int(count)
        except (KeyError, TypeError, ValueError):
            raise HistoryError('Invalid or duplicate daily sales row')
    return result


def product_key(item):
    slug = re.sub(r'[^a-z0-9]+', '_', str(item.get('realName', '')).lower()).strip('_')
    return f"{slug}|{item.get('pumping') or 'default'}|{int(bool(item.get('flyable')))}|{int(bool(item.get('rideable')))}"


class HistoryCollector:
    def __init__(self, path=PATH):
        self.path = Path(path)
        try:
            self.records = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.records = {}
        self.pending = set()
        self.queue = queue.Queue(maxsize=256)
        self.lock = threading.Lock()
        self.started = False
        self.retry_at = 0
        self.status = 'Sales history has not been checked'
        self.session = requests.Session()
        self.session.headers.update({'Accept': 'application/json', 'Origin': 'https://starpets.gg', 'Referer': 'https://starpets.gg/'})
        self.next_request = 0

    def start(self):
        with self.lock:
            if self.started:
                return
            self.started = True
        threading.Thread(target=self.work, name='targeted-sales-history', daemon=True).start()

    def enqueue(self, key, record):
        if not self.started or not key or not record:
            return
        with self.lock:
            saved = self.records.get(key, {})
            from demand import weekly_demand
            if key in self.pending or (time.time() - saved.get('fetched_at', 0) < TTL and weekly_demand(saved)['median_daily'] is not None):
                return
            try:
                self.queue.put_nowait((key, dict(record)))
                self.pending.add(key)
            except queue.Full:
                pass

    def get(self, key):
        with self.lock:
            return dict(self.records.get(key, {}))

    def request(self, method, path, **kwargs):
        time.sleep(max(0, self.next_request - time.monotonic()))
        self.next_request = time.monotonic() + 2
        try:
            response = self.session.request(method, BASE + path, timeout=(6, 12), **kwargs)
            if response.status_code != 200:
                try:
                    delay = max(900, float(response.headers.get('Retry-After', 900)))
                except ValueError:
                    delay = 900
                raise HistoryError(f'StarPets HTTP {response.status_code}; sales check paused', delay)
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            raise HistoryError(f'StarPets unavailable ({type(exc).__name__})') from exc

    def fetch(self, key, record):
        products = {}
        for page in range(1, 5):
            payload = self.request('POST', '/api/store/items/all', json={
                'currency': 'usd', 'page': page, 'amount': 72,
                'filter': {'name': record['name'], 'types': [{'type': record.get('type', 'pet')}]},
                'sort': {'price': 'asc'}})
            if not payload.get('status') or not isinstance(payload.get('items'), list):
                raise HistoryError('Catalog response is incomplete')
            for item in payload['items']:
                if item.get('id') is not None:
                    products[str(item['id'])] = item
            total = payload.get('count')
            if isinstance(total, int) and len(products) >= total:
                break
        else:
            raise HistoryError('Product enumeration exceeded the targeted-check limit')
        ids = [pid for pid, item in products.items() if product_key(item) == key]
        if not ids:
            raise HistoryError('Exact variant is absent from the current product catalog')
        today = datetime.now(timezone.utc).date()
        totals = {(today - timedelta(days=i)).isoformat(): 0 for i in range(1, 8)}
        for pid in ids:
            rows = parse_statistics(self.request('GET', f'/api/v4/goods/{pid}/statistics',
                                                 params={'days': 30, 'currency': 'usd'}))
            for day in totals:
                totals[day] += rows.get(day, 0)
        return {'demand_scope': 'observed_product_daily_totals',
                'daily_sales': [{'date': day, 'count': totals[day]} for day in sorted(totals)],
                'fetched_at': time.time(), 'product_ids': ids, 'source': BASE,
                'coverage': 'Sales reported for all currently enumerated product IDs of this exact variant; missing reported dates count as zero observed sales.'}

    def work(self):
        while True:
            key, record = self.queue.get()
            while time.time() < self.retry_at:
                time.sleep(min(30, self.retry_at - time.time()))
            try:
                self.status = 'Checking StarPets sales for ' + record['name']
                result = self.fetch(key, record)
                with self.lock:
                    self.records[key] = result
                    self.path.parent.mkdir(exist_ok=True)
                    temporary = self.path.with_suffix('.tmp')
                    temporary.write_text(json.dumps(self.records), encoding='utf-8')
                    temporary.replace(self.path)
                self.status = 'StarPets sales history checked'
            except HistoryError as exc:
                self.retry_at = time.time() + exc.delay
                self.status = str(exc) + '; retry after ' + datetime.fromtimestamp(self.retry_at, timezone.utc).strftime('%H:%M UTC')
            except Exception:
                self.retry_at = time.time() + 900
                self.status = 'Sales check failed; retained previous history; retry in 15 minutes'
            finally:
                with self.lock:
                    self.pending.discard(key)
                self.queue.task_done()


collector = HistoryCollector()
