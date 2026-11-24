"""
demand_starpets.py - demand from StarPets sales, corrected for supply.

The problem
-----------
Raw sales counts punish expensive items. A $500 pet selling 3/day can be under
far more pressure than a $5 common selling 50/day, because there are 4 listings
of one and 900 of the other.

The metric
----------
Sales per active listing per day:

    sales_per_listing = daily_sales / max(active_listings, 1)

This is price-neutral by construction. It answers "how much throughput does each
competing offer generate", which is the question that matters. An expensive item
with few sellers can post a high figure on low absolute volume; a cheap item
buried under hundreds of listings posts a low one on high volume.

    days_per_sale = active_listings / daily_sales

The inverse: how many days it takes, on average, for each active listing to turn
over a single sale. Smaller is hotter.

A variant with 4 listings selling 3/day     -> 0.75 per listing, 1.3 days  hot
A variant with 900 listings selling 50/day  -> 0.06 per listing, 18 days   ordinary

Two guards against nonsense
---------------------------
1. Noise floor: one listing selling once gives sales_per_listing = 1.0, which is
   meaningless. Ratings require a minimum absolute daily volume.
2. Tier context: the figure is also reported relative to the median of the same
   price tier, so "3x faster than other expensive items" is visible without
   letting the tier average drive the rating.

Honest limits
-------------
* A StarPets listing is a seller's offer, not one unit of stock, so this is not
  a stock-turnover rate. It measures throughput per competing offer.
* active_listings comes from the catalog crawl, which is page-capped. It is a
  consistent undercount, so read this as a relative ranking, not an absolute
  rate. Variants with a single observed listing are flagged low-confidence.
* StarPets sales are per LISTING. One listing carries ~98% of a variant's
  volume (measured), so summing every listing is correct but costs ~6x requests.
* This is NOT a sales-count scope and must never be presented as one.
"""
import argparse
import json
import os
from collections import defaultdict

SCOPE = "starpets_sales_per_listing"

# thresholds on sales per listing per day
HIGH_SPL = 0.25          # each listing turns over a sale every ~4 days
MEDIUM_SPL = 0.08        # every ~12 days
HIGH_MIN_SALES = 0.5     # units/day, noise floor for High
MEDIUM_MIN_SALES = 0.2   # units/day, noise floor for Medium


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    mid = n // 2
    return xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2


def price_tiers(prices, count=5):
    """Quantile edges for price buckets. Returns a function price -> tier index."""
    xs = sorted(p for p in prices if p and p > 0)
    if not xs:
        return lambda p: 0
    edges = []
    for i in range(1, count):
        idx = min(len(xs) - 1, int(round(i * len(xs) / count)))
        edges.append(xs[idx])

    def tier(price):
        if not price or price <= 0:
            return 0
        t = 0
        for e in edges:
            if price >= e:
                t += 1
        return t

    return tier


