from flask import Flask, render_template, request, jsonify
from urllib.parse import quote
import json, threading, time
import bot

app = Flask(__name__)

SUFFIXES = (" Fly", " Ride", " Neon", " Mega")

def build_grouped_pets():
    all_names = {p["name"]: p["value"] for p in bot.AMVGG_PETS}
    def find_base(name):
        for suffix in SUFFIXES:
            if name.endswith(suffix):
                stripped = name[:-len(suffix)]
                if stripped in all_names: return find_base(stripped)
        return name
    grouped = {}
    for name, value in all_names.items():
        base = find_base(name)
        suffix = name[len(base):].strip()
        if base not in grouped:
            grouped[base] = {"name": base, "image": f"https://amvgg.com/items/{quote(base, safe='')}.webp", "variants": {}}
        grouped[base]["variants"][suffix] = round(value, 2)
    return sorted(grouped.values(), key=lambda g: -max(g["variants"].values()))

GROUPED = None

STATE = {
    "running": False, "thread": None, "trades": {}, "results": [],
    "near_misses": [], "target_trades": [], "stats": {}, "inventory": set(),
    "target_pet": "", "started_at": 0, "last_fetch_at": 0, "total_fetches": 0,
    "total_new": 0, "last_new": 0, "last_batch": 0, "rate_limited": False,
}
LOCK = threading.Lock()

def background_scanner():
    rate_limit_streak = 0
    empty_streak = 0
    while STATE["running"]:
        batch, status = bot.fetch_one_batch()
        if status == 429:
            rate_limit_streak += 1
            with LOCK: STATE["rate_limited"] = True
            time.sleep(min(60, 10 * rate_limit_streak))
            continue
        rate_limit_streak = 0
        if status != 200: time.sleep(5); continue

        with LOCK:
            STATE["rate_limited"] = False
            new = 0
            for t in batch:
                tid = t.get("id")
                t['_seen_at'] = time.time()
                if tid in STATE['trades']:
                    STATE['trades'][tid] = t
                if tid and tid not in STATE["trades"]:
                    STATE["trades"][tid] = t
                    new += 1
            STATE["total_fetches"] += 1
            STATE["total_new"]     += new
            STATE["last_fetch_at"]  = time.time()
            STATE["last_new"]       = new
            STATE["last_batch"]     = len(batch)

            if new == 0: empty_streak += 1
            else: empty_streak = 0

            if new > 0 or STATE["total_fetches"] % 5 == 0:
                all_trades = list(STATE["trades"].values())
                stats = {}
                STATE["results"]     = bot.rank_trades(all_trades, STATE["inventory"], min_stars=1, stats=stats)
                STATE["stats"]       = stats
                STATE["near_misses"] = bot.get_near_misses(all_trades, STATE["inventory"], limit=30)
                if STATE["target_pet"]:
                    STATE["target_trades"] = bot.get_target_trades(all_trades, STATE["target_pet"], limit=20)
        time.sleep(2 if empty_streak < 5 else 4)

def start_scanner(inventory, target_pet=""):
    with LOCK:
        STATE["running"] = False
        STATE["trades"] = {}; STATE["results"] = []; STATE["near_misses"] = []
        STATE["target_trades"] = []; STATE["stats"] = {}
        STATE["inventory"] = set(inventory); STATE["target_pet"] = target_pet or ""
        STATE["started_at"] = time.time(); STATE["last_fetch_at"] = 0
        STATE["total_fetches"] = 0; STATE["total_new"] = 0; STATE["last_new"] = 0
        STATE["last_batch"] = 0; STATE["rate_limited"] = False
        time.sleep(0.1)
        STATE["running"] = True
        STATE["thread"] = threading.Thread(target=background_scanner, daemon=True)
        STATE["thread"].start()

@app.before_request
def init():
    global GROUPED
    if GROUPED is None:
        GROUPED = build_grouped_pets()
        print(f"Loaded {len(bot.AMVGG_PETS)} variants -> {len(GROUPED)} base pets")

