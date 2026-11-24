"""Read-only market feed. Run separately from the private Flask dashboard."""
import threading
import time
from flask import Flask, jsonify
import bot
import opportunities

feed = Flask(__name__, static_folder=None)
lock = threading.Lock()
state = {'generated_at': 0, 'trades': [], 'sources': {'collector': 'Starting'}}
stop = threading.Event()

@feed.after_request
def cors(response):
    response.headers['Access-Control-Allow-Origin'] = 'https://iiiiiiiigt.github.io'
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response

@feed.get('/feed')
def read_feed():
    with lock:
        return jsonify(state)

def collect():
    bot.sales_collector.start()
    # This collector sends public market requests only, without proxy rotation.
    bot.PROXIES = []
    seen = {}
    while not stop.is_set():
        try:
            batch, status = bot.fetch_one_batch()
            now = time.time()
            for trade in batch:
                if trade.get('id'):
                    trade['_seen_at'] = now
                    seen[str(trade['id'])] = trade
            seen = {k:t for k,t in seen.items() if now-t['_seen_at'] < 120}
            rows = []
            observed = list(seen.values())
            for trade in observed:
                give, ok, _ = bot._safe_pet_names(trade.get('lookingFor', []))
                if not ok or not give:
                    continue
                row, _ = bot._evaluate_trade(trade, give)
                if row:
                    row['qualified'] = True
                else:
                    near = bot.get_near_misses([trade], give)
                    if not near:
                        continue
                    row = near[0]
                    row['qualified'] = False
                rows.append(opportunities.assess_demand(bot, row, observed, row['qualified']))
            with lock:
                state.update(generated_at=now, trades=rows,
                             sources={'collector': 'Running' if status==200 else 'Upstream unavailable / backing off',
                                      'Elvebredd': opportunities.source_status,
                                      'StarPets': bot.sales_collector.status})
            stop.wait(30 if status == 429 else 5 if status != 200 else 2)
        except Exception:
            with lock:
                state['sources']['collector'] = 'Collector error; retrying'
            stop.wait(10)

if __name__ == '__main__':
    threading.Thread(target=collect, daemon=True).start()
    # No dashboard, acceptance endpoint, filesystem route or credentials exposed.
    feed.run(host='127.0.0.1', port=5051, debug=False, use_reloader=False)
