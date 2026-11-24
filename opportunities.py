"""Read-only trade source and catalog-based downgrade research."""
import time
from collections import Counter
import requests

_next_fetch = 0
source_status = 'Waiting for first fetch'

def exit_advice(bot, trade, observed, now=None):
    """One-step advice for the received basket; never assumes extra inventory."""
    now = time.time() if now is None else now
    basket = trade['receive']
    available = Counter(bot.canonical_slug(p) for p in basket)
    details = []
    for name, quantity in Counter(basket).items():
        info = bot.pet_price_info(name)
        details.append({'name': name, 'quantity': quantity,
                        'sales_per_day': info.get('sales_per_day', 0),
                        'median_daily': info.get('demand', {}).get('median_daily'),
                        'reliable': info.get('reliable', False)})
    liquid = bool(details) and all(p['reliable'] and p['sales_per_day'] >= 2 for p in details)
    options = []
    wanted = set()
    for t in observed:
        if t.get('id') == trade['trade_id'] or now - t.get('_seen_at', 0) > 120:
            continue
        give, ok, signs = bot._safe_pet_names(t.get('lookingFor', []))
        if not ok or signs or not give:
            continue
        required = Counter(bot.canonical_slug(p) for p in give)
        if set(required) & set(available):
            wanted.add(t.get('id'))
        if required - available:
            continue
        result, _ = bot._evaluate_trade(t, give)
        if not result:
            continue
        # A successive downgrade must reduce the highest individual frost value.
        value = lambda name: bot.AMVGG_MAP.get(bot.canonical_slug(name), 0)
        if any(value(p) <= 0 for p in give + result['receive']):
            continue
        if max(map(value, result['receive'])) >= max(map(value, give)):
            continue
        next_liquid = all(bot.pet_price_info(p).get('sales_per_day', 0) >= 2
                          for p in result['receive'])
        if result['profit_usd'] < max(2, trade['recv_usd'] * .05):
            continue
        options.append({'trade_id': result['trade_id'], 'give': give,
                        'receive': result['receive'], 'extra_net': result['profit_usd'],
                        'basket_net_after': round(trade['recv_usd'] + result['profit_usd'], 2),
                        'liquid': next_liquid})
    options.sort(key=lambda o: (not o['liquid'], -o['extra_net']))
    best = options[0] if options else None
    if best and best['liquid']:
        decision = 'Consider one more downgrade'
        reason = 'A recently observed offer fits your received quantities, adds net value, and leads to pets with reported resale demand. Verify that the offer is still open.'
    elif liquid:
        decision = 'Favor selling the received pets'
        reason = 'Reported resale demand supports an exit. No qualifying follow-up with sufficient margin and resale demand was observed.'
    else:
        decision = 'Insufficient evidence — review before taking this trade'
        reason = 'Resale demand is low or missing, and no qualifying liquid follow-up was observed. The displayed gain may be difficult to realize.'
    return {'decision': decision, 'reason': reason, 'sell_now_net': trade['recv_usd'],
            'received_demand': details, 'recent_wanted_offers': len(wanted),
            'next_step': best, 'note': 'Sellability requires a verified seven-day median of at least 2 sales/day for each received variant. Missing history does not pass this check. Requests in the last 2 minutes indicate interest, not completed trades. One-step estimates include a 30% commission assumption.'}

def elvebredd_trades():
    global _next_fetch, source_status
    if time.monotonic() < _next_fetch:
        return []
    _next_fetch = time.monotonic() + 10
    try:
        r = requests.get('https://elvebredd.com/api/recent-listings',
                         params={'game': 'Adopt Me', 'limit': 30}, timeout=12)
        if r.status_code != 200:
            source_status = f'HTTP {r.status_code}'
            try:
                delay = max(120, float(r.headers.get('Retry-After', 120)))
            except ValueError:
                from email.utils import parsedate_to_datetime
                try:
                    delay = max(120, parsedate_to_datetime(r.headers['Retry-After']).timestamp() - time.time())
                except (ValueError, TypeError, KeyError):
                    delay = 120
            _next_fetch = time.monotonic() + delay
            return []
        data = r.json()
        if not data.get('ok') or not isinstance(data.get('listings'), list):
            raise ValueError('Unexpected listing response')
        out = []
        for t in data['listings']:
            if t.get('acceptedAt') or t.get('ownerCancelledAt') or t.get('addValue') or t.get('other', {}).get('addRules', {}).get('mustBeCountered'):
                continue
            def side(items):
                result = []
                for p in items:
                    if p.get('mutation') or not p.get('name') or p['name'] in ('Add', 'Offer'):
                        raise ValueError('Unspecified trade')
                    a = p.get('attributes', {})
                    variant = ''.join(k[0] for k in ('fly','ride','neon','mega') if a.get(k))
                    result.append({'name': p['name'], 'type': variant})
                return result
            try:
                out.append({'id': 'elvebredd:' + str(t['id']), 'source': 'Elvebredd',
                    'authorName': t.get('ownerUsername', '?'),
                    'offering': side(t['ownerGive']), 'lookingFor': side(t['ownerGet'])})
            except (KeyError, TypeError, ValueError):
                continue
        source_status = f'Connected • {len(out)} complete offers in last batch'
        return out
    except (requests.RequestException, ValueError, TypeError):
        source_status = 'Unavailable; retrying in two minutes'
        _next_fetch = time.monotonic() + 120
        return []

def research(bot, baseline, holdings, trades):
    ref = bot.pet_price_info(baseline)
    frost = bot.AMVGG_MAP.get(bot.canonical_slug(baseline), 0)
    density = ref.get('net', 0) / frost if frost and ref.get('reliable') else None
    rows = []
    for pet in bot.AMVGG_PETS:
        name = pet['name']; value = pet['value']; info = bot.pet_price_info(name)
        if density is None or value <= 0 or value >= frost or not info.get('reliable'):
            continue
        ratio = info['net'] / value
        if ratio <= density:
            continue
        rows.append({'name': name, 'gross': info['gross'], 'net': round(info['net'], 2),
            'frost': value, 'net_per_frost': round(ratio, 2),
            'uplift_pct': round((ratio / density - 1)*100, 1) if density else None,
            'sales_per_day': info['sales_per_day'], 'confidence': info['confidence']})
    rows.sort(key=lambda r: (r['sales_per_day'] <= 0, -r['net_per_frost']))
    available = Counter(bot.canonical_slug(p) for p in holdings)
    candidates = []
    for t in trades:
        give, ok, _ = bot._safe_pet_names(t.get('lookingFor', []))
        if not ok or Counter(bot.canonical_slug(p) for p in give) - available:
            continue
        result, _ = bot._evaluate_trade(t, give)
        if result:
            candidates.append(result)
    candidates.sort(key=lambda r: -r['profit_usd'])
    infos = [bot.pet_price_info(p) for p in holdings]
    net = round(sum(i['net'] for i in infos),2) if infos and all(i.get('reliable') for i in infos) else None
    return {'baseline': baseline, 'baseline_priced': density is not None,
        'candidates': rows, 'sell_now_net': net,
        'next_trades': candidates[:10], 'recommendation':
        'Compare the available next trades below before selling.' if candidates else
        'No qualifying next trade observed. Selling is the supported exit; waiting has no demonstrated extra gain.',
        'source_status': source_status,
        'note': 'Saved catalog estimates, 30% commission assumption. Rankings assume equal frost exchange and do not establish executable trades. Demand may be stale or missing. Net gain compares resale values, not profit against your purchase cost.'}
