import requests, json, re, os, time, random
from pathlib import Path
from urllib.parse import quote
from demand import weekly_demand
from sales_history import collector as sales_collector
import demand_resolver

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
BASE_DIR = Path(__file__).resolve().parent

# ── Load proxies ──────────────────────────────────────────────────────
PROXIES = []
if (BASE_DIR / "proxies.txt").exists():
    with (BASE_DIR / "proxies.txt").open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"): continue
            try:
                up, hp = line.rsplit("@", 1)
                u, p = up.split(":", 1)
                h, port = hp.split(":")
                PROXIES.append(f"http://{u}:{p}@{h}:{port}")
            except Exception: pass

# ── Load Cookies for Accepting ────────────────────────────────────────
COOKIES = requests.cookies.RequestsCookieJar()
if (BASE_DIR / "amvgg_cookies.json").exists():
    with (BASE_DIR / "amvgg_cookies.json").open() as f:
        raw_cookies = json.load(f)
        for c in raw_cookies:
            domain = c.get('domain', '')
            if domain.lstrip('.') == 'amvgg.com':
                COOKIES.set(c['name'], c['value'], domain=domain,
                            path=c.get('path', '/'), secure=c.get('secure', True))

# ── Load data ─────────────────────────────────────────────────────────
with (BASE_DIR / "pets.json").open() as f:
    AMVGG_PETS = json.load(f)

CATALOG_PATH = BASE_DIR / "data" / "starpets_catalog.json"
CATALOG = {}
if CATALOG_PATH.exists():
    with CATALOG_PATH.open() as f:
        CATALOG = json.load(f)
    # keep the variant identity on the record so demand_resolver can look up the
    # StarPets rating without re-deriving the key
    for _k, _v in CATALOG.items():
        if isinstance(_v, dict):
            _v["_key"] = _k

# ── Slug & Variant Helpers ────────────────────────────────────────────
def to_slug(s):
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")

def canonical_slug(name):
    parts = [p for p in re.split(r"[\s\-_]+", name.lower()) if p]
    MODS = {"fly", "ride", "neon", "mega"}
    base, mods = [], set()
    for p in parts:
        (mods.add(p) if p in MODS else base.append(p))
    return "_".join(base + [m for m in ["fly", "ride", "neon", "mega"] if m in mods])

AMVGG_MAP = {}
for p in AMVGG_PETS:
    AMVGG_MAP[canonical_slug(p["name"])] = p["value"]

_VAR_SUFFIX = re.compile(r"((?:\s+(?:Fly|Ride|Neon|Mega))+)\s*$", re.IGNORECASE)

def parse_variant_components(name):
    m = _VAR_SUFFIX.search(name)
    if not m:
        return to_slug(name), "default", False, False
    suffix = m.group(1).lower()
    base   = name[:m.start()].strip()
    pumping = "mega_neon" if " mega" in suffix else "neon" if " neon" in suffix else "default"
    return to_slug(base), pumping, " fly" in suffix, " ride" in suffix

def variant_multiplier(name):
    base = canonical_slug(re.sub(r"(?:\s+(?:Fly|Ride|Neon|Mega))+$", "", name, flags=re.IGNORECASE).strip())
    var  = canonical_slug(name)
    if base not in AMVGG_MAP or var not in AMVGG_MAP: return 1.0
    b = AMVGG_MAP[base]
    return (AMVGG_MAP[var] / b) if b > 0 else 1.0

# ── StarPets Catalog Lookups ──────────────────────────────────────────
# Prefer the catalog's displayed pet name. Keep this only for a future case
# where the two sites truly use different display names.
SLUG_ALIASES = {
    'ride_a_pet_potion': 'pet_riding_potion',
    'ride_a_pet_potion_forever': 'pet_riding_potion',
    'fly_a_pet_potion': 'pet_flying_potion',
    'fly_a_pet_potion_forever': 'pet_flying_potion',
}

def _build_catalog_indexes(catalog):
    """(Re)build the display-name -> StarPets identity maps for a catalog."""
    global _PRICEABLE_BASES, _DISPLAY_TO_BASE
    _PRICEABLE_BASES = {k.split("|", 1)[0] for k in catalog}
    display_to_bases = {}
    for key, record in catalog.items():
        # Include potions, petwear, vehicles and toys, but never ambiguous names.
        displayed = to_slug(record.get("name") or "")
        if displayed:
            display_to_bases.setdefault(displayed, set()).add(key.split("|", 1)[0])

    # An AMVGG display name is allowed to map only when StarPets has exactly one
    # internal pet identity for it. Ambiguity becomes "unpriced", never a guess.
    _DISPLAY_TO_BASE = {
        displayed: next(iter(bases))
        for displayed, bases in display_to_bases.items()
        if len(bases) == 1
    }


