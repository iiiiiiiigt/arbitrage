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
    OUT.mkdir(exist_ok=True)
    shutil.copytree(bot.BASE_DIR / 'static', OUT / 'static', dirs_exist_ok=True)
    css = OUT / 'static' / 'desk.css'
    css.write_text(css.read_text(encoding='utf-8').replace("'/static/theme.css'", "'./theme.css'"), encoding='utf-8')
    js = OUT / 'static' / 'desk.js'
    js.write_text(js.read_text(encoding='utf-8').replace("'● Scanning both markets'", "(state.connection_label || 'Snapshot loaded')"), encoding='utf-8')
    grouped = app.build_grouped_pets()
    catalog = []
    for p in bot.AMVGG_PETS:
        info = bot.pet_price_info(p['name'])
        if info.get('reliable') and p['value'] > 0:
            catalog.append(dict(name=p['name'], frost=p['value'], **info))
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
            row['exit_advice'] = opportunities.exit_advice(bot, row, trades)
        else:
            near = bot.get_near_misses([t], give)
            if not near:
                continue
            row = near[0]; row['qualified'] = False
        rows.append(bot.with_demand(row))
    data = {'generated_at': time.time(), 'sources': sources,
            'pets': grouped, 'catalog': catalog, 'trades': rows}
    (OUT / 'snapshot.json').write_text(json.dumps(data, ensure_ascii=True), encoding='utf-8')
    with app.app.test_request_context():
        for template, name in [('index.html', 'index.html'), ('results_clean.html', 'dashboard.html')]:
            html = app.render_template(template, pets=grouped, target='', inventory=[])
            html = html.replace('"/static/', '"./static/').replace('href="/"', 'href="./index.html"')
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
