"""
scrape_starpets_v2.py — Fixed demand polling.
Only fetches demand for unique variants (~6,800) instead of every listing (~18,444).
Uses a rotating pool of 50 clients to prevent OS file-handle exhaustion.
"""
import asyncio, httpx, json, os, re, random, time, warnings
from collections import defaultdict

warnings.filterwarnings("ignore")

API_PRICES  = "https://market.apineural.com/api/store/items/all"
API_DEMAND  = "https://market.apineural.com/api/v4/goods/{}/statistics?days=7&currency=usd"
UA  = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
       "AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0.0.0 Safari/537.36")

CONCURRENCY      = 25
AMOUNT_PER_PAGE  = 72
MAX_PAGES        = 120
PAGE_TIMEOUT     = 25

TYPES = ["pet", "petwear", "egg", "potion", "stroller", "transport", "toy"]

OUT_DIR      = "data"
CATALOG_PATH = f"{OUT_DIR}/starpets_catalog.json"
PROXY_PATH   = "proxies.txt"

HEADERS = {
    "User-Agent":   UA,
    "Content-Type": "application/json",
    "Accept":       "application/json",
    "Origin":       "https://starpets.gg",
    "Referer":      "https://starpets.gg/",
}

os.makedirs(OUT_DIR, exist_ok=True)

def load_proxies(path):
    proxies = []
    if not os.path.exists(path): return proxies
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"): continue
            try:
                up, hp = line.rsplit("@", 1)
                u, p = up.split(":", 1)
                h, port = hp.split(":")
                proxies.append(f"http://{u}:{p}@{h}:{port}")
            except Exception: pass
    print(f"✓ {len(proxies):,} proxies")
    return proxies

def _build_filter(price_min=None, price_max=None):
    f = {"types": [{"type": t} for t in TYPES]}
    if price_min is not None:
        f["price"] = {"min": price_min}
        if price_max is not None:
            f["price"]["max"] = price_max
    return f

# ── Client Pool for Demand ────────────────────────────────────────────
class DemandClientPool:
    def __init__(self, proxies, size=50):
        self.proxies = proxies
        self.pool = asyncio.Queue()
        for _ in range(size):
            self.pool.put_nowait(self._make_client())

    def _make_client(self):
        proxy = random.choice(self.proxies) if self.proxies else None
        return httpx.AsyncClient(
            proxy=proxy, timeout=10, headers=HEADERS, verify=False
        )

    async def acquire(self):
        return await self.pool.get()

    async def release(self, client, replace=False):
        if replace:
            try: await client.aclose()
            except Exception: pass
            client = self._make_client()
        self.pool.put_nowait(client)

    async def close(self):
        while not self.pool.empty():
            c = await self.pool.get()
            try: await c.aclose()
            except Exception: pass

# ── Pricing Crawl (Unchanged) ─────────────────────────────────────────
async def fetch_price_page(proxies, page, price_min=None, price_max=None, stats=None):
    for _ in range(3):
        proxy = random.choice(proxies) if proxies else None
        payload = {
            "currency": "usd", "page": page, "amount": AMOUNT_PER_PAGE,
            "filter": _build_filter(price_min, price_max), "sort": {"price": "asc"},
        }
        try:
            async with httpx.AsyncClient(proxy=proxy, timeout=PAGE_TIMEOUT, headers=HEADERS, verify=False) as c:
                r = await c.post(API_PRICES, json=payload)
            if r.status_code == 200:
                data = r.json()
                if data.get("status"): return data
            elif r.status_code == 400: return None
        except Exception: pass
        await asyncio.sleep(0.02)
    return None