@app.route("/")
def index(): return render_template("index.html", pets=GROUPED)

@app.route("/pet-options")
def pet_options(): return jsonify(GROUPED)

@app.route('/research')
def research():
    from opportunities import research
    with LOCK:
        trades = list(STATE['trades'].values())
        holdings = list(STATE['inventory'])
    selected = request.args.getlist('holding')
    baseline = request.args.get('baseline', '').strip()
    return jsonify(research(bot, baseline, selected or ([baseline] if baseline else holdings), trades))

@app.route("/find", methods=["POST"])
def find():
    try:
        inventory = set(json.loads(request.form.get("inventory", "[]")))
        target    = request.form.get("target_pet", "").strip()
    except Exception: inventory, target = set(), ""
    if not inventory:
        return render_template("results_clean.html", error="Select at least one pet.", inventory=[], target="")
    start_scanner(inventory, target_pet=target)
    return render_template("results_clean.html", inventory=sorted(inventory), target=target)

@app.route("/status")
def status():
    from opportunities import assess_demand
    with LOCK:
        elapsed = time.time() - STATE["started_at"] if STATE["started_at"] else 0
        observed = list(STATE['trades'].values())
        evaluated = [assess_demand(bot, t, observed, True) for t in STATE['results']]
        results = [t for t in evaluated if t['qualified']]
        reviews = [t for t in evaluated if not t['qualified']]
        reviews += [assess_demand(bot, t, observed, False) for t in STATE['near_misses']]
        targets = [assess_demand(bot, t, observed, True) for t in STATE['target_trades']]
        return jsonify({
            "running": STATE["running"], "inventory": sorted(STATE["inventory"]),
            "target_pet": STATE["target_pet"], "total_fetches": STATE["total_fetches"],
            "total_new": STATE["total_new"], "last_new": STATE["last_new"],
            "last_batch": STATE["last_batch"], "trade_count": len(STATE["trades"]),
            "results": results, "near_misses": reviews,
            "target_trades": [bot.with_demand(t) for t in targets], "stats": STATE["stats"],
            "elapsed": round(elapsed, 1), "rate_limited": STATE["rate_limited"],
            "last_fetch_age": round(time.time() - STATE["last_fetch_at"], 1) if STATE["last_fetch_at"] else None,
        })

@app.route("/stop", methods=["POST"])
def stop():
    with LOCK: STATE["running"] = False
    return jsonify({"ok": True})

@app.route("/accept", methods=["POST"])
def accept():
    trade_id = request.form.get("trade_id")
    if not trade_id: return jsonify({"success": False, "error": "No trade ID"})
    success = bot.accept_trade(trade_id)
    return jsonify({"success": success})

@app.route("/update_inventory", methods=["POST"])
def update_inventory():
    try: new_inv = set(json.loads(request.form.get("inventory", "[]")))
    except Exception: return jsonify({"ok": False})
    with LOCK:
        STATE["inventory"] = new_inv
        all_trades = list(STATE["trades"].values())
        stats = {}
        STATE["results"] = bot.rank_trades(all_trades, new_inv, min_stars=1, stats=stats)
        STATE["stats"] = stats
        STATE["near_misses"] = bot.get_near_misses(all_trades, new_inv, limit=30)
        if STATE["target_pet"]:
            STATE["target_trades"] = bot.get_target_trades(all_trades, STATE["target_pet"], limit=20)
    return jsonify({"ok": True})

@app.route("/set_target", methods=["POST"])
def set_target():
    with LOCK:
        STATE["target_pet"] = request.form.get("target_pet", "").strip()
        if STATE["target_pet"]: STATE["target_trades"] = bot.get_target_trades(list(STATE["trades"].values()), STATE["target_pet"], limit=20)
        else: STATE["target_trades"] = []
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(debug=True, port=5000, threaded=True)
