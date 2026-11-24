"""Adopt Me trade research dashboard — optimized scanner.

Changes vs the original app.py:
  * scan_core: trades expire, work is hoisted, /status serves a cached payload
  * limit=100 (the endpoint's hard max) instead of 20
  * ~0.25s poll instead of 2s, with jittered backoff
  * cursor backfill at startup so a cold start sees history, not one page
  * waitress instead of the werkzeug dev server (no debugger RCE)
"""
import json
import os
import random
import threading
import time
from urllib.parse import quote

from flask import Flask, abort, jsonify, render_template, request
from urllib.parse import quote, urlparse

import bot
import demand_resolver
import opportunities
import scan_core

app = Flask(__name__)

SUFFIXES = (" Fly", " Ride", " Neon", " Mega")
GROUPED = None

STATE = {
    "running": False, "thread": None, "trades": {}, "results": [],
    "near_misses": [], "target_trades": [], "stats": {}, "inventory": set(),
    "target_pet": "", "started_at": 0, "last_fetch_at": 0, "total_fetches": 0,
    "total_new": 0, "last_new": 0, "last_batch": 0, "rate_limited": False,
    "payload": None, "gen": 0,
    # monotonic: unique offers ingested this scan. The working set expires, so
    # the live count rises and falls; this one must never go backwards.
    "total_scanned": 0,
    "archive": [],
}
LOCK = threading.Lock()

# Pacing. Measured: AMVGG's limiter is a volume window, not an instantaneous
# rate. At limit=100 even a 3s interval yields ~29 trades/s of ingest capacity
# against a feed that produces ~4.4 trades/s, so faster polling buys nothing
# but 429s. Override with POLL_INTERVAL if you want to experiment.
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "3.0"))


def start_demand_refresh(interval=1800, starpets_interval=6 * 3600, startup_delay=90):
    """Keep demand data warm while the dashboard runs.

    amvgg: cheap page fetch, TTL 12h.
    StarPets: full catalog crawl, TTL 24h. Starts shortly after boot and is
    re-checked periodically; the TTL stops it running on every restart.

    Disable with DEMAND_AUTOREFRESH=0, or DEMAND_STARPETS_AUTOREFRESH=0 for the
    expensive half only.
    """
    if os.environ.get("DEMAND_AUTOREFRESH", "1") == "0":
        print("demand auto-refresh disabled", flush=True)
        return []

    bot.sales_collector.start()

    threads = []

    def cheap_loop():
        while True:
            try:
                import demand_refresh
                if demand_refresh.refresh_amvgg():
                    print("[demand] amvgg ratings refreshed", flush=True)
            except Exception:
                pass
            time.sleep(interval)

    t = threading.Thread(target=cheap_loop, name="demand-refresh", daemon=True)
    t.start()
    threads.append(t)

    if os.environ.get("DEMAND_STARPETS_AUTOREFRESH", "0") == "0":
        print("StarPets auto-refresh disabled", flush=True)
        return threads

    def starpets_loop():
        # let the dashboard finish booting before a multi-minute crawl starts
        time.sleep(startup_delay)
        # optional bounding, so a first run can be limited to a few pets
        names = os.environ.get("DEMAND_STARPETS_NAMES") or None
        limit_env = os.environ.get("DEMAND_STARPETS_LIMIT")
        limit = int(limit_env) if limit_env and limit_env.isdigit() else None
        # default: honour the 24h TTL so restarts do not re-crawl every time.
        # DEMAND_STARPETS_FORCE=1 crawls on every boot instead.
        force_on_start = os.environ.get("DEMAND_STARPETS_FORCE", "0") == "1"
        first = True
        while True:
            try:
                import demand_refresh
                if demand_refresh.refresh_starpets(force=(force_on_start and first),
                                                   names=names, limit=limit):
                    n = bot.reload_catalog()
                    demand_resolver.reset_cache()
                    print(f"[demand] StarPets refreshed, catalog reloaded "
                          f"({n} variants)", flush=True)
            except Exception:
                pass
            first = False
            time.sleep(starpets_interval)

    t2 = threading.Thread(target=starpets_loop, name="starpets-refresh", daemon=True)
    t2.start()
    threads.append(t2)
    return threads


