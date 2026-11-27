"""Catalog research shared by Flask, the read-only live feed and Pages export."""
import time
from flask import jsonify, request
import bot

from downgrade_preferences import SHORTLIST


def canonical(name):
    return bot.canonical_slug(name)


def snapshot():
    names = {}
    for pet in bot.AMVGG_PETS:
        record, _ = bot._lookup(pet['name'])
        if record:
            names[record['_key']] = (pet['name'], pet['value'])
    shortlist = {canonical(n) for n in SHORTLIST}
    rows = []
    for key, record in bot.CATALOG.items():
        modifiers = [('Fly', record.get('flyable')), ('Ride', record.get('rideable')),
                     ('Neon', record.get('pumping') == 'neon'), ('Mega', record.get('pumping') == 'mega')]
        fallback = ' '.join([record['name']] + [n for n, enabled in modifiers if enabled])
        name, frost = names.get(key, (fallback, None))
        info = bot.pet_price_info(name)
        history = bot.sales_collector.get(key)
        demand = bot.pet_sales_demand(record)
        rows.append(dict(key=key, name=name, base=record['name'], type=record.get('type', 'item'),
                         frost=frost, net=info.get('net') if info.get('reliable') else None,
                         gross=info.get('gross'), confidence=info.get('confidence', 'none'),
                         median_daily=demand['median_daily'], weekly_total=demand['weekly_total'],
                         stale=demand.get('stale', False), period_start=demand.get('period_start'), period_end=demand.get('period_end'),
                         checked_at=history.get('fetched_at'), listing_low=history.get('listing_low'),
                         shortlist=canonical(name) in shortlist))
    return dict(rows=rows, generated_at=time.time(), price_saved_at=bot.CATALOG_PATH.stat().st_mtime,
                status=bot.sales_collector.status, progress=bot.sales_collector.progress())


def register(server):
    @server.get('/catalog-data')
    def catalog_data():
        return jsonify(snapshot())

    @server.post('/catalog-check')
    def catalog_check():
        payload = request.get_json(silent=True) or {}
        keys = payload.get('keys')
        if not isinstance(keys, list) or not 1 <= len(keys) <= 12 or any(not isinstance(k, str) or k not in bot.CATALOG for k in keys):
            return jsonify(error='Choose between 1 and 12 catalog variants.'), 400
        bot.sales_collector.start()
        for key in dict.fromkeys(keys):
            bot.sales_collector.enqueue(key, bot.CATALOG[key])
        return jsonify(status='Queued uncached variants. Checks run one at a time; recent results are reused.')
