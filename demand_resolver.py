"""
demand_resolver.py - one verdict from three demand sources.

Sources, in priority order:

  1. variant_daily_totals        verified per-variant daily sales (demand.py).
                                 Authoritative when present.
  2. starpets_sales_per_listing  sales per active listing, price-neutral.
                                 Passes on High/Medium unless confidence is low.
  3. amvgg_rating                amvgg's own Low/Medium/High label per pumping
                                 level. Passes on High and Medium.

Provenance and confidence travel with the verdict so the UI can never present a
rating as if it were measured sales.

Nothing here imports bot: bot imports demand, and a bot import would be circular.
Name parsing is duplicated deliberately and kept small.
"""
import json
import os
import re

MAX_LEVEL = "Medium"          # lowest rating that still passes the gate
LEVELS = {"High": 3, "Medium": 2, "Low": 1}

_SUFFIX = re.compile(r"((?:\s+(?:Fly|Ride|Neon|Mega))+)\s*$", re.IGNORECASE)

DEFAULT_AMVGG = os.path.join("data", "amvgg_demand.json")
DEFAULT_STARPETS = os.path.join("data", "starpets_demand.json")

_amvgg_cache = None
_starpets_cache = None


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def load_amvgg(path=None):
    global _amvgg_cache
    if _amvgg_cache is None:
        blob = _load(path or DEFAULT_AMVGG) or {}
        _amvgg_cache = {p["name"]: p for p in blob.get("pets", []) if p.get("name")}
    return _amvgg_cache


def load_starpets(path=None):
    global _starpets_cache
    if _starpets_cache is None:
        blob = _load(path or DEFAULT_STARPETS) or {}
        _starpets_cache = blob.get("variants") or {}
    return _starpets_cache


def reset_cache():
    """Tests rebuild the caches against fixture files."""
    global _amvgg_cache, _starpets_cache
    _amvgg_cache = None
    _starpets_cache = None


def split_name(name):
    """'Frost Dragon Fly Ride' -> ('Frost Dragon', 'default', True, True)."""
    m = _SUFFIX.search(name or "")
    if not m:
        return (name or "").strip(), "default", False, False
    suffix = m.group(1).lower()
    base = (name or "")[:m.start()].strip()
    pumping = "mega_neon" if " mega" in suffix else "neon" if " neon" in suffix else "default"
    return base, pumping, " fly" in suffix, " ride" in suffix


def _pumping_field(pumping):
    return {"default": "regular", "neon": "neon", "mega_neon": "mega"}.get(pumping, "regular")


def amvgg_level(base, pumping):
    """amvgg rates per pumping level, not per fly/ride combination."""
    rec = load_amvgg().get(base)
    if not rec:
        return None, None
    return rec.get("demand", {}).get(_pumping_field(pumping)), rec


def starpets_level(catalog_key):
    if not catalog_key:
        return None, None
    rec = load_starpets().get(catalog_key)
    if not rec:
        return None, None
    return rec.get("rating"), rec


def _blank(name):
    return {
        "pet": name, "ok": False, "source": "none", "level": None,
        "label": "no demand signal", "confidence": "none", "details": {},
    }


def _verdict(name, source, level, ok, label, confidence, details):
    return {"pet": name, "source": source, "level": level, "ok": ok,
            "label": label, "confidence": confidence, "details": details}


def resolve(name, record=None, verified_demand=None):
    """Return the demand verdict for one exact pet variant.

    record: the StarPets catalog entry (uses record['_key'] for variant identity).
    verified_demand: result of demand.weekly_demand(record), if already computed.
    """
    base, pumping, _fly, _ride = split_name(name)

    # 1. verified daily sales dominate everything else
    if verified_demand and verified_demand.get("median_daily") is not None:
        median = verified_demand["median_daily"]
        level = "High" if median >= 5 else "Medium" if median >= 2 else "Low"
        return _verdict(
            name, "variant_daily_totals", level, median >= 2,
            f"{median:g} verified sales/day", "high",
            {"median_daily": median, "weekly_total": verified_demand.get("weekly_total")})

    # 2. StarPets sales per listing
    sp_candidate = None
    level, sp = starpets_level((record or {}).get("_key"))
    if level in LEVELS:
        conf = (sp or {}).get("confidence", "medium")
        ok = LEVELS[level] >= LEVELS[MAX_LEVEL] and conf != "low"
        sp_candidate = _verdict(
            name, "starpets_sales_per_listing", level, ok,
            f"StarPets {level.lower()} demand"
            + (" (thin listing count)" if conf == "low" else ""),
            conf,
            {"sales_per_listing": (sp or {}).get("sales_per_listing"),
             "listings": (sp or {}).get("listings"),
             "daily_sales": (sp or {}).get("daily_sales")})
        if ok:
            return sp_candidate

    # 3. amvgg rating
    level, am = amvgg_level(base, pumping)
    if level in LEVELS:
        return _verdict(
            name, "amvgg_rating", level, LEVELS[level] >= LEVELS[MAX_LEVEL],
            f"amvgg rates {pumping.replace('_', ' ')} demand {level.lower()}",
            "medium",
            {"values": (am or {}).get("values", {}),
             "demand": (am or {}).get("demand", {})})

    # nothing passed; still report the StarPets evidence if we had any
    return sp_candidate or _blank(name)
