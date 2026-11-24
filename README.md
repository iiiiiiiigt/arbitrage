# Adopt Me trade research

Local Flask dashboard comparing estimated resale values and observed AMVGG and Elvebredd offers.

## Run locally

```sh
python -m pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000. Keep this local application private: authenticated trade actions use your local session. Cookies and proxy credentials are intentionally excluded from Git.

Catalog prices are saved estimates, not executable quotes. Calculations assume a 30% seller commission. Demand data is market-wide and may be stale. Trade acceptance is not confirmation of an in-game exchange.

## GitHub Pages

The `docs/` directory contains the static dashboard, generated from the same templates by `python build_pages.py`. Browser-local inventory and focus selection work with the bundled catalog and trade snapshots. GitHub Actions rebuilds and publishes approximately every five minutes; scheduled jobs may be delayed. The page displays the snapshot timestamp. The catalog itself remains a saved estimate until updated separately.

GitHub Pages cannot run Flask or call the marketplaces directly because their APIs do not allow cross-origin browser requests. Acceptance opens the marketplace website, and no session cookies are published. Use the local Flask app for faster live polling. Only `docs/` is deployed.

## Tests

```sh
python -m unittest test_pricing.py
```
