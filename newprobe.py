"""
probe_neural.py — Test market.apineural.com API.
Conservative: 15s between requests, aborts on first 429.

Run: python probe_neural.py
"""
import requests, json, time, sys

API = "https://market.apineural.com/api/store/items/all"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

S = requests.Session()
S.headers.update({
    "User-Agent": UA,
    "Content-Type": "application/json",
    "Accept":     "application/json",
    "Origin":     "https://starpets.gg",
    "Referer":    "https://starpets.gg/",
})


def safe_post(payload, label):
    print(f"\n>>> {label}")
    try:
        r = S.post(API, json=payload, timeout=20)
    except Exception as e:
        print(f"    NETWORK ERROR: {e}")
        return None

    print(f"    Status: {r.status_code}")
    ra = r.headers.get("Retry-After")
    if ra:
        print(f"    Retry-After: {ra}")

    if r.status_code == 429:
        print(f"    ⚠ RATE LIMITED — stopping to avoid ban")
        print(f"    Body: {r.text[:300]}")
        sys.exit(1)

    if r.status_code != 200:
        print(f"    Body: {r.text[:300]}")
        return None

    try:
        data = r.json()
    except Exception:
        print(f"    Not JSON: {r.text[:300]}")
        return None

    if not data.get("status"):
        print(f"    API status=false: {json.dumps(data)[:400]}")
        return None

    return data


# ═════════════════════════════════════════════════════════════════════
# TEST 1: Cold request — 1 pet (minimal)
# ═════════════════════════════════════════════════════════════════════
d = safe_post({
    "currency": "usd",
    "page": 1,
    "amount": 1,
    "filter": {"types": [{"type": "pet"}]},
    "sort": {"popularity": "desc"},
}, "TEST 1: 1 pet, popularity desc")

if d:
    print(f"    Total count: {d.get('count', '?')}")
    items = d.get("items", [])
    print(f"    Items returned: {len(items)}")
    if items:
        print("\n    === FULL ITEM SCHEMA ===")
        print(json.dumps(items[0], indent=2)[:2500])
else:
    print("\n    ❌ TEST 1 failed — API may be down or blocked")
    sys.exit(1)

time.sleep(15)

# ═════════════════════════════════════════════════════════════════════
# TEST 2: 5 popular pets — see names, realNames, prices
# ═════════════════════════════════════════════════════════════════════
d = safe_post({
    "currency": "usd",
    "page": 1,
    "amount": 5,
    "filter": {"types": [{"type": "pet"}]},
    "sort": {"popularity": "desc"},
}, "TEST 2: 5 popular pets")

if d:
    for item in d.get("items", []):
        pump = (item.get("pumping") or "default").replace("_", " ")
        print(f"    {item.get('name'):<25}  realName={item.get('realName'):<25}  "
              f"id={item.get('id'):<8}  ${item.get('price', '?'):<10}  "
              f"avg=${item.get('avgPrice', 'n/a'):<10}  "
              f"pump={pump:<10}  F={item.get('flyable')} R={item.get('rideable')}")

time.sleep(15)

# ═════════════════════════════════════════════════════════════════════
# TEST 3: Name search — Haetae (the one we couldn't price)
# ═════════════════════════════════════════════════════════════════════
d = safe_post({
    "currency": "usd",
    "page": 1,
    "amount": 10,
    "filter": {
        "types": [{"type": "pet"}],
        "name": "Haetae",
    },
    "sort": {"price": "asc"},
}, "TEST 3: Name search — Haetae")

if d:
    print(f"    Matches: {d.get('count', 0)}")
    for item in d.get("items", [])[:5]:
        pump = (item.get("pumping") or "default").replace("_", " ")
        print(f"    {item.get('name'):<25}  ${item.get('price', '?'):<8}  "
              f"avg=${item.get('avgPrice', 'n/a'):<8}  pump={pump}  "
              f"F={item.get('flyable')} R={item.get('rideable')}")

time.sleep(15)

# ═════════════════════════════════════════════════════════════════════
# TEST 4: Cryptid (the 2025 pet that was above ID 40,000 in the old scraper)
# ═════════════════════════════════════════════════════════════════════
d = safe_post({
    "currency": "usd",
    "page": 1,
    "amount": 10,
    "filter": {
        "types": [{"type": "pet"}],
        "name": "Cryptid",
    },
    "sort": {"price": "asc"},
}, "TEST 4: Name search — Cryptid")

if d:
    print(f"    Matches: {d.get('count', 0)}")
    for item in d.get("items", [])[:5]:
        pump = (item.get("pumping") or "default").replace("_", " ")
        print(f"    {item.get('name'):<25}  ${item.get('price', '?'):<8}  "
              f"avg=${item.get('avgPrice', 'n/a'):<8}  pump={pump}  "
              f"F={item.get('flyable')} R={item.get('rideable')}")

time.sleep(15)

# ═════════════════════════════════════════════════════════════════════
# TEST 5: Total counts per type — tells us how many pages to fetch
# ═════════════════════════════════════════════════════════════════════
print("\n>>> TEST 5: Total item counts per type")
totals = {}
for t in ["pet", "petwear", "egg", "potion", "stroller", "transport", "toy"]:
    d = safe_post({
        "currency": "usd",
        "page": 1,
        "amount": 1,
        "filter": {"types": [{"type": t}]},
        "sort": {"popularity": "desc"},
    }, f"     count for type '{t}'")
    if d:
        c = d.get("count", 0)
        totals[t] = c
        print(f"    {t}: {c} items")
    time.sleep(10)

print("\n" + "=" * 70)
print("PROBE COMPLETE")
print("=" * 70)
print(f"Total items across all types: {sum(totals.values())}")
print(f"At 72 items/page: {sum(totals.values()) // 72 + 1} pages to fetch everything")