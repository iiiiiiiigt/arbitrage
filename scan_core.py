"""Pure scanning core — no Flask import, so it is importable and benchmarkable.

Fixes vs the original app.py:
  1. prune(): trades expire, so the working set stops growing without bound.
  2. build_index(): per-trade parsing and _evaluate_trade() happen ONCE per
     refresh instead of once per row *per poll*.
  3. refresh(): the whole payload is computed once per batch and cached, so
     /status becomes a dict lookup instead of a full recompute.
"""
import time
from collections import Counter, defaultdict

TTL = 180.0         # seconds a trade stays in the working set
MAX_TRADES = 6000   # hard cap, newest win
ARCHIVE_MAX = 50    # expired-but-interesting trades kept so links stay usable
MIN_PROFIT_FOR_TOTAL = 10.0   # only trades at or above this count toward totals


def prune(trades, now=None, ttl=TTL, cap=MAX_TRADES):
    """Drop expired trades, then enforce a size cap (newest first)."""
    now = now if now is not None else time.time()
    live = {k: t for k, t in trades.items()
            if now - t.get('_seen_at', 0) <= ttl}
    if len(live) > cap:
        keep = sorted(live.items(), key=lambda kv: kv[1].get('_seen_at', 0),
                      reverse=True)[:cap]
        live = dict(keep)
    return live


def merge(trades, batch, now=None):
    """Insert a batch, returning how many were genuinely new."""
    now = now if now is not None else time.time()
    new = 0
    for t in batch:
        tid = t.get('id')
        if not tid:
            continue
        t['_seen_at'] = now
        if tid not in trades:
            new += 1
        trades[tid] = t
    return new


def _profit_summary(rows, threshold=MIN_PROFIT_FOR_TOTAL):
    """Total profit from qualifying trades at or above the threshold."""
    counted = [float(r.get('profit_usd') or 0) for r in rows
               if r.get('qualified') and (r.get('profit_usd') or 0) >= threshold]
    return {
        'threshold': threshold,
        'count': len(counted),
        'total': round(sum(counted), 2),
        'best': round(max(counted), 2) if counted else 0.0,
    }


def build_index(bot, observed, now=None):
    """Parse, evaluate and price every observed trade exactly once.

    Everything that depends only on the trade (not on the row being scored) is
    hoisted here, so the per-row loop is left with cheap Counter arithmetic.

    Returns:
      by_slug: slug -> list of prepared candidates that require that slug
    """
    now = now if now is not None else time.time()
    by_slug = defaultdict(list)

    slug_cache = {}
    frost_cache = {}

    def frost(name):
        v = frost_cache.get(name)
        if v is None:
            v = bot.AMVGG_MAP.get(bot.canonical_slug(name), 0)
            frost_cache[name] = v
        return v

    def liquid(name):
        from opportunities import cash_demand_ok
        info = bot.pet_price_info(name)
        return cash_demand_ok(info)

    for t in observed:
        if now - t.get('_seen_at', 0) > 120:
            continue
        give, ok, signs = bot._safe_pet_names(t.get('lookingFor', []))
        if not ok or signs or not give:
            continue

        required = Counter()
        for p in give:
            slug = slug_cache.get(p)
            if slug is None:
                slug = slug_cache[p] = bot.canonical_slug(p)
            required[slug] += 1

        result, _ = bot._evaluate_trade(t, give)
        if not result:
            continue

        side = give + result['receive']
        give_max = max(map(frost, give), default=0)
        recv_max = max(map(frost, result['receive']), default=0)

        prep = {
            'id': t.get('id'), 'give': give,
            'required': required, 'result': result,
            # row-independent precomputation
            'all_priced': all(frost(p) > 0 for p in side),
            'give_max': give_max,
            'recv_max': recv_max,
            'shrink': recv_max < give_max,
            'next_liquid': all(liquid(p) for p in result['receive']),
            'extra_net': result['profit_usd'],
        }
        for slug in required:
            by_slug[slug].append(prep)
    return by_slug