def reload_catalog(path=None):
    """Re-read the StarPets catalog after a refresh so live requests use it."""
    global CATALOG, CATALOG_PATH
    target = Path(path) if path else CATALOG_PATH
    with target.open() as f:
        catalog = json.load(f)
    for k, v in catalog.items():
        if isinstance(v, dict):
            v["_key"] = k
    CATALOG = catalog
    _build_catalog_indexes(CATALOG)
    return len(CATALOG)


_build_catalog_indexes(CATALOG)

def _resolve_catalog_base(base):
    """Resolve one deterministic StarPets pet identity without fuzzy matching."""
    alias = SLUG_ALIASES.get(base)
    if alias in _PRICEABLE_BASES:
        return alias
    if base in _PRICEABLE_BASES:
        return base
    return _DISPLAY_TO_BASE.get(base)

def _find_matching_bases(base):
    """Compatibility helper: there can be zero or one safe match."""
    resolved = _resolve_catalog_base(base)
    return [resolved] if resolved else []

def _lookup(name):
    """Return only the requested StarPets pet variant.

    Substituting another modifier, a base pet, or a similarly named item caused
    false profits, so a missing exact variant is deliberately left unpriced.
    """
    base, pumping, fly, ride = parse_variant_components(name)
    resolved = _resolve_catalog_base(base)
    if not resolved:
        return None, 1.0
    record = CATALOG.get(f"{resolved}|{pumping}|{int(fly)}|{int(ride)}")
    return (record, 1.0) if record else (None, 1.0)

PRICE_CONFIDENCE = {"none": 0, "low": 1, "medium": 2, "high": 3}
MIN_PRICE_CONFIDENCE = PRICE_CONFIDENCE["medium"]
STAR_PETS_SELLER_NET = 0.70

def pet_price_info(name):
    """Return auditable gross/net StarPets pricing for one exact variant."""
    record, _ = _lookup(name)
    demand = pet_sales_demand(record)
    signal = demand_resolver.resolve(name, record, demand)
    if not record:
        return {"priced": False, "reliable": False,
                "reason": "variant missing from StarPets catalog",
                "demand": demand, "demand_signal": signal}

    confidence = str(record.get("confidence") or "none").lower()
    gross = float(record.get("sellable_at") or 0)
    source = "historical average"
    if gross <= 0:
        gross = float(record.get("price_median") or record.get("price_low") or 0)
        source = "current listing"
    reliable = gross > 0 and PRICE_CONFIDENCE.get(confidence, 0) >= MIN_PRICE_CONFIDENCE
    return {
        "priced": gross > 0,
        "reliable": reliable,
        "gross": round(gross, 4),
        "net": round(gross * STAR_PETS_SELLER_NET, 4),
        "confidence": confidence,
        "source": source,
        "catalog_name": record.get("name"),
        "sales_per_day": demand['median_daily'] or 0,
        "demand": demand,
        "demand_signal": signal,
        "listing_count": int(record.get("count") or 0),
    }

def pet_is_pricable(name):
    return pet_price_info(name).get("reliable", False)

def with_demand(trade):
    """Attach exact-variant saved demand without interpreting failed polls as zero sales."""
    demand = {}
    for name in dict.fromkeys(trade.get('receive', []) + trade.get('give', [])):
        record, _ = _lookup(name)
        if record and trade.get('profit_usd', 0) > 0:
            sales_collector.enqueue(record.get('_key'), record)
        metric = pet_sales_demand(record)
        demand[name] = dict(metric, signal=demand_resolver.resolve(name, record, metric))
    prices = {}
    for name in demand:
        info = pet_price_info(name)
        prices[name] = round(info['net'], 2) if info.get('reliable') else None
    return dict(trade, item_demand=demand, item_net=prices)

def pet_sales_demand(record):
    cached = sales_collector.get((record or {}).get('_key'))
    metric = weekly_demand(cached or record)
    if metric['median_daily'] is None:
        metric['reason'] = sales_collector.describe((record or {}).get('_key'))
    return metric

# ── Conversions ───────────────────────────────────────────────────────
def pet_to_frost(name):
    return round(AMVGG_MAP.get(canonical_slug(name), 0), 2)

def pet_to_usd(name):
    """Estimated cash after seller deduction; zero means do not price it."""
    info = pet_price_info(name)
    return round(info.get("net", 0), 2) if info.get("reliable") else 0.0

