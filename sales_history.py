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

TRANSPORT_CONFIG = Path(__file__).resolve().parent / '.live' / 'starpets-transport.json'


def history_session(config_path=TRANSPORT_CONFIG):
    """Local-only transport settings; never copy proxy credentials into public data."""
    if not Path(config_path).exists():
        return requests.Session()
    config = json.loads(Path(config_path).read_text(encoding='utf-8'))
    from curl_cffi import requests as browser_requests
    session = browser_requests.Session(impersonate='chrome', trust_env=False)
    proxy_file = Path(config['proxy_file'])
    if not proxy_file.is_absolute():
        proxy_file = Path(__file__).resolve().parent / proxy_file
    with proxy_file.open(encoding='utf-8') as source:
        entries = (line.strip() for line in source if line.strip())
        index = int(config.get('proxy_index', 0))
        if index < 0:
            raise ValueError('Proxy index must be nonnegative')
        proxy = next((line for i, line in enumerate(entries) if i == index), '')
    if not proxy:
        raise ValueError('Configured StarPets proxy file is empty')
    proxy = proxy if '://' in proxy else 'http://' + proxy
    session.proxies = {'http': proxy, 'https': proxy}
    return session

BASE = 'https://market.apineural.com'
PATH = Path(__file__).resolve().parent / 'data' / 'starpets_sales_history.json'
TTL = 6 * 3600