def exit_advice_fast(bot, opportunities, row, by_slug, now=None):
    """Same decision logic as opportunities.exit_advice, but candidate-first.

    The original scanned every observed trade for every row. Here we start
    from the slugs in the received basket and only touch trades that want
    something we actually hold.
    """
    now = now if now is not None else time.time()
    basket = row['receive']
    available = Counter(bot.canonical_slug(p) for p in basket)

    details = []
    for name, quantity in Counter(basket).items():
        info = bot.pet_price_info(name)
        sig = info.get('demand_signal')
        demand_ok = opportunities.cash_demand_ok(info)
        details.append({'name': name, 'quantity': quantity,
                        'sales_per_day': info.get('sales_per_day', 0),
                        'median_daily': info.get('demand', {}).get('median_daily'),
                        'reliable': info.get('reliable', False),
                        'demand_ok': demand_ok,
                        'demand_label': (sig or {}).get('label'),
                        'demand_source': (sig or {}).get('source')})
    liquid = bool(details) and all(d['reliable'] and d['demand_ok']
                                   for d in details)

    seen = set()
    options = []
    for slug in available:
        for prep in by_slug.get(slug, ()):
            pid = prep['id']
            if pid in seen or pid == row['trade_id']:
                continue
            seen.add(pid)
            # set semantics of `required - available` without allocating a Counter
            req = prep['required']
            if len(req) > len(available):
                continue
            if any(available[k] < n for k, n in req.items()):
                continue
            if not prep['all_priced']:
                continue
            if not prep['shrink']:
                continue
            if prep['extra_net'] < max(2, row['recv_usd'] * .05):
                continue
            options.append({'trade_id': prep['result']['trade_id'],
                            'give': prep['give'],
                            'receive': prep['result']['receive'],
                            'extra_net': prep['extra_net'],
                            'basket_net_after': round(row['recv_usd'] + prep['extra_net'], 2),
                            'extra_pct': round(prep['extra_net'] / max(prep['result']['give_usd'], .01) * 100, 1),
                            'liquid': prep['next_liquid'] and all(
                                opportunities.cash_demand_ok(bot.pet_price_info(p)) for p in basket
                                if available[bot.canonical_slug(p)] > req[bot.canonical_slug(p)])})
    opportunities.rank_options(bot, options)
    best = options[0] if options else None

    if best and best['liquid']:
        decision = 'Consider one more downgrade'
        reason = ('A recently observed offer fits your received quantities, adds net '
                  'value, and leads to pets with reported resale demand. Verify that '
                  'the offer is still open.')
    elif liquid:
        decision = 'Favor selling the received pets'
        reason = ('Reported resale demand supports an exit. No qualifying follow-up '
                  'with sufficient margin and resale demand was observed.')
    elif best:
        decision = 'Profitable follow-up found — verify cash demand'
        reason = 'An observed downgrade fits your received pets and adds estimated net value, but the resulting holdings lack sufficient StarPets sales evidence.'
    else:
        decision = 'Insufficient evidence — review before taking this trade'
        reason = ('Resale demand is low or missing, and no qualifying liquid follow-up '
                  'was observed. The displayed gain may be difficult to realize.')
    return {'decision': decision, 'reason': reason,
            'sell_now_net': row['recv_usd'],
            'demand_passed': bool(liquid or (best and best['liquid'])),
            'received_demand': details, 'recent_wanted_offers': len(seen),
            'next_step': best, 'downgrade_options': options[:3],
            'note': ('Cash exit requires a seven-day median of at least 2 observed StarPets sales/day per variant, including retained pets. AMVGG ratings indicate trading interest only. Follow-up offers are alternatives, not cumulative profits. Estimates assume a 30% commission.')}


