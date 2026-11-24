"""Automatic public-offer history. Quotes are never treated as completed trades."""
import json
import math
import sqlite3
import time
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path
from statistics import median

PATH = Path(__file__).resolve().parent / '.live' / 'market-learning.sqlite3'


def basket_key(names, canonical):
    return json.dumps(sorted(Counter(map(canonical, names)).items()))


class MarketHistory:
    def __init__(self, path=PATH):
        self.path = Path(path)
        self.next_update = 0
        self.profiles = {}
        self.baskets = {}
        self.summary = {'offers': 0, 'scope': 'Posted offers only; not confirmed trades'}
        self.available = False

    def update(self, bot, rows, now=None):
        now = time.time() if now is None else now
        if now < self.next_update:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=2)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS offers (id TEXT PRIMARY KEY, first_seen REAL, last_seen REAL, payload TEXT)')
            records = []
            for row in rows:
                if not row.get('trade_id') or not row.get('give') or not row.get('receive'):
                    continue
                give = [bot.canonical_slug(n) for n in row['give']]
                receive = [bot.canonical_slug(n) for n in row['receive']]
                def frost(names):
                    values = [bot.AMVGG_MAP.get(n, 0) for n in names]
                    return sum(values) if all(v > 0 for v in values) else None
                payload = dict(give=give, receive=receive,
                               basket=basket_key(row['give'], bot.canonical_slug),
                               give_frost=frost(give), recv_frost=frost(receive),
                               give_net=row.get('give_usd'), recv_net=row.get('recv_usd'))
                seen = min(now, row.get('observed_at', now))
                records.append((str(row['trade_id']), seen, seen, json.dumps(payload)))
            db.executemany('INSERT INTO offers VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen,payload=excluded.payload', records)
            db.execute('DELETE FROM offers WHERE last_seen < ?', (now-30*86400,))
            db.execute('DELETE FROM offers WHERE id NOT IN (SELECT id FROM offers ORDER BY last_seen DESC LIMIT 20000)')
            history = db.execute('SELECT first_seen,last_seen,payload FROM offers WHERE last_seen >= ? ORDER BY last_seen DESC LIMIT 10000', (now-7*86400,)).fetchall()
        profiles = defaultdict(lambda: {'wanted_offers': 0, 'offered_offers': 0, 'premiums': []})
        baskets = defaultdict(list)
        for first, last, raw in history:
            row = json.loads(raw)
            for name in set(row['give']):profiles[name]['wanted_offers'] += 1
            for name in set(row['receive']):profiles[name]['offered_offers'] += 1
            if len(set(row['give'])) == 1 and row['give_frost'] and row['recv_frost']:
                profiles[row['give'][0]]['premiums'].append((row['recv_frost']/row['give_frost']-1)*100)
            g, r = row['give_net'], row['recv_net']
            if isinstance(g, (int,float)) and isinstance(r, (int,float)) and g > 0 and math.isfinite(g+r):
                baskets[row['basket']].append((r/g-1)*100)
        self.profiles = {}
        for name, profile in profiles.items():
            samples = profile.pop('premiums')
            self.profiles[name] = dict(profile, premium_samples=len(samples),
                                      quoted_frost_premium_pct=round(median(samples), 2) if samples else None)
        self.baskets = {key: {'samples': len(values), 'median_gain_pct': round(median(values),2),
                             'lower_quartile_gain_pct': round(sorted(values)[int((len(values)-1)*.25)],2)}
                        for key, values in baskets.items()}
        self.summary = {'offers': len(history), 'window_days': 7, 'last_updated': now,
                        'observed_since': min((r[0] for r in history), default=now),
                        'scope': 'Up to 10,000 distinct listings seen in the last 7 days; not completed trades or market-wide volume'}
        self.available = True
        self.next_update = now + 30

    def enrich(self, bot, rows):
        try:
            self.update(bot, rows)
        except (OSError, sqlite3.Error, ValueError, TypeError):
            self.available = False
            self.next_update = time.time() + 30
        for row in rows:
            def profiles(names):
                return [dict(name=n, **self.profiles.get(bot.canonical_slug(n),
                             {'wanted_offers':0,'offered_offers':0,'premium_samples':0,'quoted_frost_premium_pct':None}))
                        for n in dict.fromkeys(names)]
            row['market_evidence'] = {'available': self.available, 'summary': self.summary,
                'received': profiles(row.get('receive', [])), 'given': profiles(row.get('give', [])),
                'same_basket': self.baskets.get(basket_key(row.get('give', []), bot.canonical_slug))}
            for option in row.get('exit_advice', {}).get('downgrade_options', []):
                option['observed_wanted_offers'] = sum(self.profiles.get(bot.canonical_slug(n),{}).get('wanted_offers',0) for n in set(option['receive']))
        return rows


history = MarketHistory()
