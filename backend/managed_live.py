"""Persisted real-order state machine. Broker calls must be serialized by the service.

SUBMITTING is persisted before network I/O. A timeout is UNKNOWN, never a retry.
Prices choose a leg; only broker cumulative filled quantities close a trade.
"""
import hashlib
import json
import math
import sqlite3
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.managed_broker import FINAL, matches

DONE = {"CLOSED", "CANCELLED", "RELEASED"}


def today():
    return datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()


class LiveManager:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS managed_live (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        self.db.commit()
        with self.lock:
            for row in self.list():
                if row["state"] not in DONE:
                    self.save(row, state="RECOVERY", note="Backend restarted. Reconcile and resume explicitly; local triggers are paused.")

    def list(self, account=None):
        with self.lock:
            rows = [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM managed_live ORDER BY rowid DESC")]
            return [r for r in rows if account is None or r["account"] == account]

    def get(self, trade_id):
        with self.lock:
            row = self.db.execute("SELECT payload FROM managed_live WHERE id=?", (trade_id,)).fetchone()
            if row is None:
                raise ValueError("Managed trade not found.")
            return json.loads(row[0])

    def save(self, row, **changes):
        with self.lock, self.db:
            row.update(changes)
            row["updated_at"] = time.time()
            self.db.execute("INSERT OR REPLACE INTO managed_live VALUES (?, ?)", (row["request_id"], json.dumps(row, allow_nan=False)))
        return row

    def active(self, account):
        return [r for r in self.list(account) if r["state"] not in DONE]

    def create(self, request, broker):
        with self.lock:
            payload = request.model_dump()
            existing = next((r for r in self.list() if r["request_id"] == request.request_id), None)
            if existing:
                if existing["account"] != broker.account or existing["request"] != payload:
                    raise ValueError("Request ID already used for another trade.")
                return existing
            if self.active(broker.account):
                raise ValueError("Finish or release the existing managed trade first. One active managed trade is supported.")
            broker.validate(request)
            rows = broker.orders()
            if any(matches(r, payload) and r["status"] not in FINAL for r in rows) or broker.position(payload) != 0:
                raise ValueError("This instrument already has a position or pending order. Managed entries require a flat instrument.")
            digest = hashlib.sha256((broker.account + request.request_id).encode()).hexdigest()[:12]
            row = {**payload, "request": payload, "account": broker.account, "trade_date": today(), "mode": "live",
                   "state": "ENTRY_SUBMITTING", "entry_id": None, "exit_id": None,
                   "entry_tag": "ME" + digest, "exit_tag": "MX" + digest,
                   "entry_filled": 0, "exit_filled": 0, "entry_final": False, "selected_leg": None,
                   "exit_limit": None, "cancel_sent": False, "exit_attempted": False, "entry_price": None, "exit_price": None,
                   "baseline_ids": [r["id"] for r in rows if matches(r, payload)],
                   "note": "Sending real entry to Kotak.", "created_at": time.time()}
            self.save(row)
            try:
                order_id = broker.place(row, "entry", row["quantity"], row["entry_tag"])
            except Exception:
                return self.save(row, state="ENTRY_UNKNOWN", note="Entry acknowledgement uncertain. No retry sent. Check Kotak, then reconcile.")
            return self.save(row, entry_id=order_id, state="WAITING_ENTRY", note="Entry acknowledged; waiting for actual fills.")

    def reconcile(self, broker, *, explicit=False):
        with self.lock:
            for row in self.active(broker.account):
                if row["trade_date"] != today():
                    self.save(row, state="RECOVERY", note="Prior trading day. Review broker positions and release manually; no automatic overnight rearming.")
                    continue
                orders = broker.orders()
                held = row["state"] in {"RECOVERY", "ATTENTION", "ENTRY_UNKNOWN", "EXIT_UNKNOWN"}
                for leg in ("entry", "exit"):
                    matches_tag = [o for o in orders if o["tag"] == row[f"{leg}_tag"]]
                    if row[f"{leg}_id"] is None and len(matches_tag) == 1:
                        row[f"{leg}_id"] = matches_tag[0]["id"]
                    elif len(matches_tag) > 1:
                        self.save(row, state="ATTENTION", note="Multiple orders share the managed tag. Resolve in Kotak; automation paused.")
                        return
                entry = next((o for o in orders if o["id"] == row["entry_id"]), None)
                exit_order = next((o for o in orders if o["id"] == row["exit_id"]), None)
                if not entry:
                    self.save(row, state="ENTRY_UNKNOWN", note="Entry not found in report. Never resubmit blindly; verify Kotak order book.")
                    continue
                for leg, order in (("entry", entry), ("exit", exit_order)):
                    if order is None:
                        continue
                    if not matches(order, row) or (order.get("product") and order["product"] != row["product"]):
                        self.save(row, state="ATTENTION", note="Broker order identity differs from managed trade. Automation paused.")
                        return
                    filled = order.get("filled")
                    if filled is None or filled < row[f"{leg}_filled"] or filled > row["quantity"]:
                        self.save(row, state="ATTENTION", note="Missing or inconsistent filled quantity; automation paused.")
                        return
                    row[f"{leg}_filled"] = filled
                    row[f"{leg}_price"] = order.get("average") or None
                row["entry_final"] = entry["status"] in FINAL
                if row["exit_filled"] > row["entry_filled"]:
                    self.save(row, state="ATTENTION", note="Exit fills exceed entry fills. Check broker position immediately.")
                    continue
                known = set(row["baseline_ids"]) | {row["entry_id"], row["exit_id"]}
                if any(matches(o, row) and o["id"] not in known for o in orders):
                    self.save(row, state="ATTENTION", note="Another order was detected for this instrument. Local automation paused; reconcile external activity.")
                    continue
                if row["exit_id"] and exit_order is None:
                    self.save(row, state="EXIT_UNKNOWN", note="Exit missing from broker report. No replacement will be sent.")
                    continue
                if exit_order:
                    if exit_order["status"] in FINAL and row["exit_filled"] == row["entry_filled"] and row["entry_final"]:
                        if broker.position(row) == 0:
                            self.save(row, state="CLOSED", note="Broker confirmed exit fills and flat position.")
                        else:
                            self.save(row, state="ATTENTION", note="Exit filled but broker position is not flat. Check external activity.")
                    elif exit_order["status"] in FINAL:
                        self.save(row, state="ATTENTION", note="Exit cancelled, rejected or expired with remaining quantity. Review Kotak; no replacement sent.")
                    else:
                        self.save(row, state="EXIT_PENDING", note="Real limit exit pending. Other local trigger disabled; no automatic repricing.")
                    continue
                if row.get("exit_attempted") and not row["exit_id"]:
                    self.save(row, state="EXIT_UNKNOWN", note="Exit submission outcome unknown. Check Kotak; automatic resubmission blocked.")
                    continue
                if row["entry_final"] and row["entry_filled"] == 0:
                    self.save(row, state="CANCELLED", note="Entry ended without fills; no exit needed.")
                    continue
                if held and not explicit:
                    self.save(row)
                    continue
                if held:
                    if broker.position(row) != row["entry_filled"]:
                        self.save(row, state="ATTENTION", note="Position does not match managed fills; cannot resume.")
                        continue
                self.save(row, state="EXIT_SELECTED" if row["selected_leg"] else "ARMED" if row["entry_filled"] > 0 else "WAITING_ENTRY",
                          note="Local triggers armed for confirmed fills." if row["entry_filled"] else "Waiting for entry fills.")
                if row["selected_leg"]:
                    self.send_exit(row, broker)

    def tick(self, broker, price, received_at):
        if not math.isfinite(price) or price <= 0 or not 0 <= time.time() - received_at <= 2:
            return
        with self.lock:
            for row in self.active(broker.account):
                if row["state"] != "ARMED" or row["trade_date"] != today():
                    continue
                leg = "target" if price >= row["target_trigger"] else "stop" if price <= row["stop_trigger"] else None
                if leg:
                    self.save(row, state="EXIT_SELECTED", selected_leg=leg, exit_limit=row[f"{leg}_limit"], trigger_at=time.time(),
                              note=f"{leg.title()} selected. Other trigger disabled; reconciling quantity before exit.")
                    # Refresh fills and identify external activity immediately before sending.
                    self.reconcile(broker)

    def send_exit(self, row, broker):
        if row["exit_id"] or row["state"] != "EXIT_SELECTED":
            return
        if not row["entry_final"]:
            if not row["cancel_sent"]:
                self.save(row, cancel_sent=True, note="Cancelling unfilled entry remainder before exit.")
                try:
                    broker.cancel(row["entry_id"])
                except Exception:
                    self.save(row, state="ATTENTION", note="Entry cancellation uncertain. Reconcile before exit; cancellation will not be repeated automatically.")
            return
        remaining = row["entry_filled"] - row["exit_filled"]
        if remaining <= 0:
            return
        if broker.position(row) != remaining:
            self.save(row, state="ATTENTION", note="Broker position differs from managed quantity. No exit submitted; review manual/external trades.")
            return
        self.save(row, state="EXIT_SUBMITTING", exit_attempted=True, exit_quantity=remaining, note="Submitting selected real limit exit.")
        try:
            order_id = broker.place(row, "exit", remaining, row["exit_tag"])
        except Exception:
            self.save(row, state="EXIT_UNKNOWN", note="Exit acknowledgement uncertain. Other trigger remains disabled; no retry sent.")
            return
        elapsed = round((time.time() - row["trigger_at"]) * 1000, 1) if row.get("trigger_at") else None
        self.save(row, state="EXIT_PENDING", exit_id=order_id, trigger_to_ack_ms=elapsed, note="Exit acknowledged; awaiting actual broker fills.")

    def action(self, trade_id, action, broker):
        with self.lock:
            row = self.get(trade_id)
            if row["account"] != broker.account:
                raise ValueError("This trade belongs to another account.")
            if row["state"] in DONE:
                return row
            if action == "reconcile":
                self.reconcile(broker, explicit=True)
            elif action == "exit":
                self.reconcile(broker)
                row = self.get(trade_id)
                if row["state"] != "ARMED":
                    raise ValueError("Exit now requires an armed trade. Reconcile unresolved orders first.")
                # User requests an immediate attempt at the existing stop limit, never market.
                self.save(row, state="EXIT_SELECTED", selected_leg="manual", exit_limit=row["stop_limit"], trigger_at=time.time(), note="Manual exit requested at configured stop limit.")
                self.send_exit(row, broker)
            elif action == "release":
                orders = broker.orders()
                if any(matches(o, row) and o["status"] not in FINAL for o in orders):
                    raise ValueError("Cancel/resolve pending broker orders before releasing management.")
                if not row["entry_id"] or (row.get("exit_attempted") and not row["exit_id"]):
                    raise ValueError("Unknown submission must be resolved before release. Check broker/support; do not duplicate it.")
                self.save(row, state="RELEASED", note="Local management released by user. Any remaining position must be managed manually.")
            return self.get(trade_id)