def build_grouped_pets():
    all_names = {p["name"]: p["value"] for p in bot.AMVGG_PETS}

    def find_base(name):
        for suffix in SUFFIXES:
            if name.endswith(suffix):
                stripped = name[:-len(suffix)]
                if stripped in all_names:
                    return find_base(stripped)
        return name

    grouped = {}
    for name, value in all_names.items():
        base = find_base(name)
        suffix = name[len(base):].strip()
        if base not in grouped:
            grouped[base] = {"name": base,
                             "image": f"https://amvgg.com/items/{quote(base, safe='')}.webp",
                             "variants": {}}
        grouped[base]["variants"][suffix] = round(value, 2)
    return sorted(grouped.values(), key=lambda g: -max(g["variants"].values()))


def background_scanner(gen):
    streak = 0
    # one-shot backfill: walk the cursor back a couple of pages so the first
    # scan has real history instead of a single 100-trade window
    try:
        history = bot.backfill(pages=2)
        if history:
            with LOCK:
                STATE["total_scanned"] += scan_core.merge(STATE["trades"], history)
                STATE["last_batch"] = len(history)
                scan_core.refresh(bot, opportunities, STATE)
    except Exception:
        pass

    while STATE["running"] and STATE["gen"] == gen:
        batch, status = bot.fetch_one_batch()

        if status == 429:
            streak += 1
            with LOCK:
                STATE["rate_limited"] = True
                # keep whatever the other source returned this cycle instead of
                # throwing it away while we back off
                if batch:
                    STATE["total_scanned"] += scan_core.merge(STATE["trades"], batch)
                    STATE["last_batch"] = len(batch)
            # Honor the server's own Retry-After when it gives one.
            wait = bot.LAST_RETRY_AFTER or min(30.0, 2.0 ** streak)
            time.sleep(wait + random.uniform(0.2, 1.0))
            continue

        streak = 0

        if status != 200:
            time.sleep(1.0)
            continue

        with LOCK:
            STATE["rate_limited"] = False
            new = scan_core.merge(STATE["trades"], batch)
            STATE["total_scanned"] += new
            STATE["total_fetches"] += 1
            STATE["total_new"] += new
            STATE["last_fetch_at"] = time.time()
            STATE["last_new"] = new
            STATE["last_batch"] = len(batch)
            if new or STATE["total_fetches"] % 5 == 0:
                scan_core.refresh(bot, opportunities, STATE)

        time.sleep(POLL_INTERVAL)


def start_scanner(inventory, target_pet=""):
    """Start a fresh scan, making sure the previous thread actually stops.

    Previously the old thread was only nudged with a 100 ms sleep; because it
    spends most of its time in a network call, it usually missed the window and
    kept running alongside the new one -- two pollers, double request rate.
    """
    with LOCK:
        STATE["running"] = False
        STATE["gen"] += 1
        gen = STATE["gen"]
        previous = STATE["thread"]
        STATE["trades"] = {}
        STATE["results"] = []
        STATE["near_misses"] = []
        STATE["target_trades"] = []
        STATE["stats"] = {}
        STATE["payload"] = None
        STATE["archive"] = []
        STATE["inventory"] = set(inventory)
        STATE["target_pet"] = target_pet or ""
        STATE["started_at"] = time.time()
        STATE["last_fetch_at"] = 0
        STATE["total_fetches"] = 0
        STATE["total_new"] = 0
        STATE["total_scanned"] = 0
        STATE["last_new"] = 0
        STATE["last_batch"] = 0
        STATE["rate_limited"] = False

    if previous is not None and previous.is_alive():
        previous.join(timeout=2.0)   # outside LOCK: the old thread needs it

    with LOCK:
        if STATE["gen"] != gen:      # a newer start won the race; stand down
            return
        STATE["running"] = True
        STATE["thread"] = threading.Thread(target=background_scanner, args=(gen,),
                                           name="scanner", daemon=True)
        STATE["thread"].start()


