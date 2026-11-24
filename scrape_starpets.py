"""
scrape_starpets_v3.py — demand with honest per-variant aggregation.

What changed vs v2
------------------
* The statistics endpoint returns SALES PER LISTING (product id), not per
  variant. It was verified empirically: the six listings of Frost Dragon
  Fly Ride reported 144, 2, 0, 1, 0, 0 sales over seven days.
* Listing choice is now deterministic: the cheapest listing with a real price.
  The old code took whatever came first out of a dict, which could sample a
  $500 stale listing and understate demand by ~70x.
* --sum aggregates every listing of a variant into a true variant total, and
  only that mode is allowed to claim demand_scope "variant_daily_totals".
* The window is 30 days (the endpoint's maximum); only the most recent days
  are persisted so the catalog does not balloon.
* The per-day average sale price was previously discarded. It is now kept as
  price_history, which is realised sale price rather than listing price.

Scopes
------
  variant_daily_totals      --sum, every listing summed into a zero-filled
                            daily grid. This is what demand.py accepts.
  primary_listing_daily_sales  default, cheapest listing only, gaps preserved.
                            Honest but demand.py will report "unknown" unless
                            that listing sold every single day.
"""
import argparse
import asyncio
import json
import os
import random
import re
import shutil
import sys
import time
import warnings
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import httpx

warnings.filterwarnings("ignore")

API_PRICES = "https://market.apineural.com/api/store/items/all"
API_STATS = "https://market.apineural.com/api/v4/goods/{}/statistics?days={}&currency={}"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Safari/537.36")

CONCURRENCY = 25
AMOUNT_PER_PAGE = 72
MAX_PAGES = 120
PAGE_TIMEOUT = 25

DAYS = 30               # endpoint maximum
PERSIST_DAYS = 7        # rows kept in the catalog: the 7 complete UTC days
CURRENCY = "usd"

TYPES = ["pet", "petwear", "egg", "potion", "stroller", "transport", "toy"]

# Circuit breaker: when the host is unreachable (IP block, outage) every retry
# is wasted work that can also refresh the block. Abort early instead of
# grinding through 30 bucket iterations first.
ABORT_AFTER_FAILURES = 10
_consecutive_failures = 0


class UpstreamBlocked(RuntimeError):
    """Raised when the upstream looks unreachable rather than merely slow."""


def _note_success():
    global _consecutive_failures
    _consecutive_failures = 0


def _note_failure():
    global _consecutive_failures
    _consecutive_failures += 1
    if _consecutive_failures >= ABORT_AFTER_FAILURES:
        raise UpstreamBlocked(
            f"{_consecutive_failures} consecutive request failures; "
            "upstream appears blocked or down. Stopping instead of retrying.")

OUT_DIR = "data"
CATALOG_PATH = f"{OUT_DIR}/starpets_catalog.json"
PROXY_PATH = "proxies.txt"

HEADERS = {
    "User-Agent": UA,
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://starpets.gg",
    "Referer": "https://starpets.gg/",
}

os.makedirs(OUT_DIR, exist_ok=True)


def load_proxies(path):
    proxies = []
    if not os.path.exists(path):
        return proxies
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                up, hp = line.rsplit("@", 1)
                u, p = up.split(":", 1)
                h, port = hp.split(":")
                proxies.append(f"http://{u}:{p}@{h}:{port}")
            except Exception:
                pass
    print(f"proxies loaded: {len(proxies):,}")
    return proxies


def _build_filter(price_min=None, price_max=None):
    f = {"types": [{"type": t} for t in TYPES]}
    if price_min is not None:
        f["price"] = {"min": price_min}
        if price_max is not None:
            f["price"]["max"] = price_max
    return f


# ── Client pool ───────────────────────────────────────────────────────
class DemandClientPool:
    def __init__(self, proxies, size=50):
        self.proxies = proxies
        self.pool = asyncio.Queue()
        for _ in range(size):
            self.pool.put_nowait(self._make_client())

    def _make_client(self):
        proxy = random.choice(self.proxies) if self.proxies else None
        return httpx.AsyncClient(proxy=proxy, timeout=10, headers=HEADERS, verify=False)

    async def acquire(self):
        return await self.pool.get()

    async def release(self, client, replace=False):
        if replace:
            try:
                await client.aclose()
            except Exception:
                pass
            client = self._make_client()
        self.pool.put_nowait(client)

    async def close(self):
        while not self.pool.empty():
            c = await self.pool.get()
            try:
                await c.aclose()
            except Exception:
                pass


