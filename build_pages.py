"""Build the existing Flask UI as a credential-free static dashboard."""
import json
import hashlib
import re
import shutil
import time
from pathlib import Path
import requests
import app
import bot
import opportunities

OUT = Path(__file__).resolve().parent / 'docs'

def build():
    # Keep the amvgg rating table current. TTL-gated (default 12h) and never
    # fatal: a stale file beats a broken build.
    try:
        import demand_refresh
        demand_refresh.refresh_amvgg()
    except Exception as exc:
        print(f"demand refresh skipped: {type(exc).__name__}: {exc}")

    OUT.mkdir(exist_ok=True)
    shutil.copytree(bot.BASE_DIR / 'static', OUT / 'static', dirs_exist_ok=True)
    css = OUT / 'static' / 'desk.css'
    css.write_text(css.read_text(encoding='utf-8').replace("'/static/theme.css'", "'./theme.css'"), encoding='utf-8')
    js = OUT / 'static' / 'desk.js'
    js.write_text(js.read_text(encoding='utf-8').replace("'● Scanning both markets'", "(state.connection_label || 'Snapshot loaded')"), encoding='utf-8')
    import catalog_checker
    (OUT / 'checker.json').write_text(json.dumps(catalog_checker.snapshot()), encoding='utf-8')
    grouped = app.build_grouped_pets()
    catalog = []
    for p in bot.AMVGG_PETS:
        info = bot.pet_price_info(p['name'])
        if info.get('reliable') and p['value'] > 0:
            # sales_per_day is what the UI reads; the nested per-trade blocks
            # are only used server-side, so they are dropped to keep the
            # published catalog small.
            entry = {k: v for k, v in info.items()
                     if k not in ('demand', 'demand_signal')}
            catalog.append(dict(name=p['name'], frost=p['value'], preferred=opportunities.preferred(bot, p['name']), **entry))
    trades = []; sources = {}
    try:
        r = requests.get('https://amvgg.com/api/trades?limit=20', timeout=20)
        r.raise_for_status()
        trades.extend(r.json()['trades']); sources['AMVGG'] = 'Snapshot fetched'
    except (requests.RequestException, ValueError, KeyError):
        sources['AMVGG'] = 'Unavailable during this build'
    trades.extend(opportunities.elvebredd_trades())
    sources['Elvebredd'] = opportunities.source_status
    for t in trades:
        t['_seen_at'] = time.time()
    rows = []
    for t in trades:
        give, ok, _ = bot._safe_pet_names(t.get('lookingFor', []))
        if not ok or not give:
            continue
        row, reason = bot._evaluate_trade(t, give)
        if row:
            row['qualified'] = True
        else:
            near = bot.get_near_misses([t], give)
            if not near:
                continue
            row = near[0]; row['qualified'] = False
        rows.append(opportunities.assess_demand(bot, row, trades, row['qualified']))
    # Split by change frequency. The catalog is 86% of the old payload but only
    # changes when the scraper is re-run; trades are small and change every
    # build. Sending one bundle meant re-downloading ~2.1 MB to get ~60 KB.
    trades_blob = {'generated_at': time.time(), 'sources': sources, 'trades': rows}
    (OUT / 'trades.json').write_text(json.dumps(trades_blob, ensure_ascii=True), encoding='utf-8')
    (OUT / 'pets.json').write_text(json.dumps(grouped, ensure_ascii=True), encoding='utf-8')
    (OUT / 'catalog.json').write_text(json.dumps(catalog, ensure_ascii=True), encoding='utf-8')
    (OUT / 'snapshot.json').unlink(missing_ok=True)
    with app.app.test_request_context():
        for template, name in [('index.html', 'index.html'), ('results_clean.html', 'dashboard.html'), ('catalog.html', 'catalog.html')]:
            html = app.render_template(template, pets=grouped, target='', inventory=[])
            html = html.replace('"/static/', '"./static/').replace('href="/"', 'href="./index.html"')
            html = html.replace('href="/catalog"', 'href="./catalog.html"')
            if name != 'catalog.html':
                html = html.replace('<head>', '<head><script src="./static/pages-runtime.js"></script>')
            html = html.replace('</nav>', '</nav><p class="muted" id="pages-status" style="margin:8px 0;font-size:11px">Loading snapshot…</p>', 1)
            html = html.replace('Stop scanning</button>', 'Pause view</button>')
            html = re.sub(r'<div class="help-tip">.*?</div>',
                '<div class="help-tip">Trade snapshots refresh through GitHub Actions, scheduled about every five minutes. Jobs may be delayed. Prices come from the saved catalog with an assumed 30% commission. Accept opens the source marketplace; update your inventory after completing an exchange.</div>',
                html, flags=re.S)
            html = html.replace('Scanning...', 'Snapshot loaded').replace('>LIVE</div>', '>SNAPSHOT</div>')
            def version_asset(match):
                path = OUT / match.group(0).removeprefix('./')
                version = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
                return match.group(0) + '?v=' + version
            html = re.sub(r'\./static/[A-Za-z0-9_-]+\.(?:js|css)', version_asset, html)
            (OUT / name).write_text(html, encoding='utf-8')
    print(f'Built Pages: {len(rows)} trade rows, {len(catalog)} priced variants')

if __name__ == '__main__':
    build()
