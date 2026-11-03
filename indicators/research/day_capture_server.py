"""Local sink for DOM-only chart snapshots. Stops at 15:35 IST today.

Run with --port PORT. Browser collector is installed through the shared tab.
This module never connects to a broker or places an order.
"""
import argparse
import json
import secrets
import threading
import time
from analyze_day import analyze
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
ROOT = Path(__file__).resolve().parent
ALLOWED_ORIGINS = {'https://www.indstocks.com', 'https://indstocks.com'}


class Capture:
    def __init__(self):
        now = datetime.now(IST)
        self.end = now.replace(hour=15, minute=35, second=0, microsecond=0)
        if now >= self.end:
            raise SystemExit('Today capture cutoff already passed')
        self.tag = now.strftime('%Y-%m-%d')
        self.raw = ROOT / f'day_{self.tag}.jsonl'
        self.status_path = ROOT / f'day_{self.tag}_status.json'
        self.token = secrets.token_urlsafe(24)
        self.lock = threading.RLock()
        self.started = now.isoformat()
        self.samples = 0
        self.last = None
        self.max_gap = 0
        self.gaps = []
        self.bars = {}
        self.previous = {}
        self.transitions = []
        self.health = {'state': 'waiting_for_browser'}
        self.stop = threading.Event()

    def save_status(self, final=False):
        with self.lock:
            now = datetime.now(IST)
            age = (now - self.last).total_seconds() if self.last else None
            table_missing = self.health.get('tablePresent') is False
            status = {
                'started_ist': self.started, 'cutoff_ist': self.end.isoformat(),
                'updated_ist': now.isoformat(), 'samples_this_run': self.samples,
                'last_received_ist': self.last.isoformat() if self.last else None,
                'seconds_since_last_receive': age,
                'state': 'finished' if final else ('capturing' if age is not None and age < 20 and not table_missing else 'capture_interrupted' if self.last else 'waiting_for_browser'),
                'browser_health': self.health, 'distinct_bars_this_run': len(self.bars),
                'maximum_receive_gap_seconds': self.max_gap,
                'receive_gaps_over_10_seconds': self.gaps,
                'observed_state_changes': self.transitions,
                'raw_file': str(self.raw),
                'limitations': 'DOM polling can miss brief changes. Keep tab open on Table View, computer awake and session logged in. Reload/navigation removes the page collector. Data before start is not live evidence. No automatic chat analysis is scheduled.'
            }
            temp = self.status_path.with_suffix('.tmp')
            temp.write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding='utf-8')
            temp.replace(self.status_path)

    def receive(self, packet):
        if not isinstance(packet, dict) or not isinstance(packet.get('rows'), list):
            raise ValueError('Expected rows array')
        if len(packet['rows']) > 50 or any(not isinstance(r, list) or len(r) > 30 or any(not isinstance(v, str) or len(v) > 500 for v in r) for r in packet['rows']):
            raise ValueError('Unexpected table shape')
        with self.lock:
            now = datetime.now(IST)
            if now >= self.end:
                raise ValueError('Capture cutoff reached')
            if self.last:
                gap = (now - self.last).total_seconds()
                self.max_gap = max(self.max_gap, gap)
                if gap > 10:
                    self.gaps.append({'from': self.last.isoformat(), 'to': now.isoformat(), 'seconds': gap})
            self.last = now
            self.samples += 1
            self.health = {k: packet.get(k) for k in ['observedUtc', 'tablePresent', 'visibility', 'pageUrl']}
            stored = {'receivedIst': now.isoformat(), **packet}
            with self.raw.open('a', encoding='utf-8') as out:
                out.write(json.dumps(stored, ensure_ascii=False, separators=(',', ':')) + '\n')
                out.flush()
            rows = packet['rows']
            context = '|'.join(rows[0]) if rows else 'missing'
            valid = [r for r in rows if len(r) == 18]
            for row in valid:
                self.bars[(context, row[7])] = row
            if valid:
                latest = max(valid, key=lambda r: float(r[7].replace(',', '')))
                state = (latest[7], latest[16], latest[14], latest[15])
                prev = self.previous.get(context)
                if prev and prev[1:] != state[1:]:
                    self.transitions.append({'received_ist': now.isoformat(), 'context': context, 'bar': latest[0], 'previous': prev, 'current': state})
                self.previous[context] = state
            if self.samples % 5 == 0:
                self.save_status()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=18763)
    args = parser.parse_args()
    capture = Capture()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, code, body):
            self.send_response(code)
            origin = self.headers.get('Origin')
            if origin in ALLOWED_ORIGINS:
                self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Vary', 'Origin')
            self.send_header('Access-Control-Allow-Private-Network', 'true')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Capture-Token')
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def do_OPTIONS(self):
            self.respond(204, {})

        def do_GET(self):
            if self.path != '/health':
                return self.respond(404, {})
            self.respond(200, {'ready': True, 'cutoff': capture.end.isoformat()})

        def do_POST(self):
            if self.path != '/capture' or self.headers.get('X-Capture-Token') != capture.token or self.headers.get('Origin') not in ALLOWED_ORIGINS:
                return self.respond(403, {'error': 'Unauthorized collector'})
            size = int(self.headers.get('Content-Length', 0))
            if not 0 < size < 100000:
                return self.respond(413, {})
            try:
                capture.receive(json.loads(self.rfile.read(size)))
                self.respond(200, {'saved': capture.samples})
            except (ValueError, KeyError, TypeError) as error:
                self.respond(400, {'error': str(error)})

    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.daemon_threads = True
    control = {'port': args.port, 'token': capture.token, 'cutoff': capture.end.isoformat()}
    (ROOT/'day_capture_control.json').write_text(json.dumps(control), encoding='utf-8')
    capture.save_status()

    def watchdog():
        next_analysis = time.monotonic()
        while datetime.now(IST) < capture.end:
            capture.save_status()
            if capture.raw.exists() and time.monotonic() >= next_analysis:
                try:
                    analyze(capture.raw)
                except Exception as error:
                    print('Analysis retry:', str(error), flush=True)
                next_analysis = time.monotonic() + 300
            time.sleep(5)
        capture.save_status(final=True)
        if capture.raw.exists():
            analyze(capture.raw)
        server.shutdown()

    threading.Thread(target=watchdog, daemon=True).start()
    try:
        server.serve_forever(poll_interval=1)
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
