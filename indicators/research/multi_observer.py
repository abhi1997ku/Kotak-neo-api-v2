"""Observe rendered INDstocks tables in dedicated Edge tabs; never submit orders."""
import argparse
import asyncio
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
ROOT = Path(__file__).resolve().parent
PERIODS = {'10s': 10, '1m': 60, '5m': 300}
TABLE_JS = "rows => rows.slice(0,42).map(r=>Array.from(r.children).map(c=>c.innerText))"
DASHBOARD = '''<!doctype html><meta charset="utf-8"><title>Indicator observer</title>
<style>body{font:16px system-ui;margin:32px;background:#f5f7fa;color:#172338}table{border-collapse:collapse;width:100%;background:white}td,th{padding:12px;text-align:left;border-bottom:1px solid #ddd}pre{white-space:pre-wrap}.ok{color:green}.bad{color:#b42318}</style>
<h1>Indicator observer</h1><p>Keep Edge and this observer running. Configure each named chart tab on Table View with TTC and TRENDIKATERR. The same contract must be used across its three timeframes.</p>
<p id="phase">Connecting...</p><table><thead><tr><th>Stream</th><th>Status</th><th>Contract</th><th>Latest candle</th><th>Candle age</th><th>Readings</th></tr></thead><tbody id="streams"></tbody></table><pre id="help"></pre>
<script>async function tick(){try{const s=await(await fetch('/status',{cache:'no-store'})).json();document.querySelector('#phase').textContent=s.phase+' | '+s.updatedIst+' | ends '+s.cutoffIst;const b=document.querySelector('#streams');b.replaceChildren();for(const [name,v] of Object.entries(s.streams)){const r=document.createElement('tr');for(const t of [name,v.state,v.symbol||'',v.latestBar||'',v.candleAgeSeconds==null?'':Math.round(v.candleAgeSeconds)+'s',v.samples||0]){const d=document.createElement('td');d.textContent=t;r.append(d)}r.className=v.state==='capturing'?'ok':'bad';b.append(r)}document.querySelector('#help').textContent=s.instructions||''}catch(e){document.querySelector('#phase').textContent='OBSERVER UNREACHABLE: '+e}}tick();setInterval(tick,2000)</script>'''


def inspect(rows, expected, now):
    """Validate actual timeframe and chart freshness independently of receipt time."""
    valid = [r for r in rows if len(r) == 18]
    if not valid:
        return {'state': 'setup_required', 'detail': 'Open chart Table View with both indicators.'}
    try:
        latest = max(valid, key=lambda r: float(r[7].replace(',', '')))
        period = float(latest[12].replace(',', ''))
        epoch = float(latest[7].replace(',', ''))
    except (ValueError, IndexError):
        return {'state': 'schema_error', 'detail': 'Unexpected table values; no signal inference.'}
    symbol = rows[0][1] if rows and len(rows[0]) > 1 else ''
    age = now.timestamp() - epoch
    state = 'capturing'
    if period != PERIODS[expected]:
        state = 'wrong_timeframe'
    elif datetime.fromtimestamp(epoch, IST).date() != now.date():
        state = 'stale_chart'
    elif age > period + 30 or age < -10:
        state = 'stale_chart'
    return {'state': state, 'symbol': symbol, 'actualPeriodSeconds': period,
            'latestBar': latest[0], 'candleAgeSeconds': round(age, 1),
            'latestEpoch': epoch, 'detail': ''}


