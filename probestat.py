"""
probe_statistics.py — Understand the /api/v4/goods/{id}/statistics endpoint.

Tests:
  1. Does the URL work as given?
  2. Can we use scraper product IDs directly?
  3. Can we use goodId (hex string) directly?
  4. Is there a mapping endpoint?
  5. What's the schema? Daily sales data?

Run: python probe_statistics.py
"""
import requests, json, time, random, os

UA  = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
       "AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0.0.0 Safari/537.36")

S = requests.Session()
S.headers.update({
    "User-Agent": UA,
    "Accept":     "application/json",
    "Origin":     "https://starpets.gg",
    "Referer":    "https://starpets.gg/",
})

def load_proxies():
    if not os.path.exists("proxies.txt"):
        return [None]
    ps = []
    with open("proxies.txt") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                up, hp = line.rsplit("@", 1)
                u, p = up.split(":", 1)
                h, port = hp.split(":")
                ps.append(f"http://{u}:{p}@{h}:{port}")
            except Exception:
                pass
    return ps or [None]

PROXIES = load_proxies()

def get(url, proxies_enabled=True):
    """One GET, random proxy, 20s timeout. Returns (status, body_text)."""
    proxy = random.choice(PROXIES) if proxies_enabled else None
    try:
        kwargs = {"timeout": 20, "verify": False}
        if proxy:
            kwargs["proxies"] = {"http": proxy, "https": proxy}
        r = S.get(url, **kwargs)
        return r.status_code, r.text
    except Exception as e:
        return 0, f"EXCEPTION: {type(e).__name__}: {str(e)[:100]}"

def show(label, url):
    print(f"\n>>> {label}")
    print(f"    URL: {url}")
    status, body = get(url)
    print(f"    Status: {status}")
    if status == 200:
        try:
            data = json.loads(body)
            print(f"    Response (first 1500 chars):")
            print("    " + json.dumps(data, indent=2)[:1500].replace("\n", "\n    "))
            return data
        except Exception:
            print(f"    Body (not JSON): {body[:400]}")
    else:
        print(f"    Body: {body[:300]}")
    return None


# ═════════════════════════════════════════════════════════════════════
# TEST 1: The URL exactly as user found it
# ═════════════════════════════════════════════════════════════════════
print("=" * 72)
print("TEST 1: URL as-found (28337)")
print("=" * 72)

data = show("exact URL",
    "https://market.apineural.com/api/v4/goods/28337/statistics?days=30&currency=usd")

# Get a real product ID from our catalog so we can compare responses
print("\n" + "=" * 72)
print("Getting a known product ID from scraper catalog...")
print("=" * 72)

sample_id = None
try:
    with open("data/starpets_catalog.json") as f:
        cat = json.load(f)
    # Take Frost Dragon base pet as sample
    for key, v in cat.items():
        if v.get("realName") == "frost_dragon" and v.get("pumping") == "default":
            # The `count` field is how many listings of this variant exist
            print(f"  Found: {key}  count={v['count']}  sellable_at=${v['sellable_at']}")
            break
except FileNotFoundError:
    print("  ⚠ data/starpets_catalog.json not found — will use raw product query")

# ═════════════════════════════════════════════════════════════════════
# TEST 2: Try statistics with a real product ID from the products API
# ═════════════════════════════════════════════════════════════════════

# First, fetch a Frost Dragon product to get its ID
print("\n" + "=" * 72)
print("Fetching Frost Dragon product ID...")
print("=" * 72)

status, body = None, None
for attempt in range(3):
    proxy = random.choice(PROXIES)
    try:
        r = S.post(
            "https://market.apineural.com/api/store/items/all",
            json={
                "currency": "usd",
                "page":     1,
                "amount":   1,
                "filter":   {"types": [{"type": "pet"}], "name": "Frost Dragon"},
                "sort":     {"price": "asc"},
            },
            proxies={"http": proxy, "https": proxy} if proxy else None,
            timeout=20, verify=True,
        )
        if r.status_code == 200:
            data = r.json()
            if data.get("status") and data.get("items"):
                item = data["items"][0]
                print(f"  Product ID: {item.get('id')}")
                print(f"  GoodID:     {item.get('goodId')}")
                print(f"  RealName:   {item.get('realName')}")
                print(f"  Full item: {json.dumps(item, indent=2)[:800]}")
                sample_id = item.get("id")
                sample_good = item.get("goodId")
                break
    except Exception as e:
        print(f"  retry: {e}")
    time.sleep(2)

# ═════════════════════════════════════════════════════════════════════
# TEST 3: Try statistics with scraper product ID
# ═════════════════════════════════════════════════════════════════════
if sample_id:
    print("\n" + "=" * 72)
    print("TEST 3: statistics with PRODUCT ID (from scraper)")
    print("=" * 72)
    show(f"product={sample_id}",
         f"https://market.apineural.com/api/v4/goods/{sample_id}/statistics?days=30&currency=usd")

# ═════════════════════════════════════════════════════════════════════
# TEST 4: Try statistics with goodId (hex string)
# ═════════════════════════════════════════════════════════════════════
if sample_good:
    print("\n" + "=" * 72)
    print("TEST 4: statistics with goodId (hex)")
    print("=" * 72)
    show(f"goodId={sample_good}",
         f"https://market.apineural.com/api/v4/goods/{sample_good}/statistics?days=30&currency=usd")

# ═════════════════════════════════════════════════════════════════════
# TEST 5: Look for a product→good mapping endpoint
# ═════════════════════════════════════════════════════════════════════
print("\n" + "=" * 72)
print("TEST 5: Mapping endpoints")
print("=" * 72)
for path in [
    f"/api/v4/goods/{sample_id}" if sample_id else None,
    f"/api/store/items/{sample_id}" if sample_id else None,
    f"/api/v4/items/{sample_id}" if sample_id else None,
    f"/api/v4/products/{sample_id}" if sample_id else None,
]:
    if path:
        show(f"detail endpoint",
             f"https://market.apineural.com{path}")

# ═════════════════════════════════════════════════════════════════════
# TEST 6: Try alternative statistics signatures
# ═════════════════════════════════════════════════════════════════════
print("\n" + "=" * 72)
print("TEST 6: Alternative statistics URL patterns")
print("=" * 72)
if sample_id:
    for variant in [
        f"/api/v4/goods/{sample_id}/stats?days=30&currency=usd",
        f"/api/v4/goods/{sample_id}/statistics?days=7&currency=usd",
        f"/api/v4/goods/{sample_id}/statistics?days=90&currency=usd",
        f"/api/v4/goods/{sample_id}/statistics",
    ]:
        show(f"variant",
             f"https://market.apineural.com{variant}")

print("\n" + "=" * 72)
print("PROBE COMPLETE")
print("=" * 72)