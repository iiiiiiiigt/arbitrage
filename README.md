# Adopt Me trade research

Local Flask dashboard comparing estimated resale values and observed AMVGG and Elvebredd offers.

## Run locally

```sh
python -m pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000. Keep this local application private: authenticated trade actions use your local session. Cookies and proxy credentials are intentionally excluded from Git.

Catalog prices are saved estimates, not executable quotes. Calculations assume a 30% seller commission. Trade acceptance is not confirmation of an in-game exchange.

Demand scoring uses the median of daily sales totals for the last seven complete UTC days for the exact pet variant. The displayed weekly count is their sum. Confirmed zero-sale days count; missing dates, stale history, and unverified listing-level statistics do not establish demand. The existing catalog averages cannot be converted into medians. StarPets history acquisition remains unverified: the scraper preserves raw product statistics for inspection but does not label them market-wide daily sales.

Verified catalog records must supply `demand_scope: "variant_daily_totals"` and `daily_sales: [{"date": "YYYY-MM-DD", "count": 10}, ...]`, one total per day. Only set that scope after verifying the source covers all sales of that exact variant. Unknown demand cannot satisfy the sellability threshold.

## GitHub Pages

The `docs/` directory contains the static dashboard, generated from the same templates by `python build_pages.py`. Browser-local inventory and focus selection work with the bundled catalog and trade snapshots. GitHub Actions rebuilds and publishes approximately every five minutes; scheduled jobs may be delayed. The page displays the snapshot timestamp. The catalog itself remains a saved estimate until updated separately.

GitHub Pages cannot run Flask or call the marketplaces directly because their APIs do not allow cross-origin browser requests. Acceptance opens the marketplace website, and no session cookies are published. Use the local Flask app for faster live polling. Only `docs/` is deployed.

## Tests

## Live data on Pages

The live collector now queues targeted StarPets history checks for exact variants in observed trades. A single worker spaces requests at least two seconds apart, caches successful history for six hours, and pauses for at least 15 minutes after upstream failures (longer when Retry-After requires it). The public production API is the only source used; the alternate `.dev` host returned different IDs and empty Giant Panda history and is not treated as live data. Production access can still fail; no cached average is converted into a median.

History is saved to `data/starpets_sales_history.json`. Counts represent the currently enumerated product IDs and are labeled observed sales, not exhaustive marketplace totals. Failed or malformed responses are never zero-filled. A successful response reporting no sales means zero observed sales for that product. AMVGG ratings are displayed separately as trade interest and cannot qualify a cash exit. Broad StarPets scraping is opt-in (`DEMAND_STARPETS_AUTOREFRESH=1`); targeted history checks replace it by default.

Trade cards show up to three alternative observed downgrades, including extra net value, uplift percentage, resulting holdings value and source links. Quantities must fit the received basket. Cash qualification checks retained pets too. These are competing alternatives, not a sum of guaranteed profits; the offers may expire before either trade completes.

Run `powershell -ExecutionPolicy Bypass -File start_live.ps1` from this folder with the official Cloudflare `cloudflared.exe` installed in `tools/`. The launcher starts a separate read-only collector on port 5051 and a temporary HTTPS tunnel. Open the printed Pages link to connect that browser. It polls the collector every two seconds; upstream rate limits and latency still apply.

The local Flask dashboard does not need to be open or scanning. Inventory and focus are browser-local, independent of the Flask dashboard. Keep the collector and tunnel running and the PC awake. Quick Tunnel addresses change on restart; use the new link. Only public market research is exposed; there is no acceptance or cookie endpoint. Anyone who has the tunnel URL can read the feed. To stop, terminate only the collector/tunnel PIDs printed by the launcher.

If live data stops, Pages labels the last data offline. Use "Use snapshots" to switch back to scheduled data.

```sh
python -m unittest test_pricing.py
```


### Optional local StarPets transport

The targeted sales-history worker can use a Chrome-compatible HTTPS client and
one fixed proxy from your local proxy file. Install `requirements.txt`, then create
`.live/starpets-transport.json` containing
`{"proxy_file": "proxies.txt", "proxy_index": 1}` (index is zero-based).
Both configuration and proxy credentials stay ignored by Git. No proxy rotation
or automatic retry storm is used: requests remain spaced, history is cached for
six hours, and item failures retry separately without stopping other checks. Connection failures use a short escalating cooldown; server Retry-After instructions are respected. Remove the local
configuration to use the ordinary direct client. This only changes the Python
history worker, not your browser connection or the GitHub Actions environment.
A successful check is observed sales for enumerated exact-variant product IDs,
not a promise of exhaustive marketplace coverage.


### Catalog checker

Open `/catalog` in Flask, or `catalog.html` on Pages. Search all catalog items,
select a starting variant, compare net dollars per frost, and filter by the
median daily sales over seven complete UTC days. The shortlist contains Bunny
Swirl Neon Ride, Reindeer Neon Ride / Neon Fly Ride, Fallow Deer Fly Ride,
Pupcicle Neon Ride, Arctic Dusk Dragon Fly Ride, Honey Badger, and Christmas
Pudding Pup Fly Ride. Rankings use saved resale estimates; a newly checked
lowest listing is shown separately as an asking price.

Check individual variants or the shortlist to queue paced price/history checks.
Pages needs the live feed running for these buttons; otherwise it provides a
clearly labelled saved catalog. No credentials are included in its export.


Sales checks continue through the queue automatically. A failed item is retried
after 60 seconds, increasing up to one hour if that item repeatedly fails.
Network failures pause globally for 15 seconds initially, increasing up to five
minutes; HTTP 429 starts at 60 seconds and any longer Retry-After wins. Successful
history is retained, and a successful retry clears that item's error. Catalog
searches allow up to 20 pages and detect pagination that makes no progress.