# ── Pricing crawl (unchanged behaviour) ───────────────────────────────
async def fetch_price_page(proxies, page, price_min=None, price_max=None, stats=None):
    for _ in range(3):
        proxy = random.choice(proxies) if proxies else None
        payload = {
            "currency": CURRENCY, "page": page, "amount": AMOUNT_PER_PAGE,
            "filter": _build_filter(price_min, price_max), "sort": {"price": "asc"},
        }
        try:
            async with httpx.AsyncClient(proxy=proxy, timeout=PAGE_TIMEOUT, headers=HEADERS, verify=False) as c:
                r = await c.post(API_PRICES, json=payload)
            if r.status_code == 200:
                data = r.json()
                if data.get("status"):
                    _note_success()
                    return data
            elif r.status_code == 400:
                return None
            elif r.status_code in (403, 429, 503):
                _note_failure()
        except UpstreamBlocked:
            raise
        except Exception:
            _note_failure()
        await asyncio.sleep(0.05)
    return None


async def fetch_price_bucket(proxies, p_min, p_max, stats):
    first = await fetch_price_page(proxies, 1, p_min, p_max, stats)
    if not first or not first.get("items"):
        return {}, False, p_min

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
        async with sem:
            return await fetch_price_page(proxies, p, p_min, p_max, stats)

    for coro in asyncio.as_completed([worker(p) for p in range(2, pages_needed + 1)]):
        data = await coro
        if data:
            for it in data.get("items", []):
                results[it["id"]] = it

    prices = [float(it["price"]) for it in results.values() if it.get("price")]
    boundary = max(prices) if prices else p_min
    return results, hit_cap, boundary


async def crawl_prices(proxies, stats):
    probe = await fetch_price_page(proxies, 1, stats=stats)
    max_p = 999999.0
    try:
        async with httpx.AsyncClient(proxy=random.choice(proxies) if proxies else None,
                                     timeout=20, headers=HEADERS, verify=False) as c:
            r = await c.post(API_PRICES, json={"currency": CURRENCY, "page": 1, "amount": 1,
                                               "filter": _build_filter(),
                                               "sort": {"price": "desc"}})
            if r.status_code == 200:
                max_p = float(r.json()["items"][0]["price"])
    except Exception:
        pass

    all_items = {}
    current_min = 0.0
    iteration = 0
    while iteration < 30:
        iteration += 1
        bucket, hit_cap, boundary = await fetch_price_bucket(proxies, current_min, max_p, stats)
        for it in bucket.values():
            all_items[it["id"]] = it
        if not hit_cap:
            break
        sweep, _, _ = await fetch_price_bucket(proxies, boundary, boundary, stats)
        for it in sweep.values():
            all_items[it["id"]] = it
        current_min = boundary + 0.0001
    return all_items


# ── Demand ────────────────────────────────────────────────────────────
def _variant_key(it):
    rn = _slugify(it.get("realName") or "")
    if not rn:
        return None
    pump = (it.get("pumping") or "default").lower()
    return f"{rn}|{pump}|{int(bool(it.get('flyable')))}|{int(bool(it.get('rideable')))}"


def _price_of(it):
    try:
        p = float(it.get("price"))
        return p if p > 0 else None
    except (TypeError, ValueError):
        return None


def group_variant_listings(items_by_id):
    """variant key -> listings sorted cheapest first (no-price items last)."""
    groups = defaultdict(list)
    for it in items_by_id.values():
        key = _variant_key(it)
        if key:
            groups[key].append(it)
    for key in groups:
        groups[key].sort(key=lambda i: (_price_of(i) is None, _price_of(i) or 0.0))
    return dict(groups)