async def fetch_price_bucket(proxies, p_min, p_max, stats):
    first = await fetch_price_page(proxies, 1, p_min, p_max, stats)
    if not first or not first.get("items"): return {}, False, p_min

    items = first["items"]
    results = {it["id"]: it for it in items}
    total = first.get("count", len(items))
    pages_needed = min(-(-total // AMOUNT_PER_PAGE), MAX_PAGES)
    hit_cap = pages_needed >= MAX_PAGES

    if pages_needed <= 1:
        prices = [float(it["price"]) for it in items if it.get("price")]
        return results, False, max(prices) if prices else p_min

    sem = asyncio.Semaphore(CONCURRENCY)
    async def worker(p):
        async with sem: return await fetch_price_page(proxies, p, p_min, p_max, stats)

    for coro in asyncio.as_completed([worker(p) for p in range(2, pages_needed + 1)]):
        data = await coro
        if data:
            for it in data.get("items", []): results[it["id"]] = it

    prices = [float(it["price"]) for it in results.values() if it.get("price")]
    boundary = max(prices) if prices else p_min
    return results, hit_cap, boundary

async def crawl_prices(proxies, stats):
    probe = await fetch_price_page(proxies, 1, stats=stats)
    total = probe["count"] if probe else 0
    
    max_p = 999999.0
    try:
        async with httpx.AsyncClient(proxy=random.choice(proxies), timeout=20, headers=HEADERS, verify=False) as c:
            r = await c.post(API_PRICES, json={"currency": "usd", "page": 1, "amount": 1, "filter": _build_filter(), "sort": {"price": "desc"}})
            if r.status_code == 200: max_p = float(r.json()["items"][0]["price"])
    except Exception: pass

    all_items = {}
    current_min = 0.0
    iteration = 0

    while iteration < 30:
        iteration += 1
        bucket, hit_cap, boundary = await fetch_price_bucket(proxies, current_min, max_p, stats)
        for it in bucket.values(): all_items[it["id"]] = it
        if not hit_cap: break
        
        sweep, _, _ = await fetch_price_bucket(proxies, boundary, boundary, stats)
        for it in sweep.values(): all_items[it["id"]] = it
        current_min = boundary + 0.0001

    return all_items

# ── Main ──────────────────────────────────────────────────────────────
def _slugify(s):
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")

async def run():
    t_start = time.time()
    proxies = load_proxies(PROXY_PATH)
    stats   = defaultdict(int)

    print("1. Crawling prices...")
    items_by_id = await crawl_prices(proxies, stats)
    print(f"   ✓ {len(items_by_id):,} products found in {time.time()-t_start:.1f}s")

    # Group to find ONE ID per variant
    variant_samples = {}
    for it in items_by_id.values():
        rn = _slugify(it.get("realName") or "")
        if not rn: continue
        pump = (it.get("pumping") or "default").lower()
        fly  = bool(it.get("flyable"))
        ride = bool(it.get("rideable"))
        key  = f"{rn}|{pump}|{int(fly)}|{int(ride)}"
        if key not in variant_samples:
            variant_samples[key] = it["id"]

    print(f"\n2. Fetching demand for {len(variant_samples):,} unique variants...")
    demand_data = {}
    pool = DemandClientPool(proxies, size=50)

    async def demand_worker(key, sid):
        client = await pool.acquire()
        replace = False
        try:
            r = await client.get(API_DEMAND.format(sid))
            if r.status_code == 200:
                s = r.json().get("data", {}).get("statistics", [])
                demand_data[key] = round(sum(x["count"] for x in s) / len(s), 2) if s else 0.0
            elif r.status_code == 429:
                replace = True  # Rotate this proxy away immediately
                demand_data[key] = 0.0
            else:
                demand_data[key] = 0.0
        except Exception:
            replace = True
            demand_data[key] = 0.0
        finally:
            await pool.release(client, replace=replace)

    tasks = [demand_worker(k, sid) for k, sid in variant_samples.items()]
    
    done = 0
    for coro in asyncio.as_completed(tasks):
        await coro
        done += 1
        if done % 1000 == 0:
            print(f"   {done:,}/{len(variant_samples):,} fetched", flush=True)
            
    await pool.close()
    print(f"   ✓ Demand data complete")

    print("\n3. Aggregating...")
    groups = defaultdict(lambda: {
        "prices": [], "avgs": [], "name": None, "realName": None,
        "pumping": None, "flyable": None, "rideable": None,
        "rare": None, "type": None, "sample_id": None
    })
    
    for it in items_by_id.values():
        rn = _slugify(it.get("realName") or "")
        if not rn: continue
        pump = (it.get("pumping") or "default").lower()
        fly, ride = bool(it.get("flyable")), bool(it.get("rideable"))
        key = f"{rn}|{pump}|{int(fly)}|{int(ride)}"
        g = groups[key]
        g["name"], g["realName"], g["pumping"] = it.get("name"), rn, pump
        g["flyable"], g["rideable"] = fly, ride
        g["rare"], g["type"] = it.get("rare"), it.get("type")
        if not g["sample_id"]: g["sample_id"] = it["id"]
        try:
            p = it.get("price")
            if p and float(p) > 0: g["prices"].append(float(p))
            a = it.get("avgPrice")
            if a and float(a) > 0: g["avgs"].append(float(a))
        except (ValueError, TypeError): pass

    catalog = {}
    for key, g in groups.items():
        prices  = sorted(g["prices"]); nz = sorted(g["avgs"])
        if nz: sellable, conf = nz[len(nz)//2], ("high" if len(nz) >= 3 else "medium")
        elif prices: sellable, conf = prices[0]*0.5, "low"
        else: sellable, conf = 0.0, "none"

        catalog[key] = {
            "name":             g["name"],
            "realName":         g["realName"],
            "pumping":          g["pumping"],
            "flyable":          g["flyable"],
            "rideable":         g["rideable"],
            "rare":             g["rare"],
            "type":             g["type"],
            "price_low":        round(prices[0], 4) if prices else 0.0,
            "price_median":     round(prices[len(prices)//2], 4) if prices else 0.0,
            "sellable_at":      round(sellable, 4),
            "count":            len(prices),
            "n_with_sales":     len(nz),
            "confidence":       conf,
            "avg_daily_sales":  demand_data.get(key, 0.0)
        }
        
    with open(CATALOG_PATH, "w") as f:
        json.dump(catalog, f, indent=2)
    print(f"   ✓ {len(catalog):,} unique variants written to {CATALOG_PATH}")

    print("\nTop 5 most liquid:")
    for k, v in sorted(catalog.items(), key=lambda x: -x[1]["avg_daily_sales"])[:5]:
        print(f"  {k:<40} {v['avg_daily_sales']:>6.1f} sales/day  (${v['sellable_at']})")

def main():
    try: asyncio.run(run())
    except KeyboardInterrupt: print("\n⚠ Interrupted")

if __name__ == "__main__":
    main()