@app.before_request
def init():
    global GROUPED
    if GROUPED is None:
        GROUPED = build_grouped_pets()
        print(f"Loaded {len(bot.AMVGG_PETS)} variants -> {len(GROUPED)} base pets", flush=True)
    # Any page the user happens to visit can POST to 127.0.0.1:5000, so a
    # stray tab could stop the scan or accept a trade. Browsers attach Origin
    # to cross-site POSTs; reject a mismatch.
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc != request.host:
            abort(403)


@app.route("/")
def index():
    return render_template("index.html", pets=GROUPED)


@app.route("/pet-options")
def pet_options():
    return jsonify(GROUPED)


@app.route("/research")
def research():
    with LOCK:
        trades = list(STATE["trades"].values())
        holdings = list(STATE["inventory"])
    selected = request.args.getlist("holding")
    baseline = request.args.get("baseline", "").strip()
    return jsonify(opportunities.research(
        bot, baseline, selected or ([baseline] if baseline else holdings), trades))


@app.route("/find", methods=["POST"])
def find():
    try:
        inventory = set(json.loads(request.form.get("inventory", "[]")))
        target = request.form.get("target_pet", "").strip()
    except Exception:
        inventory, target = set(), ""
    if not inventory:
        return render_template("results_clean.html", error="Select at least one pet.",
                               inventory=[], target="")
    start_scanner(inventory, target_pet=target)
    return render_template("results_clean.html", inventory=sorted(inventory), target=target)


@app.route("/status")
def status():
    with LOCK:
        payload = STATE.get("payload")
        if payload is None:
            payload = scan_core.refresh(bot, opportunities, STATE)
        now = time.time()
        # The expensive ranking stays cached, but health fields must never go
        # stale: while the scanner is backing off no new batch arrives, so a
        # fully-cached payload would keep reporting rate_limited=False forever.
        payload.update({
            "running": STATE["running"],
            "rate_limited": STATE["rate_limited"],
            "total_fetches": STATE["total_fetches"],
            "total_new": STATE["total_new"],
            "last_new": STATE["last_new"],
            "last_batch": STATE["last_batch"],
            "trade_count": len(STATE["trades"]),
            "total_scanned": STATE["total_scanned"],
            "in_window": len(STATE["trades"]),
            "target_pet": STATE["target_pet"],
            "last_fetch_age": (round(now - STATE["last_fetch_at"], 1)
                               if STATE["last_fetch_at"] else None),
        })
        return jsonify(payload)


@app.route("/stop", methods=["POST"])
def stop():
    with LOCK:
        STATE["running"] = False
    return jsonify({"ok": True})


@app.route("/accept", methods=["POST"])
def accept():
    trade_id = request.form.get("trade_id")
    if not trade_id:
        return jsonify({"success": False, "error": "No trade ID"})
    success = bot.accept_trade(trade_id)
    return jsonify({"success": success})


@app.route("/update_inventory", methods=["POST"])
def update_inventory():
    try:
        new_inv = set(json.loads(request.form.get("inventory", "[]")))
    except Exception:
        return jsonify({"ok": False})
    with LOCK:
        STATE["inventory"] = new_inv
        scan_core.refresh(bot, opportunities, STATE)
    return jsonify({"ok": True})


@app.route("/set_target", methods=["POST"])
def set_target():
    with LOCK:
        STATE["target_pet"] = request.form.get("target_pet", "").strip()
        scan_core.refresh(bot, opportunities, STATE)
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    host = os.environ.get("HOST", "127.0.0.1")
    server = os.environ.get("SERVER", "waitress").lower()
    print(f"Building pet index...", flush=True)
    GROUPED = build_grouped_pets()
    print(f"Loaded {len(bot.AMVGG_PETS)} variants -> {len(GROUPED)} base pets", flush=True)
    start_demand_refresh()
    if server == "waitress":
        from waitress import serve
        print(f"Serving on http://{host}:{port} (waitress, {POLL_INTERVAL}s poll)", flush=True)
        serve(app, host=host, port=port, threads=8)
    else:
        # debug stays OFF: the werkzeug debugger is remote code execution
        print(f"Serving on http://{host}:{port} (werkzeug, {POLL_INTERVAL}s poll)", flush=True)
        app.run(host=host, port=port, threaded=True, debug=False, use_reloader=False)