def _complete_days(days):
    """The most recent `days` COMPLETE UTC days, oldest first.

    Today is excluded on purpose: it is a partial day, and demand.py scores
    today-7 .. today-1 only.
    """
    today = datetime.now(timezone.utc).date()
    return [(today - timedelta(days=d)).isoformat() for d in range(days, 0, -1)]


def merge_variant_stats(per_listing, days=DAYS, zero_fill=False, persist_days=PERSIST_DAYS):
    """Combine per-listing daily rows into one variant series.

    per_listing: list of (product_id, [(day, count, price), ...])
    zero_fill:   enumerate the whole window, missing days become 0. Only valid
                 when every listing of the variant was queried.
    """
    daily = Counter()
    weighted = defaultdict(lambda: [0.0, 0])   # day -> [price*count, count]
    for _pid, rows in per_listing:
        for day, count, price in rows:
            daily[day] += count
            if count > 0 and price is not None:
                acc = weighted[day]
                acc[0] += price * count
                acc[1] += count

    if zero_fill:
        dates = _complete_days(days)
    else:
        # gaps preserved: only days the listing actually reported sales on
        complete = _complete_days(days)
        allowed = set(complete)
        dates = [d for d in sorted(daily) if d in allowed]

    series = [{"date": d, "count": int(daily.get(d, 0))} for d in dates]

    prices = []
    for d in dates:
        acc = weighted.get(d)
        if acc and acc[1] > 0:
            prices.append({"date": d, "avg_price": round(acc[0] / acc[1], 4), "sales": acc[1]})
        else:
            prices.append({"date": d, "avg_price": None, "sales": 0})

    kept = series[-persist_days:]
    kept_dates = {r["date"] for r in kept}
    price_rows = [p for p in prices if p["date"] in kept_dates]
    counts = [r["count"] for r in series]
    priced = [p["avg_price"] for p in prices if p["avg_price"] is not None]

    return {
        "daily_sales": kept,
        "price_history": price_rows,
        "sales_window_total": sum(counts),
        "window_days": len(series),
        "days_with_sales": sum(1 for c in counts if c > 0),
        "avg_daily_sales": round(sum(counts) / len(series), 3) if series else 0.0,
        "price_30d": ({
            "min": round(min(priced), 4),
            "max": round(max(priced), 4),
            "median": round(sorted(priced)[len(priced) // 2], 4),
            "last": round(priced[-1], 4),
        } if priced else None),
    }


async def fetch_listing_stats(client, product_id, days=DAYS, currency=CURRENCY, retries=2):
    """Daily (day, count, price) rows for one listing. Empty list on failure."""
    url = API_STATS.format(product_id, days, currency)
    for attempt in range(retries + 1):
        try:
            r = await client.get(url)
            if r.status_code == 200:
                data = r.json().get("data", {}).get("statistics", [])
                rows = []
                for x in data:
                    try:
                        rows.append((x["day"], int(x["count"]), float(x["price"])))
                    except (KeyError, TypeError, ValueError):
                        continue
                return rows
            if r.status_code == 429:
                await asyncio.sleep(3 * (2 ** attempt))
                continue
            return []
        except Exception:
            await asyncio.sleep(1.0)
    return []


async def build_demand(pool, groups, sum_mode, days=DAYS, limit=None, names=None):
    """Fetch stats and produce the demand block for every variant."""
    keys = list(groups)
    if names:
        wanted = {_slugify(n) for n in names}
        keys = [k for k in keys if k.split("|", 1)[0] in wanted]
    if limit:
        keys = keys[:limit]

    print(f"  demand for {len(keys):,} variants "
          f"({'summing all listings' if sum_mode else 'cheapest listing'}), "
          f"{'up to ' + str(days) + ' days'}")

    out = {}
    done = 0

    async def worker(key):
        nonlocal done
        listings = groups[key]
        chosen = listings if sum_mode else listings[:1]
        ids = [it.get("id") for it in chosen if it.get("id")]
        if not ids:
            # Never claim a verified scope for data we did not fetch: an empty
            # product id would otherwise produce a zero-filled "variant total".
            out[key] = {
                "daily_sales": [], "price_history": [], "sales_window_total": 0,
                "window_days": 0, "days_with_sales": 0, "avg_daily_sales": 0.0,
                "price_30d": None, "demand_scope": "unavailable",
                "demand_product_ids": [], "listings_enumerated": 0,
            }
            done += 1
            return

        client = await pool.acquire()
        replace = False
        per_listing = []
        try:
            for it in chosen:
                if not it.get("id"):
                    continue
                rows = await fetch_listing_stats(client, it["id"], days)
                per_listing.append((it["id"], rows))
        except Exception:
            replace = True
        finally:
            await pool.release(client, replace=replace)
        out[key] = merge_variant_stats(per_listing, days=days,
                                       zero_fill=sum_mode,
                                       persist_days=PERSIST_DAYS)
        out[key]["demand_scope"] = ("variant_daily_totals" if sum_mode
                                    else "primary_listing_daily_sales")
        out[key]["demand_product_ids"] = [pid for pid, _ in per_listing]
        out[key]["listings_enumerated"] = len(per_listing)
        done += 1
        if done % 500 == 0:
            print(f"    {done:,}/{len(keys):,}", flush=True)

    sem = asyncio.Semaphore(24)

    async def guarded(key):
        async with sem:
            await worker(key)

    await asyncio.gather(*(guarded(k) for k in keys))
    return out


# ── Catalog ───────────────────────────────────────────────────────────
def _slugify(s):
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def aggregate_catalog(items_by_id, demand, keep_prices=False):
    """keep_prices=False drops the per-day price rows from the catalog.

    The 7 daily sale counts are what demand.py needs and they are small. The
    per-day price rows roughly double each record, and nothing reads them yet,
    so they are opt-in to keep the published catalog from ballooning.
    """
    groups = defaultdict(lambda: {"prices": [], "avgs": [], "item": None})
    for it in items_by_id.values():
        key = _variant_key(it)
        if not key:
            continue
        g = groups[key]
        if g["item"] is None:
            g["item"] = it
        try:
            p = it.get("price")
            if p and float(p) > 0:
                g["prices"].append(float(p))
            a = it.get("avgPrice")
            if a and float(a) > 0:
                g["avgs"].append(float(a))
        except (ValueError, TypeError):
            pass

    catalog = {}
    for key, g in groups.items():
        prices = sorted(g["prices"])
        nz = sorted(g["avgs"])
        if nz:
            sellable, conf = nz[len(nz) // 2], ("high" if len(nz) >= 3 else "medium")
        elif prices:
            sellable, conf = prices[0] * 0.5, "low"
        else:
            sellable, conf = 0.0, "none"

        it = g["item"]
        d = demand.get(key) or {}
        catalog[key] = {
            "name": it.get("name"),
            "realName": _slugify(it.get("realName") or ""),
            "pumping": (it.get("pumping") or "default").lower(),
            "flyable": bool(it.get("flyable")),
            "rideable": bool(it.get("rideable")),
            "rare": it.get("rare"),
            "type": it.get("type"),
            "price_low": round(prices[0], 4) if prices else 0.0,
            "price_median": round(prices[len(prices) // 2], 4) if prices else 0.0,
            "sellable_at": round(sellable, 4),
            "count": len(prices),
            "n_with_sales": len(nz),
            "confidence": conf,
            "avg_daily_sales": d.get("avg_daily_sales", 0.0),
            "sales_window_total": d.get("sales_window_total", 0),
            "window_days": d.get("window_days", 0),
            "days_with_sales": d.get("days_with_sales", 0),
            "daily_sales": d.get("daily_sales", []),
            "price_history": d.get("price_history", []) if keep_prices else [],
            "price_30d": d.get("price_30d"),
            "demand_scope": d.get("demand_scope", "unavailable"),
            "demand_product_ids": d.get("demand_product_ids", []),
            "listings_enumerated": d.get("listings_enumerated", 0),
        }
    return catalog


# ── Main ──────────────────────────────────────────────────────────────
def parse_args(argv=None):
    p = argparse.ArgumentParser(description="StarPets catalog + demand scraper")
    p.add_argument("--sum", dest="sum_mode", action="store_true",
                   help="sum every listing of a variant (enables variant_daily_totals)")
    p.add_argument("--days", type=int, default=DAYS, help="statistics window, max 30")
    p.add_argument("--limit", type=int, default=None, help="only the first N variants")
    p.add_argument("--names", type=str, default=None,
                   help="comma-separated realNames to restrict demand collection")
    p.add_argument("--no-crawl", action="store_true",
                   help="reuse the existing catalog for prices instead of re-crawling")
    p.add_argument("--keep-prices", action="store_true",
                   help="also store the per-day realised price rows (larger catalog)")
    p.add_argument("--out", type=str, default=CATALOG_PATH)
    return p.parse_args(argv)


async def run(args):
    t_start = time.time()
    proxies = load_proxies(PROXY_PATH)
    stats = defaultdict(int)

    if args.no_crawl and os.path.exists(args.out):
        with open(args.out) as f:
            existing = json.load(f)
        print(f"1. Reusing {len(existing):,} variants from {args.out}")
        items_by_id = {}
        for key, rec in existing.items():
            ids = rec.get("demand_product_ids")
            if not ids and rec.get("demand_product_id"):
                ids = [rec["demand_product_id"]]        # older catalog schema
            items_by_id[key] = {
                "id": (ids or [None])[0],
                "name": rec.get("name"), "realName": rec.get("realName"),
                "pumping": rec.get("pumping"), "flyable": rec.get("flyable"),
                "rideable": rec.get("rideable"), "rare": rec.get("rare"),
                "type": rec.get("type"), "price": rec.get("sellable_at"),
                "avgPrice": rec.get("sellable_at"),
            }
    else:
        print("1. Crawling prices...")
        # keep the previous catalog so a failed crawl can never destroy it
        try:
            if os.path.exists(args.out):
                shutil.copy2(args.out, args.out + ".bak")
        except OSError:
            pass
        global _consecutive_failures
        _consecutive_failures = 0
        try:
            items_by_id = await crawl_prices(proxies, stats)
        except UpstreamBlocked as e:
            raise RuntimeError(str(e)) from e
        print(f"   {len(items_by_id):,} products in {time.time()-t_start:.1f}s")
        if not items_by_id:
            raise RuntimeError(
                "price crawl returned 0 products (upstream blocked or down); "
                "refusing to overwrite " + args.out)

    groups = group_variant_listings(items_by_id)
    print(f"   {len(groups):,} variants across {len(items_by_id):,} listings")

    names = [n.strip() for n in args.names.split(",")] if args.names else None
    pool = DemandClientPool(proxies, size=24)
    demand = await build_demand(pool, groups, args.sum_mode,
                               days=min(args.days, 30), limit=args.limit, names=names)
    await pool.close()

    catalog = aggregate_catalog(items_by_id, demand, keep_prices=args.keep_prices)
    if not catalog:
        raise RuntimeError("no variants aggregated; refusing to overwrite " + args.out)
    for key, d in demand.items():
        if key in catalog:
            catalog[key].update({k: d[k] for k in
                                 ("daily_sales", "price_30d",
                                  "demand_scope", "demand_product_ids",
                                  "listings_enumerated", "avg_daily_sales",
                                  "sales_window_total", "window_days", "days_with_sales")})
    with open(args.out, "w") as f:
        json.dump(catalog, f, indent=1)
    print(f"   wrote {len(catalog):,} variants to {args.out} "
          f"({os.path.getsize(args.out)/1e6:.1f} MB)")

    scopes = Counter(v.get("demand_scope") for v in catalog.values())
    print(f"   scopes: {dict(scopes)}")
    verified = [v for v in catalog.values() if v.get("demand_scope") == "variant_daily_totals"]
    if verified:
        top = sorted(verified, key=lambda v: -v.get("sales_window_total", 0))[:5]
        print("\n   top verified sellers:")
        for v in top:
            print(f"    {v['name']:<28} {v.get('sales_window_total'):>5} sold / "
                  f"{v.get('window_days')}d  {v.get('avg_daily_sales'):.2f}/day")
    return catalog


def main(argv=None):
    args = parse_args(argv)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\ninterrupted")


if __name__ == "__main__":
    main()
