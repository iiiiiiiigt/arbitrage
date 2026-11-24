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

The `docs/` directory is a static project landing page. GitHub Pages cannot run Flask, authenticated APIs, or the background scanner. The workflow deploys only `docs/`; run the application locally for live scanning. Enable GitHub Actions as the Pages source in repository Settings → Pages after pushing.

## Tests

```sh
python -m unittest test_pricing.py
```
