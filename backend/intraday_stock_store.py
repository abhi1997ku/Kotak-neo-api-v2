"""Local, durable paper research. Reads verified cached candles, never a broker."""
import json
import sqlite3
from pathlib import Path
import pandas as pd
from backend.indstocks_monitor import completed_bars
from screener.intraday_stocks import rank, entry_signals

ROOT = Path(__file__).resolve().parent / "data" / "intraday_stocks"


def dashboard(root=ROOT):
    root.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(root/"journal.sqlite") as db:
        db.execute("CREATE TABLE IF NOT EXISTS snapshots(day TEXT, slot TEXT, payload TEXT, PRIMARY KEY(day,slot))")
        db.execute("CREATE TABLE IF NOT EXISTS signals(symbol TEXT, timestamp TEXT, side TEXT, payload TEXT, PRIMARY KEY(symbol,timestamp,side))")
        history = [json.loads(r[0]) for r in db.execute("SELECT payload FROM snapshots ORDER BY day DESC, slot DESC LIMIT 60")]
        signals = [json.loads(r[0]) for r in db.execute("SELECT payload FROM signals ORDER BY timestamp DESC LIMIT 100")]
    reconstruction_path = root/"today_reconstruction_v2.json"
    reconstruction = json.loads(reconstruction_path.read_text()) if reconstruction_path.exists() else None
    account_path, worker_path = root/'paper_account.json', root/'worker_status.json'
    account = json.loads(account_path.read_text()) if account_path.exists() else None
    worker = json.loads(worker_path.read_text()) if worker_path.exists() else dict(state='STOPPED')
    if worker.get('checked_at') and pd.Timestamp.now(tz='Asia/Kolkata')-pd.Timestamp(worker['checked_at']) > pd.Timedelta(minutes=5):
        worker = dict(worker, state='STALE')
    return dict(mode="paper research", orders_enabled=False, fills_enabled=worker.get('state') == 'RUNNING',
                state="Ready to evaluate cached data" if (root/"candles.json").exists() else "DATA REQUIRED",
                message="Paper execution requires a timely shortlist and a subsequently observed trigger. Historical reconstructions never create forward fills.",
                snapshots=history, signals=signals, reconstruction=reconstruction, account=account, worker=worker)


def evaluate(root=ROOT, now=None):
    now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="Asia/Kolkata")
    now = now.tz_convert("Asia/Kolkata")
    base = dashboard(root)
    source = root/"candles.json"
    if not source.exists():
        return base
    raw = json.loads(source.read_text())
    if raw.get("source") != "INDstocks" or not raw.get("universe_as_of"):
        raise ValueError("Verified INDstocks source and universe date required")
    if pd.Timestamp(raw["universe_as_of"]).date() != now.date():
        raise ValueError("Universe mapping is not verified for today")
    benchmark, _ = completed_bars(raw["benchmark"]["candles"], now)
    frames = {symbol: completed_bars(payload["candles"], now)[0] for symbol, payload in raw["stocks"].items()}
    if not frames:
        raise ValueError("Empty stock universe")
    clock = now.strftime("%H:%M")
    if now.weekday() >= 5 or not "09:30" <= clock <= "15:30":
        return dict(base, state="OUTSIDE SESSION")
    at = now.floor("5min")
    if now-benchmark.index[-1] > pd.Timedelta(minutes=2):
        raise ValueError("Stale benchmark; scanner paused")
    saved = []
    with sqlite3.connect(root/"journal.sqlite") as db:
        for slot in ("09:45", "11:00"):
            cutoff = pd.Timestamp(f"{now.date()} {slot}", tz="Asia/Kolkata")
            existing = db.execute("SELECT payload FROM snapshots WHERE day=? AND slot=?", (str(now.date()), slot)).fetchone()
            if existing:
                saved.append(json.loads(existing[0]))
            elif cutoff <= now:
                rows, excluded = rank(frames, benchmark, cutoff)
                record = dict(day=str(now.date()), slot=slot, observed_at=now.isoformat(),
                              historical_reconstruction=now-cutoff > pd.Timedelta(minutes=2),
                              rows=rows, excluded=excluded, universe_count=len(frames),
                              status="WATCH" if rows else "NO TRADE")
                db.execute("INSERT OR IGNORE INTO snapshots VALUES (?,?,?)", (str(now.date()), slot, json.dumps(record)))
                saved.append(record)
    rows, excluded = rank(frames, benchmark, at)
    # A late start may reconstruct rankings but never fabricate timely trade signals.
    timely = [s for s in saved if not s["historical_reconstruction"]]
    signals = entry_signals(frames, benchmark, timely[-1]["rows"], now) if timely else []
    with sqlite3.connect(root/"journal.sqlite") as db:
        for signal in signals:
            signal["observed_at"] = now.isoformat()
            signal["status"] = "EXPIRED" if pd.Timestamp(signal["valid_until"]) <= now else "ENTRY SIGNAL"
            db.execute("INSERT OR IGNORE INTO signals VALUES (?,?,?,?)", (signal["symbol"], signal["timestamp"], signal["side"], json.dumps(signal)))
    return dict(dashboard(root), state="WATCH" if rows else "NO TRADE", provisional=rows,
                excluded=excluded, as_of=at.isoformat(), universe_count=len(frames))