def pet_sellability(name):
    info = pet_price_info(name)
    if not info.get("reliable"): return 0.0
    sales = info.get("sales_per_day", 0)
    if sales >= 5.0:   return 1.0
    if sales >= 2.0:   return 0.8
    if sales >= 0.5:   return 0.5
    if sales >= 0.1:   return 0.2
    return 0.0

# ── Trade Parsing Helpers ─────────────────────────────────────────────
LETTERS = {"f": "Fly", "r": "Ride", "n": "Neon", "m": "Mega"}

def expand_variant(base_name, vtype):
    if not vtype: return base_name
    parts = [LETTERS[c] for c in vtype if c in LETTERS]
    return f"{base_name} {' '.join(parts)}" if parts else base_name

_MALFORMED_DEBUG_COUNT = 0
def _reset_debug_counter():
    global _MALFORMED_DEBUG_COUNT
    _MALFORMED_DEBUG_COUNT = 0

def _extract_name(p):
    if not isinstance(p, dict): return None
    for field in ("name", "petName", "itemName", "productName"):
        v = p.get(field)
        if isinstance(v, str) and v.strip(): return v.strip()
    for k in ("item", "pet", "product"):
        n = p.get(k)
        if isinstance(n, dict):
            for field in ("name", "petName", "itemName"):
                v = n.get(field)
                if isinstance(v, str) and v.strip(): return v.strip()
    return None

def _extract_type(p):
    if not isinstance(p, dict): return ""
    for field in ("type", "variant", "petType", "itemType"):
        v = p.get(field)
        if isinstance(v, str): return v
    return ""

def _is_sign(p):
    return (isinstance(p, dict) and "sign" in p and isinstance(p.get("sign"), str) and p.get("sign"))

def _safe_pet_names(lst, label=""):
    if not isinstance(lst, list): return None, False, []
    result, signs = [], []
    for i, p in enumerate(lst):
        if not isinstance(p, dict): return None, False, []
        if _is_sign(p):
            sign_path = p.get("sign", "")
            sign_name = sign_path.split("/")[-1].replace(".webp", "")
            signs.append(sign_name)
            continue
        raw = _extract_name(p)
        if not raw: return None, False, []
        result.append(expand_variant(raw, _extract_type(p)))
    return result, True, signs

# ── Trade Evaluation ──────────────────────────────────────────────────
MIN_PROFIT_USD  = 1.00
MIN_PROFIT_PCT  = 0.05
FAIR_RANGE_LOW  = 1.25
FAIR_RANGE_HIGH = 1.60

def _evaluate_trade(trade, user_pets, ignore_inventory=False):
    looking  = trade.get("lookingFor", [])
    offering = trade.get("offering", [])
    if not looking or not offering: return None, "empty side"

    give_pets, ok, give_signs = _safe_pet_names(looking, label="lookingFor")
    if not ok: return None, "malformed lookingFor"
    receive_pets, ok, recv_signs = _safe_pet_names(offering, label="offering")
    if not ok: return None, "malformed offering"

    if not give_pets or not receive_pets: return None, "sign-only side"
    if any(p not in user_pets for p in give_pets) and not ignore_inventory:
        return None, "not in your inventory"
    if give_signs or recv_signs:
        all_signs = list(set(give_signs + recv_signs))
        return None, f"trade has signs ({', '.join(all_signs)})"

    give_frost = sum(pet_to_frost(p) for p in give_pets)
    recv_frost = sum(pet_to_frost(p) for p in receive_pets)
    frost_ratio = recv_frost / give_frost if give_frost else 0

    unknown_give = [p for p in give_pets if not pet_is_pricable(p)]
    unknown_recv = [p for p in receive_pets if not pet_is_pricable(p)]
    if unknown_give or unknown_recv:
        names = ", ".join((unknown_give + unknown_recv)[:3])
        return None, f"unpriced or low-confidence variant ({names})"

    give_usd = sum(pet_to_usd(p) for p in give_pets)
    recv_usd = sum(pet_to_usd(p) for p in receive_pets)
    profit   = recv_usd - give_usd
    if profit <= 0: return None, f"no USD profit (${profit:+.2f})"

    sellability = sum(pet_sellability(p) for p in receive_pets) / len(receive_pets)
    profit_pct  = profit / give_usd if give_usd else 0
    if profit < MIN_PROFIT_USD or profit_pct < MIN_PROFIT_PCT:
        return None, f"profit margin too small (${profit:+.2f}, {profit_pct:.1%})"

    stars = 0
    if profit >= MIN_PROFIT_USD:                             stars += 1
    if profit > 5:                                          stars += 1
    if profit > 20:                                         stars += 1
    if sellability >= 0.6:                                  stars += 1
    if FAIR_RANGE_LOW <= frost_ratio <= FAIR_RANGE_HIGH:    stars += 1

    return {
        "author": trade.get("authorName", "?"), "trade_id": trade.get("id", ""),
        "give": give_pets, "receive": receive_pets,
        "give_frost": round(give_frost, 2), "recv_frost": round(recv_frost, 2),
        "give_usd": round(give_usd, 2), "recv_usd": round(recv_usd, 2),
        "profit_usd": round(profit, 2), "profit_pct": round(profit_pct * 100, 2),
        "frost_ratio": round(frost_ratio, 2), "sellability": round(sellability, 2),
        "stars": stars, "unpriced": False, "unknown": [],
    }, None

