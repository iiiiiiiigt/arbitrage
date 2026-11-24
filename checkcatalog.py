"""
Fast StarPets catalog scraper.
- Phase 1: fetch USD values for IDs 1-30,000 (async, 50 concurrent)
- Phase 2: fetch slugs only for pets with value >= $0.02 (async, 20 concurrent)
- Phase 3: aggregate into data/starpets_catalog.json

Install once:  pip install httpx
Run:           python scrape_starpets.py

Expected: ~20-40 min on a decent connection.
"""
import asyncio, json, re, os, time, random
from collections import defaultdict
import httpx

# ── CONFIG ────────────────────────────────────────────────────────────
ID_START        = 1
ID_END          = 30000     # 30k covers all base + variant pets
MAX_CONCURRENT  = 50        # JSON fetches (raise to 80 if no 429s)
SLUG_CONCURRENT = 20        # HTML fetches (heavier, keep lower)
VALUE_THRESHOLD = 0.02      # skip slugs for pets cheaper than this
OUT_DIR         = "data"
OUT_FILE        = f"{OUT_DIR}/starpets_catalog.json"

CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
             "AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/124.0.0.0 Safari/537.36")
BOT_UA    = "Googlebot/2.1 (+http://www.google.com/bot.html)"

# ── Async fetch helpers ───────────────────────────────────────────────
async def fetch_json(client, url, retries=3):
    """Fetch JSON with exponential backoff on 429."""
    for attempt in range(retries):
        try:
            r = await client.get(url)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                wait = 3 * (2 ** attempt) + random.uniform(0, 2)
                await asyncio.sleep(wait)
                continue
            return None
        except Exception:
            await asyncio.sleep(0.5)
    return None

async def get_value(client, pid, sem):
    async with sem:
        data = await fetch_json(client, f"https://starpets.gg/item-values/adoptme/{pid}")
        if not data:
            return pid, None
        item = data.get("item")
        if not item or item.get("value") is None:
            return pid, None
        return pid, {
            "value":     item["value"],
            "estimated": item.get("PriceEstimated"),
            "trend":     item.get("trendStatus"),
        }

async def get_slug(client, pid, sem):
    async with sem:
        for attempt in range(3):
            try:
                r = await client.get(f"https://starpets.gg/adopt-me/shop/pet/x/{pid}")
                if r.status_code == 200:
                    m = re.search(r'og:url" content="[^"]*?/pet/([^/]+)/', r.text)
                    return pid, (m.group(1) if m else None)
                if r.status_code == 429:
                    await asyncio.sleep(3 * (2 ** attempt))
                    continue
                return pid, None
            except Exception:
                await asyncio.sleep(0.5)
        return pid, None