def refresh(bot, opportunities, state):
    """Recompute the full /status payload. Call once per batch."""
    now = time.time()
    previous = state.get('payload') or {}
    before = state['trades']
    live = prune(before, now)
    # Expired rows that were actually worth looking at are archived rather than
    # dropped, so their links and details stay reachable after they leave the
    # live window.
    dropped = [tid for tid in before if tid not in live]
    if dropped:
        index = {}
        for key in ('results', 'near_misses', 'target_trades'):
            for row in (previous.get(key) or []):
                index[row.get('trade_id')] = row
        archive = state.setdefault('archive', [])
        known = {a['trade_id'] for a in archive}
        for tid in dropped:
            row = index.get(tid)
            if not row or tid in known:
                continue
            # keep the full row: price, frost, profit, demand labels and all
            keep = ('trade_id', 'author', 'give', 'receive', 'give_usd', 'recv_usd',
                    'profit_usd', 'profit_pct', 'give_frost', 'recv_frost',
                    'stars', 'sellability', 'reason', 'qualified', 'unpriced')
            entry = {k: row.get(k) for k in keep}
            entry['expired_at'] = now
            adv = row.get('exit_advice') or {}
            entry['exit_advice'] = {k: adv.get(k) for k in
                                    ('decision', 'reason', 'sell_now_net', 'demand_passed')}
            entry['demand'] = {
                name: {
                    'label': (val or {}).get('signal', {}).get('label'),
                    'level': (val or {}).get('signal', {}).get('level'),
                    'median_daily': (val or {}).get('median_daily'),
                }
                for name, val in (row.get('item_demand') or {}).items()
            }
            archive.append(entry)
            known.add(tid)
        if len(archive) > ARCHIVE_MAX:
            del archive[:-ARCHIVE_MAX]
    state['trades'] = live
    observed = list(state['trades'].values())

    by_slug = build_index(bot, observed, now)

    stats = {}
    explore = state.get('mode') == 'all'
    results = bot.rank_trades(observed, state['inventory'], min_stars=1, stats=stats,
                              ignore_inventory=explore)
    near = bot.get_near_misses(observed, state['inventory'], limit=30,
                               ignore_inventory=explore)

    def assess(row, margin):
        advice = exit_advice_fast(bot, opportunities, row, by_slug, now)
        out = dict(row, exit_advice=advice, margin_qualified=margin,
                   qualified=bool(margin and advice['demand_passed']))
        if margin and not advice['demand_passed']:
            missing = [d['name'] for d in advice['received_demand']
                       if d['median_daily'] is None]
            out['reason'] = (('Demand history unavailable: ' + ', '.join(missing))
                             if missing else
                             'Received pets do not pass the resale demand check.')
        return bot.with_demand(out)

    results = [assess(t, True) for t in results]
    near = [assess(t, False) for t in near]
    targets = []
    if state['target_pet']:
        targets = [assess(t, True) for t in
                   bot.get_target_trades(observed, state['target_pet'], limit=20)]

    # How long ago each row was last seen in the upstream feed. The working set
    # expires, so a card can stay on screen after it stops being refreshed;
    # surfacing the age keeps that honest instead of looking live.
    for row in results + near + targets:
        src = state['trades'].get(row.get('trade_id'))
        if src is not None:
            row['seen_age'] = round(now - src.get('_seen_at', now), 1)

    state['payload'] = {
        'running': state['running'], 'inventory': sorted(state['inventory']),
        'mode': state.get('mode', 'inventory'),
        'target_pet': state['target_pet'],
        'total_fetches': state['total_fetches'], 'total_new': state['total_new'],
        'last_new': state['last_new'], 'last_batch': state['last_batch'],
        'trade_count': len(state['trades']),
        'total_scanned': state.get('total_scanned', 0),
        'in_window': len(state['trades']),
        'profit_summary': _profit_summary(results),
        'expired_summary': _profit_summary(state.get('archive') or []),
        'results': results, 'near_misses': near, 'target_trades': targets,
        'stats': stats,
        'elapsed': round(now - state['started_at'], 1) if state['started_at'] else 0,
        'rate_limited': state['rate_limited'],
        'expired': list(state.get('archive') or []),
        'last_fetch_age': (round(now - state['last_fetch_at'], 1)
                           if state['last_fetch_at'] else None),
        'built_at': now,
    }
    return state['payload']