# ── Amvgg Trade Fetching (Proxied) ────────────────────────────────────
AMVGG_LIMIT = 100  # endpoint hard-caps here; limit=200 returns HTTP 400
LAST_RETRY_AFTER = 0.0  # seconds the server asked us to wait, if it said so

def fetch_one_batch():
    from opportunities import elvebredd_trades
    extra = elvebredd_trades()
    batch, status, _ = _fetch_amvgg_batch()
    # Never mask an AMVGG rate-limit behind Elvebredd's success: the caller's
    # backoff depends on seeing the real status.
    return batch + extra, status

def _fetch_amvgg_batch(limit=AMVGG_LIMIT, cursor=None, use_proxy=True):
    """One page of the AMVGG trade feed. Returns (trades, status, next_cursor)."""
    global LAST_RETRY_AFTER
    url = f"https://amvgg.com/api/trades?limit={limit}"
    if cursor: url += "&cursor=" + quote(str(cursor), safe='')
    headers = {"User-Agent": UA, "Referer": "https://amvgg.com/trades", "Accept": "application/json"}
    proxy = random.choice(PROXIES) if (PROXIES and use_proxy) else None
    req_kwargs = {"headers": headers, "timeout": 15}
    if proxy: req_kwargs["proxies"] = {"http": proxy, "https": proxy}
    try:
        r = requests.get(url, **req_kwargs)
        if r.status_code == 429:
            try: LAST_RETRY_AFTER = float(r.headers.get("Retry-After") or 0)
            except (TypeError, ValueError): LAST_RETRY_AFTER = 0.0
            return [], r.status_code, None
        if r.status_code != 200: return [], r.status_code, None
        LAST_RETRY_AFTER = 0.0
        data = r.json()
        nxt = (data.get("pagination") or {}).get("nextCursor")
        return data.get("trades", []), 200, nxt
    except Exception: return [], 0, None

def backfill(pages=2, limit=AMVGG_LIMIT):
    """Walk the cursor back a few pages so a cold start has real history."""
    out, cursor = [], None
    for _ in range(max(0, pages)):
        trades, status, cursor = _fetch_amvgg_batch(limit, cursor, use_proxy=False)
        if status != 200 or not trades: break
        out.extend(trades)
        if not cursor: break
    return out

def fetch_trades(max_fetches=25, delay=1.5, debug=False):
    out, seen = [], set()
    rate_limit_streak = 0
    empty_streak = 0
    for attempt in range(max_fetches):
        batch, status = fetch_one_batch()
        if status == 429 or status == 0:
            rate_limit_streak += 1
            if rate_limit_streak >= 3: break
            time.sleep(2)
            continue
        rate_limit_streak = 0
        if status != 200: break
        new = 0
        for t in batch:
            tid = t.get("id")
            if tid and tid not in seen:
                seen.add(tid); out.append(t); new += 1
        if debug and new > 0: print(f"    ← {len(batch)} trades, {new} new, {len(out)} total")
        if new == 0:
            empty_streak += 1
            if empty_streak >= 3: break
        else: empty_streak = 0
        time.sleep(delay)
    return out