def atomic_json(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')
    temp.replace(path)


async def observe(args):
    from playwright.async_api import async_playwright
    day = datetime.strptime(args.date, '%Y-%m-%d').replace(tzinfo=IST)
    start = day.replace(hour=9, minute=15)
    end = day.replace(hour=15, minute=35)
    if datetime.now(IST) >= end:
        raise SystemExit('Selected capture date has already ended; choose a future date.')
    folder = ROOT / 'sessions' / args.date
    folder.mkdir(parents=True, exist_ok=True)
    names = [f'{group}_{period}' for group in (['call', 'put'] if args.include_put else ['primary']) for period in args.timeframes]
    status = {'phase': 'setup', 'streams': {n: {'state': 'setup_required', 'samples': 0} for n in names},
              'cutoffIst': end.isoformat(), 'updatedIst': datetime.now(IST).isoformat(),
              'instructions': 'Each chart tab: select contract and assigned timeframe; right-click empty chart > Table view. Keep both indicators added. Sign in only inside Edge. Dashboard refreshes every 2 seconds. Wrong-timeframe data is quarantined, never mixed with valid data. Ctrl+C in the observer console stops capture.'}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *unused): pass
        def do_GET(self):
            with lock:
                body = json.dumps(status, ensure_ascii=False).encode() if self.path == '/status' else DASHBOARD.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8' if self.path == '/status' else 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # A separate profile avoids depending on the chat-shared tab or touching the user's ordinary Edge profile.
    profile = Path.home() / 'AppData' / 'Local' / 'INDstocksIndicatorObserver'
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch_persistent_context(str(profile), channel='msedge', headless=False,
                args=['--disable-background-timer-throttling', '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows'],
                viewport={'width': 1280, 'height': 900})
            pages = {}
            for name in names:
                p = await browser.new_page()
                # Tab title names are deliberately separate from the detected contract/timeframe validation.
                await p.add_init_script(f"document.addEventListener('DOMContentLoaded',()=>{{document.title={json.dumps(name+' | Indicator observer')};}});")
                pages[name] = p
                try:
                    await p.goto('https://www.indstocks.com/app/tv', wait_until='domcontentloaded', timeout=45000)
                except Exception:
                    pass
            dashboard = await browser.new_page()
            await dashboard.goto(f'http://127.0.0.1:{args.port}')
            counters = {name: 0 for name in names}
            last_epoch = {}
            detected_symbols = {}
            pinned_symbols = {}
            next_analysis = time.monotonic() + 300

            async def sample(name, page):
                now = datetime.now(IST)
                period = name.rsplit('_', 1)[1]
                rows = []
                try:
                    if page.is_closed():
                        raise RuntimeError('Chart tab closed; restart observer to reopen it.')
                    frames = [f for f in page.frames if f != page.main_frame]
                    for frame in frames:
                        grid = frame.get_by_role('grid', name='Table view', exact=True)
                        if await grid.count():
                            # Counter the virtual table's tendency to retain a historical scroll anchor.
                            await grid.evaluate('el=>{el.parentElement.scrollTop=0;}')
                            rows = await grid.get_by_role('row').evaluate_all(TABLE_JS)
                            break
                    health = inspect(rows, period, now)
                    await page.evaluate('(title)=>{document.title=title}', name+' | Indicator observer')
                except Exception as error:
                    health = {'state': 'disconnected', 'detail': str(error)[:250]}
                if health.get('actualPeriodSeconds') == PERIODS[period] and health.get('symbol'):
                    group = name.rsplit('_', 1)[0]
                    detected_symbols[name] = health['symbol']
                    if group in pinned_symbols and health['symbol'] != pinned_symbols[group]:
                        health['state'] = 'contract_mismatch'
                        health['detail'] = 'Use the same contract across this group; restart to deliberately change it.'
                recording = start <= now < end
                if recording:
                    counters[name] += 1
                    packet = {'receivedIst': now.isoformat(), 'observedUtc': now.astimezone(timezone.utc).isoformat(),
                              'stream': name, 'expectedPeriodSeconds': PERIODS[period], 'tablePresent': bool(rows),
                              'health': health, 'rows': rows}
                    accepted = health['state'] == 'capturing'
                    path = folder / (name + ('.jsonl' if accepted else '_issues.jsonl'))
                    with path.open('a', encoding='utf-8') as out:
                        out.write(json.dumps(packet, ensure_ascii=False, separators=(',', ':')) + '\n')
                    epoch = health.get('latestEpoch')
                    prev = last_epoch.get(name)
                    if accepted and epoch is not None:
                        if prev and epoch - prev > PERIODS[period]:
                            with (folder/'gaps.jsonl').open('a', encoding='utf-8') as out:
                                out.write(json.dumps({'stream': name, 'receivedIst': now.isoformat(), 'fromEpoch': prev, 'toEpoch': epoch})+'\n')
                        last_epoch[name] = max(epoch, prev or epoch)
                with lock:
                    status['streams'][name] = {**health, 'samples': counters[name], 'lastCheckedIst': now.isoformat()}

            try:
                while datetime.now(IST) < end:
                    tick = time.monotonic()
                    await asyncio.gather(*(sample(n, p) for n, p in pages.items()))
                    with lock:
                        # Pin only after the session starts and all three correctly timed tabs agree.
                        # Setup before the open remains freely editable.
                        for group in {n.rsplit('_', 1)[0] for n in names}:
                            members = [status['streams'][n] for n in names if n.rsplit('_', 1)[0] == group]
                            symbols = {m.get('symbol') for m in members if m.get('symbol')}
                            if len(symbols) == 1 and all(m.get('state') == 'capturing' for m in members) and datetime.now(IST) >= start:
                                pinned_symbols.setdefault(group, next(iter(symbols)))
                            elif len(symbols) > 1:
                                for member in members:
                                    if member.get('state') == 'capturing':
                                        member['state'] = 'contract_mismatch'
                                        member['detail'] = 'Contracts disagree across this group.'
                        status['updatedIst'] = datetime.now(IST).isoformat()
                        status['phase'] = 'observing' if datetime.now(IST) >= start else 'armed_waiting_for_09_15'
                        snapshot = json.loads(json.dumps(status))
                    atomic_json(folder/'status.json', snapshot)
                    if time.monotonic() >= next_analysis:
                        await asyncio.to_thread(summarize, folder)
                        next_analysis = time.monotonic() + 300
                    await asyncio.sleep(max(.1, args.interval - (time.monotonic() - tick)))
            finally:
                with lock:
                    status['phase'] = 'finished' if datetime.now(IST) >= end else 'stopped_early'
                    status['updatedIst'] = datetime.now(IST).isoformat()
                atomic_json(folder/'status.json', status)
                await asyncio.to_thread(summarize, folder)
                await browser.close()
    finally:
        server.shutdown()
        server.server_close()


def summarize(folder):
    from analyze_day import analyze
    for path in folder.glob('*.jsonl'):
        if path.name == 'gaps.jsonl' or path.stem.endswith('_issues'):
            continue
        try:
            analyze(path)
        except Exception as error:
            with (folder/'analysis_errors.log').open('a', encoding='utf-8') as out:
                out.write(str(error)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', default=datetime.now(IST).date().isoformat())
    parser.add_argument('--include-put', action='store_true', help='Open a call and put for each selected timeframe.')
    parser.add_argument('--timeframes', nargs='+', choices=list(PERIODS), default=list(PERIODS))
    parser.add_argument('--interval', type=float, default=2)
    parser.add_argument('--port', type=int, default=18764)
    args = parser.parse_args()
    if args.interval < 1:
        parser.error('Minimum sampling interval is 1 second')
    args.timeframes = list(dict.fromkeys(args.timeframes))
    asyncio.run(observe(args))


if __name__ == '__main__':
    main()