# ── Main pipeline ─────────────────────────────────────────────────────
async def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    limits  = httpx.Limits(max_connections=MAX_CONCURRENT,
                            max_keepalive_connections=MAX_CONCURRENT)
    timeout = httpx.Timeout(20.0, connect=10.0)

    print("=" * 65)
    print(f"PHASE 1: fetching USD values for IDs {ID_START:,}–{ID_END:,}")
    print(f"         {MAX_CONCURRENT} concurrent · httpx async · {ID_END-ID_START+1:,} total")
    print("=" * 65)

    t0 = time.time()
    values = {}
    total  = ID_END - ID_START + 1

    async with httpx.AsyncClient(limits=limits, timeout=timeout,
                                  headers={"User-Agent": CHROME_UA}) as client:
        sem   = asyncio.Semaphore(MAX_CONCURRENT)
        tasks = [get_value(client, i, sem) for i in range(ID_START, ID_END + 1)]

        done = 0
        for coro in asyncio.as_completed(tasks):
            pid, d = await coro
            done += 1
            if d:
                values[pid] = d
            if done % 500 == 0:
                elapsed = time.time() - t0
                rate    = done / elapsed
                eta_min = (total - done) / rate / 60 if rate else 0
                print(f"  {done:>6,}/{total:,}    valid={len(values):>6,}    "
                      f"{rate:>5.1f}/s    ETA {eta_min:>4.1f}m")

    phase1_min = (time.time() - t0) / 60
    print(f"\n✅ PHASE 1 DONE in {phase1_min:.1f}m — {len(values):,} valid pets")

    if not values:
        print("❌ No valid data. Check network / site status.")
        return

    # ── Phase 2: slugs for meaningful-value pets ──────────────────────
    likely = [pid for pid, d in values.items() if d["value"] >= VALUE_THRESHOLD]
    print("\n" + "=" * 65)
    print(f"PHASE 2: fetching slugs for {len(likely):,} pets ≥ ${VALUE_THRESHOLD}")
    print(f"         {SLUG_CONCURRENT} concurrent · Googlebot UA")
    print("=" * 65)

    t1 = time.time()
    slugs = {}

    async with httpx.AsyncClient(limits=limits, timeout=timeout,
                                  headers={"User-Agent": BOT_UA}) as client:
        sem   = asyncio.Semaphore(SLUG_CONCURRENT)
        tasks = [get_slug(client, pid, sem) for pid in likely]

        done = 0
        for coro in asyncio.as_completed(tasks):
            pid, slug = await coro
            done += 1
            if slug:
                slugs[pid] = slug
            if done % 100 == 0:
                elapsed = time.time() - t1
                rate    = done / elapsed
                eta_min = (len(likely) - done) / rate / 60 if rate else 0
                print(f"  {done:>5,}/{len(likely):,}    matched={len(slugs):>5,}    "
                      f"{rate:>5.1f}/s    ETA {eta_min:>4.1f}m")

    phase2_min = (time.time() - t1) / 60
    print(f"\n✅ PHASE 2 DONE in {phase2_min:.1f}m — {len(slugs):,} slugs resolved")

    # ── Phase 3: aggregate ────────────────────────────────────────────
    print("\n" + "=" * 65)
    print("PHASE 3: aggregating catalog")
    print("=" * 65)

    catalog = defaultdict(lambda: {"prices": [], "estimated": [], "trends": [], "ids": []})

    for pid, d in values.items():
        slug = slugs.get(pid)
        if not slug:
            continue
        catalog[slug]["prices"].append(d["value"])
        catalog[slug]["estimated"].append(d["estimated"])
        catalog[slug]["trends"].append(d["trend"])
        catalog[slug]["ids"].append(pid)

    out = {}
    for slug, d in catalog.items():
        prices = sorted(d["prices"])
        out[slug] = {
            "low":      round(prices[0], 4),
            "median":   round(prices[len(prices) // 2], 4),
            "high":     round(prices[-1], 4),
            "count":    len(prices),
            "net":      round(prices[0] * 0.7, 4),
            "all_real": all(e is False for e in d["estimated"]),
            "trend":    d["trends"][0] if d["trends"] else "unknown",
            "ids":      d["ids"][:5],
        }

    with open(OUT_FILE, "w") as f:
        json.dump(out, f, indent=2)

    total_min = (time.time() - t0) / 60
    print(f"\n{'=' * 65}")
    print(f"✅ COMPLETE in {total_min:.1f}m — {len(out):,} pet families written")
    print(f"   File: {OUT_FILE}")
    print(f"{'=' * 65}\n")

    print("Top 10 by median price:")
    for s, d in sorted(out.items(), key=lambda x: -x[1]["median"])[:10]:
        print(f"  {s:<40} median=${d['median']:<9}  net=${d['net']:<8}  n={d['count']}")

    print(f"\nSpot-check required pets:")
    for test in ["frost_dragon", "fallow_deer", "cow", "unicorn", "pig"]:
        if test in out:
            d = out[test]
            print(f"  ✓ {test:<20} median=${d['median']:<8}  net=${d['net']}  n={d['count']}")
        else:
            print(f"  ✗ {test:<20} NOT FOUND")


if __name__ == "__main__":
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠ Interrupted by user")