"""
demand_refresh.py - keep the demand datasets current.

Two datasets, very different costs:

  amvgg_demand.json      one page fetch (~450 KB), 803 pets.
                         Cheap enough to refresh inline, in CI or in the app.

  starpets_demand.json   requires the full catalog crawl with --sum
                         (~6 requests per variant, ~40k total). Must NOT run in
                         a 5-minute CI job. Intended for a daily job on your PC.

Usage
-----
  python demand_refresh.py --status            # what is stale
  python demand_refresh.py                     # amvgg only (cheap default)
  python demand_refresh.py --force --amvgg     # ignore TTL
  python demand_refresh.py --starpets          # the expensive one
  python demand_refresh.py --all               # both
  python demand_refresh.py --loop 3600         # stay resident (amvgg only)

StarPets is never refreshed by default: it needs a full catalog crawl.

Environment
-----------
  DEMAND_AMVGG_TTL_HOURS    default 12
  DEMAND_STARPETS_TTL_HOURS default 24

Nothing here raises on a failed fetch: a stale file is better than no file, so
callers keep working with the previous snapshot.
"""
import argparse
import asyncio
import json
import os
import sys
import time
import traceback

import demand_resolver

AMVGG_PATH = os.path.join("data", "amvgg_demand.json")
STARPETS_CATALOG = os.path.join("data", "starpets_catalog.json")
STARPETS_PATH = os.path.join("data", "starpets_demand.json")
LOCK_PATH = os.path.join("data", ".starpets_refresh.lock")
LOCK_MAX_AGE = 6 * 3600          # a crawl that has not finished in 6h is dead

AMVGG_TTL_HOURS = float(os.environ.get("DEMAND_AMVGG_TTL_HOURS", "12"))
STARPETS_TTL_HOURS = float(os.environ.get("DEMAND_STARPETS_TTL_HOURS", "24"))


def _generated_epoch(path):
    """Timestamp recorded inside the file, if any (CI resets mtimes on checkout)."""
    try:
        with open(path, encoding="utf-8") as f:
            blob = json.load(f)
    except (OSError, ValueError):
        return None
    stamp = blob.get("generated_at")
    if isinstance(stamp, (int, float)):
        return float(stamp)
    if isinstance(stamp, str):
        try:
            from datetime import datetime, timezone
            return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def age_hours(path):
    """Hours since the data was generated, or None when it does not exist."""
    stamp = _generated_epoch(path)
    if stamp is not None:
        return max(0.0, (time.time() - stamp) / 3600.0)
    try:
        return (time.time() - os.path.getmtime(path)) / 3600.0
    except OSError:
        return None


def is_due(path, ttl_hours):
    a = age_hours(path)
    return a is None or a >= ttl_hours


def _lock_active():
    """True when another StarPets crawl holds the lock and looks alive."""
    try:
        if time.time() - os.path.getmtime(LOCK_PATH) > LOCK_MAX_AGE:
            return False          # stale lock from a dead process
        return True
    except OSError:
        return False


def _acquire_lock():
    if _lock_active():
        return False
    try:
        os.makedirs(os.path.dirname(LOCK_PATH) or ".", exist_ok=True)
        with open(LOCK_PATH, "w") as f:
            f.write(str(os.getpid()))
        return True
    except OSError:
        return False


def _release_lock():
    try:
        os.remove(LOCK_PATH)
    except OSError:
        pass


def refresh_amvgg(force=False, fetcher=None):
    """Fetch amvgg's embedded value/demand table. Returns True on success."""
    if not force and not is_due(AMVGG_PATH, AMVGG_TTL_HOURS):
        return False
    try:
        if fetcher is None:
            import scrape_amvgg
            fetcher = scrape_amvgg.extract
        blob = fetcher(print_summary=False)
        demand_resolver.reset_cache()
        print(f"[amvgg] refreshed {len(blob['pets'])} pets -> {AMVGG_PATH}", flush=True)
        return True
    except Exception as e:
        print(f"[amvgg] refresh failed ({type(e).__name__}: {e}); keeping previous file",
              flush=True)
        return False


