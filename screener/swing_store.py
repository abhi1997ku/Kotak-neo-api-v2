"""Persistent expectancy-based paper portfolio, retaining all previous history."""
from contextlib import closing
from dataclasses import asdict
from datetime import date, datetime
import json
import sqlite3
import threading
from pathlib import Path

from screener.stock_paper import IST, DB_PATH, PaperStore, new_account, event
from screener.swing_data import BENCHMARK, ROOT
from screener.swing_engine import Config, VERSION, open_risk, prepare, process_day, regime, scan_day

CONFIG_PATH = ROOT / "config.json"
_migration_lock = threading.Lock()


def load_config():
    return Config(**json.loads(CONFIG_PATH.read_text(encoding="utf-8"))).validate() if CONFIG_PATH.exists() else Config()


def save_config(values):
    config = Config(**values).validate()
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")
    tmp.replace(CONFIG_PATH)
    return asdict(config)


def research_summary():
    path = ROOT / "results.json"
    if not path.exists():
        return dict(status="NOT_RUN", strategies={}, message="Historical comparison has not completed; no deployment recommendation")
    report = json.loads(path.read_text(encoding="utf-8"))
    return dict(status="COMPLETED", protocol=report["protocol"], universe_source=report["universe_source"],
                strategies={key: {field: value[field] for field in ("config", "out_of_sample", "recommendation", "walk_forward", "cost_stress")} for key, value in report["strategies"].items()})


class SwingStore(PaperStore):
    def __init__(self, path=None):
        super().__init__(path or DB_PATH.with_name("stock_swing_v3.sqlite3"))
        self.migrate_legacy = path is None

    def connect(self):
        if self.migrate_legacy:
            with _migration_lock:
                if not self.path.exists() and DB_PATH.exists():
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    temp = self.path.with_suffix(".migration.sqlite3")
                    with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)) as source:
                        with closing(sqlite3.connect(temp)) as target:
                            source.backup(target)
                    temp.replace(self.path)
        return super().connect()

    def scan(self, frames, members, now, source):
        now = now.astimezone(IST)
        benchmark = frames.get(BENCHMARK, [])
        if not benchmark or (now.date()-date.fromisoformat(benchmark[-1]["date"])).days > 4:
            raise ValueError("Fresh completed Nifty 500 benchmark unavailable; no portfolio changes")
        if benchmark[-1]["date"] > now.date().isoformat() or (benchmark[-1]["date"] == now.date().isoformat() and (now.hour, now.minute) < (16, 10)):
            raise ValueError("Incomplete benchmark candle; wait until after 16:10 IST")
        prepared = prepare(dict(frames=frames, members=members))
        day, cfg = prepared["days"][-1], load_config()
        stamp = now.isoformat()
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM account WHERE id=1").fetchone()
            account = json.loads(row[0]) if row else new_account()
            account["warnings"] = []
            if account.get("engine") != VERSION:
                for order in account["orders"]:
                    if order["status"] == "QUEUED":
                        order.update(status="CANCELLED", outcome_date=day, outcome_reason="Superseded strategy; preserved in history")
                account.update(engine=VERSION, policy={"monthly_goal_pct": None})
                event(account, day, "ACCOUNT", "STRATEGY_CHANGED", "Expectancy-based paper research; existing positions retain their rules")
            if not account["last_session"]:
                account["last_session"] = day
                account["equity_history"].append(dict(date=day, equity=account["cash"], cash=account["cash"], regime=regime(prepared["snapshots"][day][BENCHMARK])))
            elif account["last_session"] < prepared["days"][0] and any(t["status"] == "OPEN" for t in account["trades"]):
                raise ValueError("Open-position replay exceeds available warmed history")
            for session in prepared["days"]:
                process_day(account, prepared["snapshots"][session], session, prepared["returns"], cfg)
                if account["warnings"]:
                    break
            rows = scan_day(account, prepared, day, cfg)
            # Explicit position rows carry current stop and exit intent.
            for t in account["trades"]:
                if t["status"] == "OPEN" or t.get("exit_date") == day:
                    rows.insert(0, dict(symbol=t["symbol"], company=t["symbol"], sector=t["sector"],
                        strategy=t.get("strategy", "legacy"), status="EXIT" if t.get("pending_exit") or t["status"] != "OPEN" else "HOLD",
                        score=0, reason=t.get("exit_reason") or (t.get("pending_exit") or {}).get("reason") or "Trend/stop rules remain valid",
                        close=t["mark"], as_of=t["mark_date"], entry_price=t["entry"], initial_stop=t.get("initial_stop", t["stop"]),
                        trailing_stop=t["stop"], risk_per_share=max(0, t["mark"]-t["stop"]), quantity=t["quantity"],
                        capital_required=t["cost"], holding_sessions=t["sessions"],
                        unrealized_pnl=t["quantity"]*t["mark"]+t["dividends"]-t["cost"] if t["status"] == "OPEN" else None,
                        execution_status="EXIT_EXECUTED" if t["status"] != "OPEN" else "EXIT_SIGNAL_PENDING" if t.get("pending_exit") else "POSITION_OPEN"))
            report = dict(created_at=stamp, bar_date=day, source=source, requested=len(members),
                available=sum(bool(frames.get(m["symbol"])) for m in members), stocks=rows,
                regime=regime(prepared["snapshots"][day][BENCHMARK]), benchmark="Nifty 500",
                status="BUY CANDIDATE" if any(r["status"] == "BUY CANDIDATE" for r in rows) else "NO TRADE",
                config=asdict(cfg), event_data="UNKNOWN — reliable historical event calendar unavailable")
            scan_id = db.execute("INSERT INTO scans(created_at,payload) VALUES(?,?)", (stamp, json.dumps(report, allow_nan=False))).lastrowid
            account.update(last_scan=stamp, latest_scan=dict(report, id=scan_id), enabled=True)
            db.execute("INSERT OR REPLACE INTO account VALUES(1,?)", (json.dumps(account, allow_nan=False),))
            return self.summary(account, now)

    @staticmethod
    def summary(account, now=None):
        cfg = load_config()
        result = PaperStore.summary(account, now)
        result.update(monthly_target=None, config=asdict(cfg),
                      open_portfolio_risk=open_risk(account, cfg, (now or datetime.now(IST)).date().isoformat()),
                      max_open=cfg.max_positions, max_weekly=cfg.max_weekly,
                      policy=dict(id=VERSION, name="Expectancy-based paper research", validation_status="RESEARCH",
                                  entries_enabled=True, message="Paper research: no fixed target, probability promise or monthly quota. Review chronological backtests before deployment."))
        return result