# ── ACCEPT TRADE FUNCTION ─────────────────────────────────────────────
def accept_trade(trade_id):
    """Sends the authenticated POST request to amvgg to accept the trade."""
    if str(trade_id).startswith('elvebredd:'):
        return False
    url = "https://amvgg.com/api/acceptTrade"
    headers = {
        "User-Agent": UA,
        "Referer": "https://amvgg.com/trades",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    # Don't use proxies for authenticated actions to prevent bans
    try:
        r = requests.post(url, json={"tradeId": str(trade_id)}, headers=headers, cookies=COOKIES, timeout=10)
        print(f"  [accept] status={r.status_code} body={r.text[:200]}")
        return r.status_code == 200
    except Exception as e:
        print(f"  [accept] error: {e}")
        return False

# ── Ranking ───────────────────────────────────────────────────────────
def rank_trades(trades, user_pets, min_stars=2, stats=None, ignore_inventory=False):
    """Rank trades. ignore_inventory=True explores every offer, not just yours."""
    if stats is None: stats = {}
    passed = []
    for t in trades:
        result, reason = _evaluate_trade(t, user_pets, ignore_inventory=ignore_inventory)
        if result:
            if result["stars"] >= min_stars: passed.append(result)
            else: stats["below min stars"] = stats.get("below min stars", 0) + 1
        else: stats[reason] = stats.get(reason, 0) + 1
    stats["TOTAL SCANNED"] = len(trades)
    stats["PASSED"]        = len(passed)
    passed.sort(key=lambda x: (-x["stars"], -x["profit_usd"]))
    return passed

def get_near_misses(trades, user_pets, limit=30, ignore_inventory=False):
    out = []
    skip = ("not in your inventory", "empty side", "sign-only side",
            "malformed lookingFor", "malformed offering", "below min stars")
    for t in trades:
        result, reason = _evaluate_trade(t, user_pets, ignore_inventory=ignore_inventory)
        if result is not None or reason in skip: continue
        if reason.startswith("trade has signs"): continue
        gp, _, _ = _safe_pet_names(t.get("lookingFor", []), label="d")
        rp, _, _ = _safe_pet_names(t.get("offering", []), label="d")
        if not gp or not rp: continue
        give_frost = sum(pet_to_frost(p) for p in gp)
        recv_frost = sum(pet_to_frost(p) for p in rp)
        give_usd   = sum(pet_to_usd(p) for p in gp)
        recv_usd   = sum(pet_to_usd(p) for p in rp)
        unknown = ([p for p in gp if not pet_is_pricable(p) and pet_to_frost(p) > 0]
                 + [p for p in rp if not pet_is_pricable(p) and pet_to_frost(p) > 0])
        out.append({
            "author": t.get("authorName", "?"), "trade_id": t.get("id", ""),
            "give": gp, "receive": rp,
            "give_frost": round(give_frost, 2), "recv_frost": round(recv_frost, 2),
            "give_usd": round(give_usd, 2), "recv_usd": round(recv_usd, 2),
            "profit_usd": round(recv_usd - give_usd, 2),
            "frost_ratio": round(recv_frost / give_frost, 2) if give_frost else 0,
            "reason": reason, "unknown": unknown[:3],
        })
    out.sort(key=lambda x: -x["profit_usd"])
    return out[:limit]

def get_target_trades(trades, target_substring, limit=20):
    """Profitable offers taking the exact selected variant, independent of feed."""
    target = canonical_slug(target_substring)
    out = []
    for trade in trades:
        give, ok, signs = _safe_pet_names(trade.get('lookingFor', []))
        if not ok or signs or not any(canonical_slug(p) == target for p in give):
            continue
        result, _ = _evaluate_trade(trade, give)
        if result:
            result['wants_target'] = True
            out.append(result)
    return sorted(out, key=lambda t: -t['profit_usd'])[:limit]

def _legacy_target_trades(trades, target_substring, limit=20):
    target = target_substring.lower()
    out = []
    for t in trades:
        gp, ok1, _ = _safe_pet_names(t.get("lookingFor", []), label="d")
        rp, ok2, _ = _safe_pet_names(t.get("offering", []), label="d")
        if not ok1 or not ok2: continue
        g_match = any(target in p.lower() for p in gp)
        r_match = any(target in p.lower() for p in rp)
        if not (g_match or r_match): continue
        give_usd = sum(pet_to_usd(p) for p in gp)
        recv_usd = sum(pet_to_usd(p) for p in rp)
        out.append({
            "author": t.get("authorName", "?"), "trade_id": t.get("id", ""),
            "give": gp, "receive": rp,
            "give_frost": round(sum(pet_to_frost(p) for p in gp), 2),
            "recv_frost": round(sum(pet_to_frost(p) for p in rp), 2),
            "give_usd": round(give_usd, 2), "recv_usd": round(recv_usd, 2),
            "profit_usd": round(recv_usd - give_usd, 2), "wants_target": g_match,
        })
    out.sort(key=lambda x: -x["profit_usd"])
    return out[:limit]
