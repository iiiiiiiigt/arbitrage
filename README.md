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

Run `powershell -ExecutionPolicy Bypass -File start_live.ps1` from this folder with the official Cloudflare `cloudflared.exe` installed in `tools/`. The launcher starts a separate read-only collector on port 5051 and a temporary HTTPS tunnel. Open the printed Pages link to connect that browser. It polls the collector every two seconds; upstream rate limits and latency still apply.

The local Flask dashboard does not need to be open or scanning. Inventory and focus are browser-local, independent of the Flask dashboard. Keep the collector and tunnel running and the PC awake. Quick Tunnel addresses change on restart; use the new link. Only public market research is exposed; there is no acceptance or cookie endpoint. Anyone who has the tunnel URL can read the feed. To stop, terminate only the collector/tunnel PIDs printed by the launcher.

If live data stops, Pages labels the last data offline. Use "Use snapshots" to switch back to scheduled data.

```sh
python -m unittest test_pricing.py
```