class HistoryError(Exception):
    def __init__(self, message, delay=60, global_pause=False):
        super().__init__(message)
        self.delay = delay
        self.global_pause = global_pause


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
        self.failures = {}
        self.retries = {}
        self.errors = {}
        self.network_failures = 0
        self.status = 'Sales history has not been checked'
        self.session = history_session()
        self.session.headers.update({'Accept': 'application/json', 'Origin': 'https://starpets.gg', 'Referer': 'https://starpets.gg/'})
        self.next_request = 0
        self.catalog_cache = {}
        self.statistics_cache = {}
        self.active_key = None
        self.completed = 0
        self.cache_hits = 0

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
            if key in self.pending or key in self.retries or (time.time() - saved.get('fetched_at', 0) < TTL and 'listing_low' in saved and weekly_demand(saved)['median_daily'] is not None):
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
                global_pause = response.status_code in (429, 503) or response.status_code >= 500
                delay = 60 if response.status_code == 429 else 15
                retry_after = response.headers.get('Retry-After')
                if retry_after:
                    try:
                        delay = max(delay, float(retry_after))
                    except ValueError:
                        from email.utils import parsedate_to_datetime
                        try:
                            delay = max(delay, parsedate_to_datetime(retry_after).timestamp() - time.time())
                        except (ValueError, TypeError, OverflowError):
                            pass
                raise HistoryError(f'StarPets HTTP {response.status_code}', delay, global_pause)
            return response.json()
        except HistoryError:
            raise
        except Exception as exc:
            raise HistoryError(f'StarPets unavailable ({type(exc).__name__})', 15, True) from exc

    def enumerate_products(self, record):
        cache_key = (record['name'], record.get('type', 'pet'))
        saved = self.catalog_cache.get(cache_key)
        if saved and time.time() - saved[0] < 300:
            self.cache_hits += 1
            return saved[1]
        products = {}
        for page in range(1, 21):
            previous_count = len(products)
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
            if len(products) == previous_count and total != 0:
                raise HistoryError('Catalog pagination made no progress; item will retry later')
            if isinstance(total, int) and len(products) >= total:
                break
        else:
            raise HistoryError('Product enumeration exceeded the targeted-check limit')
        self.catalog_cache = {k:v for k,v in self.catalog_cache.items() if time.time()-v[0]<300}
        self.catalog_cache[cache_key] = (time.time(), products)
        return products

    def product_statistics(self, pid):
        # Reuse successful requests on retries, never partial aggregates or errors.
        cache_key = (pid, datetime.now(timezone.utc).date())
        saved = self.statistics_cache.get(cache_key)
        if saved and time.time()-saved[0] < 600:
            self.cache_hits += 1
            return saved[1]
        rows = parse_statistics(self.request('GET', f'/api/v4/goods/{pid}/statistics',
                                            params={'days': 30, 'currency': 'usd'}))
        self.statistics_cache = {k:v for k,v in self.statistics_cache.items() if time.time()-v[0]<600}
        self.statistics_cache[cache_key] = (time.time(), rows)
        return rows

    def progress(self):
        with self.lock:
            return dict(remaining=len(self.pending | set(self.retries)),
                        queued=len(self.pending - {self.active_key}),
                        retrying=len(self.retries), active=self.active_key,
                        completed_since_start=self.completed, reused_requests=self.cache_hits,
                        cooldown_seconds=max(0, round(self.retry_at-time.time())))

    def fetch(self, key, record):
        products = self.enumerate_products(record)
        ids = [pid for pid, item in products.items() if product_key(item) == key]
        if not ids:
            raise HistoryError('Exact variant is absent from the current product catalog')
        today = datetime.now(timezone.utc).date()
        totals = {(today - timedelta(days=i)).isoformat(): 0 for i in range(1, 8)}
        for pid in ids:
            rows = self.product_statistics(pid)
            for day in totals:
                totals[day] += rows.get(day, 0)
        prices = [float(products[pid]['price']) for pid in ids
                  if isinstance(products[pid].get('price'), (int, float))
                  and math.isfinite(products[pid]['price']) and products[pid]['price'] > 0]
        return {'listing_low': min(prices) if prices else None,
                'demand_scope': 'observed_product_daily_totals',
                'daily_sales': [{'date': day, 'count': totals[day]} for day in sorted(totals)],
                'fetched_at': time.time(), 'product_ids': ids, 'source': BASE,
                'coverage': 'Sales reported for all currently enumerated product IDs of this exact variant; missing reported dates count as zero observed sales.'}

    def describe(self, key):
        with self.lock:
            if key in self.errors:
                return self.errors[key]
            if key in self.pending:
                return 'Sales check queued or running'
            return 'Sales history has not been checked for this variant'

    def process_one(self, key, record):
        with self.lock:
            self.active_key = key
        try:
            self.status = 'Checking StarPets sales for ' + record['name']
            result = self.fetch(key, record)
            with self.lock:
                self.records[key] = result
                self.completed += 1
                self.path.parent.mkdir(exist_ok=True)
                temporary = self.path.with_suffix('.tmp')
                temporary.write_text(json.dumps(self.records), encoding='utf-8')
                temporary.replace(self.path)
                self.failures.pop(key, None)
                self.errors.pop(key, None)
                self.retries.pop(key, None)
            self.network_failures = 0
            self.status = 'StarPets sales history checked; continuing queue'
        except Exception as exc:
            error = exc if isinstance(exc, HistoryError) else HistoryError('Item check failed; previous data retained')
            count = self.failures.get(key, 0) + 1
            delay = max(error.delay, min(3600, 60 * 2 ** min(count - 1, 6)))
            if error.global_pause:
                self.network_failures += 1
                pause = max(error.delay, min(300, 15 * 2 ** min(self.network_failures - 1, 5)))
                self.retry_at = time.time() + pause
                self.status = f'StarPets connection cooldown: {int(pause)}s; automatically resumes'
            else:
                self.status = 'Skipped ' + record['name'] + '; continuing other sales checks'
            with self.lock:
                self.failures[key] = count
                self.retries[key] = (record, time.time() + delay)
                self.errors[key] = str(error) + '; automatic retry after ' + datetime.fromtimestamp(time.time() + delay, timezone.utc).strftime('%H:%M:%S UTC')
        finally:
            with self.lock:
                self.pending.discard(key)
                self.active_key = None

    def work(self):
        while True:
            if time.time() < self.retry_at:
                time.sleep(min(1, self.retry_at - time.time()))
                continue
            with self.lock:
                for key, (record, due) in list(self.retries.items()):
                    if due <= time.time() and not self.queue.full():
                        self.queue.put_nowait((key, record))
                        self.pending.add(key)
                        del self.retries[key]
            try:
                key, record = self.queue.get(timeout=1)
            except queue.Empty:
                continue
            self.process_one(key, record)
            self.queue.task_done()


collector = HistoryCollector()