def refresh_starpets(force=False, sum_mode=True, limit=None, names=None):
    """Full catalog crawl + sell-through scoring. Expensive; run daily."""
    if not force and not is_due(STARPETS_PATH, STARPETS_TTL_HOURS):
        return False
    if not _acquire_lock():
        print("[starpets] another refresh is already running; skipping", flush=True)
        return False
    try:
        import scrape_starpets as ss
        import demand_starpets as ds

        argv = ["--sum"] if sum_mode else []
        if limit:
            argv += ["--limit", str(limit)]
        if names:
            argv += ["--names", names]
        args = ss.parse_args(argv)
        print("[starpets] catalog crawl starting (this takes a while)", flush=True)
        asyncio.run(ss.run(args))

        with open(ss.CATALOG_PATH, encoding="utf-8") as f:
            catalog = json.load(f)
        if not catalog:
            raise RuntimeError("catalog is empty; refusing to write demand")
        scored = ds.compute(ds.records_from_catalog(catalog))
        if not scored:
            raise RuntimeError("no variants scored; refusing to overwrite demand")
        with open(STARPETS_PATH, "w", encoding="utf-8") as f:
            json.dump({"scope": ds.SCOPE, "generated_at": time.time(),
                       "variants": scored}, f, indent=1)
        demand_resolver.reset_cache()
        print(f"[starpets] scored {len(scored)} variants -> {STARPETS_PATH}", flush=True)
        return True
    except Exception as e:
        print(f"[starpets] refresh failed ({type(e).__name__}: {e}); keeping previous file",
              flush=True)
        traceback.print_exc(limit=2)
        return False
    finally:
        _release_lock()


def refresh_due(force=False, include_starpets=False, fetcher=None):
    """Refresh what is due.

    StarPets is opt-in: it needs a full catalog crawl (~40k requests), which is
    never appropriate as a default. Only the cheap amvgg table refreshes here
    unless include_starpets is set.
    """
    did = refresh_amvgg(force=force, fetcher=fetcher)
    if include_starpets:
        did = refresh_starpets(force=force) or did
    return did


def status():
    for label, path, ttl in (("amvgg", AMVGG_PATH, AMVGG_TTL_HOURS),
                             ("starpets", STARPETS_PATH, STARPETS_TTL_HOURS)):
        a = age_hours(path)
        if a is None:
            print(f"  {label:<9} missing            -> due")
        else:
            print(f"  {label:<9} {a:6.1f}h old (ttl {ttl:g}h) -> "
                  f"{'due' if a >= ttl else 'fresh'}")


def main():
    ap = argparse.ArgumentParser(description="Refresh demand datasets")
    ap.add_argument("--force", action="store_true", help="ignore the TTL")
    ap.add_argument("--amvgg", action="store_true", help="amvgg only (cheap, default)")
    ap.add_argument("--starpets", action="store_true",
                    help="StarPets only (EXPENSIVE: full catalog crawl)")
    ap.add_argument("--all", action="store_true", help="both")
    ap.add_argument("--no-sum", action="store_true", help="cheapest listing only")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--names", type=str, default=None)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--loop", type=int, default=0, metavar="SECONDS",
                    help="stay resident and re-check on this interval")
    args = ap.parse_args()

    if args.status:
        print("demand dataset status:")
        status()
        return 0

    def once():
        if args.all:
            return refresh_due(force=args.force, include_starpets=True)
        if args.starpets:
            return refresh_starpets(force=args.force, sum_mode=not args.no_sum,
                                    limit=args.limit, names=args.names)
        # --amvgg or no flags: cheap path only
        return refresh_amvgg(force=args.force)

    if not once():
        print("nothing was due (use --force to refresh anyway)")
        print("note: StarPets needs --starpets or --all; it is a full crawl")

    if args.loop:
        print(f"looping every {args.loop}s; ctrl-c to stop", flush=True)
        try:
            while True:
                time.sleep(args.loop)
                once()
        except KeyboardInterrupt:
            print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