def compute(records, tiers=5):
    """Score every record.

    records: iterable of {name, price, daily_sales, supply, days_with_sales?}
    returns: dict name -> metrics
    """
    rows = []
    for r in records:
        price = r.get("price")
        sales = r.get("daily_sales")
        supply = r.get("supply")
        rows.append({
            # keyed by variant, not by name: "Frost Dragon" has 12 variants
            "key": r.get("key") or r.get("name"),
            "name": r.get("name"),
            "price": price if isinstance(price, (int, float)) and price > 0 else None,
            "daily_sales": sales if isinstance(sales, (int, float)) and sales >= 0 else None,
            "supply": supply if isinstance(supply, int) and supply >= 0 else None,
            "days_with_sales": r.get("days_with_sales"),
        })

    tier_of = price_tiers([r["price"] for r in rows], tiers)

    tier_samples = defaultdict(list)
    for r in rows:
        if r["daily_sales"] and r["supply"]:
            tier_samples[tier_of(r["price"])].append(r["daily_sales"] / max(r["supply"], 1))
    tier_median = {t: _median(v) for t, v in tier_samples.items()}
    overall = _median([x for v in tier_samples.values() for x in v])

    out = {}
    for r in rows:
        price, sales, supply = r["price"], r["daily_sales"], r["supply"]
        rec = {
            "name": r["name"],
            "price": price,
            "daily_sales": sales,
            "listings": supply,
            "demand_scope": SCOPE,
        }
        key = r["key"]

        if sales is None or supply is None or not price:
            rec.update(rating="Unknown", reason="missing sales, listings or price",
                       sales_per_listing=None, days_per_sale=None,
                       tier_relative=None, confidence="none")
            out[key] = rec
            continue

        if sales <= 0:
            rec.update(rating="Low", reason="no sales in the window",
                       sales_per_listing=0.0, days_per_sale=None,
                       tier_relative=0.0, confidence="none")
            out[key] = rec
            continue

        spl = sales / max(supply, 1)
        dps = (supply / sales) if sales > 0 else None
        base = tier_median.get(tier_of(price)) or overall
        rel = (spl / base) if base else None

        if spl >= HIGH_SPL and sales >= HIGH_MIN_SALES:
            rating = "High"
        elif spl >= MEDIUM_SPL and sales >= MEDIUM_MIN_SALES:
            rating = "Medium"
        else:
            rating = "Low"

        # a single observed listing is the weakest input this model can get
        if supply <= 1:
            confidence = "low"
        elif r["days_with_sales"] is not None and r["days_with_sales"] >= 5:
            confidence = "high"
        else:
            confidence = "medium"

        reasons = [f"{sales:.2f} sold/day across {supply} listing(s)",
                   f"{spl:.2f} sales per listing per day"]
        if dps is not None:
            reasons.append(f"one sale per listing every {dps:.1f}d")
        if rel is not None:
            reasons.append(f"{rel:.2f}x the median for its price tier")
        if sales < HIGH_MIN_SALES:
            reasons.append("volume below the noise floor")
        if supply <= 1:
            reasons.append("listing count looks page-capped")

        rec.update(rating=rating, reason="; ".join(reasons),
                   sales_per_listing=round(spl, 4),
                   days_per_sale=round(dps, 2) if dps is not None else None,
                   tier_relative=round(rel, 3) if rel is not None else None,
                   confidence=confidence)
        out[key] = rec

    return out


def records_from_catalog(catalog):
    """Adapt data/starpets_catalog.json to the fields this module needs."""
    rows = []
    for key, rec in (catalog or {}).items():
        sales = rec.get("daily_sales")
        daily = None
        if isinstance(sales, list) and sales:
            daily = _median([d.get("count") for d in sales
                             if isinstance(d, dict) and d.get("count") is not None])
        if daily is None:
            v = rec.get("avg_daily_sales")
            daily = v if isinstance(v, (int, float)) else None
        rows.append({
            "key": key,
            "name": rec.get("name") or key,
            "price": rec.get("sellable_at") or rec.get("price_median"),
            "daily_sales": daily,
            "supply": rec.get("count"),
            "days_with_sales": rec.get("days_with_sales"),
        })
    return rows


def main():
    ap = argparse.ArgumentParser(description="StarPets sales-per-listing demand")
    ap.add_argument("--catalog", default=os.path.join("data", "starpets_catalog.json"))
    ap.add_argument("--out", default=os.path.join("data", "starpets_demand.json"))
    ap.add_argument("--tiers", type=int, default=5)
    ap.add_argument("--top", type=int, default=12)
    args = ap.parse_args()

    with open(args.catalog, encoding="utf-8") as f:
        catalog = json.load(f)
    rows = records_from_catalog(catalog)

    thin = sum(1 for r in rows if r["supply"] <= 1)
    if thin > len(rows) * 0.5:
        print(f"WARNING: {thin}/{len(rows)} records have <=1 observed listing.")
        print("         The catalog crawl looks page-capped; ratings will be inflated.")
        print("         Re-run scrape_starpets.py --sum before trusting this output.\n")

    scored = compute(rows, tiers=args.tiers)

    rated = [v for v in scored.values() if v["rating"] in ("High", "Medium")]
    rated.sort(key=lambda v: (-{"High": 2, "Medium": 1}.get(v["rating"], 0),
                              -(v["sales_per_listing"] or 0)))
    print(f"variants scored : {len(scored)}")
    for k in ("High", "Medium", "Low", "Unknown"):
        print(f"  {k:<8} {sum(1 for v in scored.values() if v['rating'] == k)}")
    if rated:
        print(f"\ntop ({args.top}):")
        for v in rated[:args.top]:
            print(f"  {str(v['name'])[:24]:<24} ${v['price']:<8.2f} "
                  f"{v['daily_sales']:>6.2f}/day listings={v['listings']:<4} "
                  f"SPL={v['sales_per_listing']:<7} {v['rating']:<6} conf={v['confidence']}")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"scope": SCOPE, "variants": scored}, f, indent=1)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
