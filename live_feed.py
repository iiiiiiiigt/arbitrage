"""Read-only market feed. Run separately from the private Flask dashboard."""
import threading
import queue
import time
from flask import Flask, jsonify, request
import bot
import opportunities
import scan_core
from market_learning import history as market_history

feed = Flask(__name__, static_folder=None)
import catalog_checker
catalog_checker.register(feed)
lock = threading.Lock()
changed = threading.Condition(lock)
state = {'version': 0, 'generated_at': 0, 'trades': [], 'sources': {'collector': 'Starting'}}
stop = threading.Event()

@feed.after_request
def cors(response):
    response.headers['Access-Control-Allow-Origin'] = 'https://iiiiiiiigt.github.io'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response

@feed.get('/feed')
def read_feed():
    after = request.args.get('after', type=int)
    with changed:
        if after is not None:
            changed.wait_for(lambda: state['version'] != after, timeout=12)
        return jsonify(state)


def collect():
    bot.sales_collector.start()
    events = queue.Queue(maxsize=8)
    statuses = {'AMVGG': 'Starting', 'Elvebredd': 'Starting'}
    def source_loop(source):
        while not stop.is_set():
            try:
                if source == 'AMVGG':
                    batch, status, _ = bot._fetch_amvgg_batch(use_proxy=False)
                    delay = max(30, bot.LAST_RETRY_AFTER) if status == 429 else 5 if status != 200 else 2
                else:
                    batch = opportunities.elvebredd_trades()
                    status = 200 if opportunities.source_status.startswith('Connected') else 0
                    delay = 10
                events.put((source, batch, status, time.time()))
            except Exception:
                delay = 10
            stop.wait(delay)
    for source in statuses:
        threading.Thread(target=source_loop, args=(source,), daemon=True, name='feed-'+source).start()
    seen = {}
    recent = {}
    history_started = time.time()
    while not stop.is_set():
        try:
            try:
                source, batch, status, arrived = events.get(timeout=2)
                statuses[source] = 'Connected' if status == 200 else 'Unavailable / backing off'
                for trade in batch:
                    if trade.get('id'):
                        trade['_seen_at'] = arrived
                        seen[str(trade['id'])] = trade
            except queue.Empty:
                pass
            now = time.time()
            seen = {k:t for k,t in seen.items() if now-t['_seen_at'] < 120}
            observed = list(seen.values())
            began = time.perf_counter()
            index = scan_core.build_index(bot, observed, now)
            rows = []
            for trade in observed:
                give, ok, _ = bot._safe_pet_names(trade.get('lookingFor', []))
                if not ok or not give:
                    continue
                row, _ = bot._evaluate_trade(trade, give)
                qualified = row is not None
                if row is None:
                    near = bot.get_near_misses([trade], give)
                    if not near:
                        continue
                    row = near[0]
                advice = scan_core.exit_advice_fast(bot, opportunities, row, index, now)
                row = opportunities.assess_demand(bot, row, observed, qualified, advice=advice)
                row['observed_at'] = trade['_seen_at']
                rows.append(row)
            market_history.enrich(bot, rows)
            for row in rows:
                recent[row["trade_id"]] = row
            recent = {k:r for k,r in recent.items() if now-r["observed_at"] < 600}
            if len(recent) > 6000:
                recent = dict(sorted(recent.items(), key=lambda item: item[1]["observed_at"], reverse=True)[:6000])
            with changed:
                state.update(version=state['version']+1, generated_at=time.time(), trades=rows,
                             recent_trades=list(recent.values()), history_started=history_started,
                             market_learning=market_history.summary, scoring_ms=round((time.perf_counter()-began)*1000, 1),
                             sources={'collector': 'Running' if 'Connected' in statuses.values() else 'Upstream unavailable / backing off',
                                      **statuses, 'StarPets': bot.sales_collector.status})
                changed.notify_all()
        except Exception:
            with lock:
                state['sources']['collector'] = 'Collector error; retrying'
            stop.wait(2)

if __name__ == '__main__':
    threading.Thread(target=collect, daemon=True).start()
    # No dashboard, acceptance endpoint, filesystem route or credentials exposed.
    feed.run(host='127.0.0.1', port=5051, debug=False, use_reloader=False)
