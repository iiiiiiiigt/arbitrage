"""
scrape_amvgg.py — pull amvgg's pet value + demand dataset.

There is no demand *API*. The /trades page ships the whole table inside its
server-rendered payload as a JSON array, which this script extracts:

  803 base pets, each with
    category, regularValue, neonValue, megaValue
    regularDemand, neonDemand, megaDemand   ("Low" | "Medium" | "High")

  ~29 of them additionally carry the full six-way variant matrix
    r/f/nr/nf/mr/mf Value + Demand, plus the no-potion (np*) values.

Demand here is a qualitative rating published by amvgg, not sales counts.

Usage:
  python scrape_amvgg.py                 # write data/amvgg_demand.json
  python scrape_amvgg.py --print-summary
"""
import argparse
import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone

import requests

PAGE = "https://amvgg.com/trades"
OUT = os.path.join("data", "amvgg_demand.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

VARIANT_KEYS = ("r", "f", "nr", "nf", "mr", "mf")


def fetch_page(retries=3):
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(PAGE, headers={"User-Agent": UA, "Accept": "text/html"},
                             timeout=40)
            if r.status_code == 200:
                return r.text
            last = f"HTTP {r.status_code}"
        except Exception as e:
            last = type(e).__name__
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"could not fetch {PAGE}: {last}")


def unescape(html):
    """The payload is a JS string literal; undo one level of escaping."""
    return html.replace('\\\\"', '\\"').replace('\\"', '"')


def find_array(raw, needle, max_back=4000):
    """Locate the JSON array that encloses the first `needle` occurrence."""
    idx = raw.find(needle)
    if idx < 0:
        return None
    start = raw.rfind('[{"id"', max(0, idx - max_back), idx)
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for j in range(start, len(raw)):
        c = raw[j]
        if in_str:
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == '[':
            depth += 1
        elif c == ']':
            depth -= 1
            if depth == 0:
                return json.loads(raw[start:j + 1])
    return None


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def normalise(pet):
    rec = {
        "name": pet.get("name"),
        "amvgg_id": pet.get("id"),
        "category": pet.get("category"),
        "values": {
            "regular": num(pet.get("regularValue")),
            "neon": num(pet.get("neonValue")),
            "mega": num(pet.get("megaValue")),
        },
        "demand": {
            "regular": pet.get("regularDemand"),
            "neon": pet.get("neonDemand"),
            "mega": pet.get("megaDemand"),
        },
        "variants": {},
    }
    if pet.get("npRegularValue") is not None:
        rec["values"].update({
            "np_regular": num(pet.get("npRegularValue")),
            "np_neon": num(pet.get("npNeonValue")),
            "np_mega": num(pet.get("npMegaValue")),
        })
        rec["demand"].update({
            "np_regular": pet.get("npRegularDemand"),
            "np_neon": pet.get("npNeonDemand"),
            "np_mega": pet.get("npMegaDemand"),
        })
    for k in VARIANT_KEYS:
        v = pet.get(f"{k}Value")
        d = pet.get(f"{k}Demand")
        if v is not None or d is not None:
            rec["variants"][k] = {"value": num(v), "demand": d}
    return rec


def extract(html=None, print_summary=False):
    raw = unescape(html or fetch_page())
    pets_raw = find_array(raw, '"regularDemand"')
    if not pets_raw:
        raise RuntimeError("pet dataset not found in the page payload")

    pets = [normalise(p) for p in pets_raw]
    # the richer six-way records appear in a later array; merge by name if found
    extra = find_array(raw, '"rValue"')
    merged = 0
    if extra:
        by_name = {p["name"]: p for p in pets}
        for p in extra:
            target = by_name.get(p.get("name"))
            if not target:
                continue
            rich = normalise(p)
            target["values"].update({k: v for k, v in rich["values"].items() if v is not None})
            target["demand"].update({k: v for k, v in rich["demand"].items() if v})
            target["variants"].update(rich["variants"])
            merged += 1

    blob = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": PAGE,
        "note": ("Demand ratings are amvgg's qualitative labels (Low/Medium/High), "
                 "not sales counts."),
        "pets": pets,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(blob, f, indent=1, ensure_ascii=True)

    if print_summary:
        dem = Counter()
        for p in pets:
            for k in ("regular", "neon", "mega"):
                dem[p["demand"][k]] += 1
        with_variants = sum(1 for p in pets if p["variants"])
        print(f"  pets                : {len(pets)}")
        print(f"  with 6-way variants : {with_variants} (merged {merged})")
        print(f"  demand values       : {dict(dem)}")
        print(f"  file                : {OUT} ({os.path.getsize(OUT)/1e6:.2f} MB)")
        hi = [p for p in pets if p["demand"]["regular"] == "High"]
        print(f"  High regular demand : {len(hi)}")
        for p in sorted(hi, key=lambda x: -(x["values"]["regular"] or 0))[:8]:
            print(f"    {p['name']:<26} regular={p['values']['regular']:<10} "
                  f"demand={p['demand']['regular']}")
    return blob


def main():
    ap = argparse.ArgumentParser(description="Extract amvgg pet values + demand")
    ap.add_argument("--print-summary", action="store_true")
    args = ap.parse_args()
    blob = extract(print_summary=args.print_summary)
    print(f"wrote {OUT} ({len(blob['pets'])} pets)")


if __name__ == "__main__":
    main()
