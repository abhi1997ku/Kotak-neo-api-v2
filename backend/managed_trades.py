"""Durable local OCO preview. Deliberately has no broker order submission API.

Prices are live; fills are a simplified LTP simulation, not execution evidence.
All transitions are serialized and committed before being exposed to callers.
"""
import json
import math
import sqlite3
import threading
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ManagedTradeRequest(BaseModel):
    request_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    symbol: str = Field(min_length=1, max_length=100)
    instrument_token: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9]+$")
    exchange_segment: Literal["nse_fo", "bse_fo"]
    product: Literal["MIS", "NRML"]
    side: Literal["BUY", "SELL"]
    quantity: int = Field(gt=0, strict=True)
    entry_type: Literal["MKT", "L"]
    entry_limit: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    target_trigger: float = Field(gt=0, allow_inf_nan=False)
    target_limit: float = Field(gt=0, allow_inf_nan=False)
    stop_trigger: float = Field(gt=0, allow_inf_nan=False)
    stop_limit: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_prices(self):
        if self.entry_type == "L" and self.entry_limit is None:
            raise ValueError("Limit entries require an entry price.")
        if self.side == "BUY":
            valid = self.stop_trigger < self.target_trigger and self.stop_limit <= self.stop_trigger and self.target_limit <= self.target_trigger
            if self.entry_type == "L":
                valid = valid and self.stop_trigger < self.entry_limit < self.target_trigger
        else:
            valid = self.target_trigger < self.stop_trigger and self.stop_limit >= self.stop_trigger and self.target_limit >= self.target_trigger
            if self.entry_type == "L":
                valid = valid and self.target_trigger < self.entry_limit < self.stop_trigger
        if not valid:
            raise ValueError("Check trigger ordering and executable exit limits for the selected side.")
        return self


TERMINAL = {"CLOSED", "CANCELLED"}


class PreviewStore:
    def __init__(self, path: str | Path):
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS managed_preview (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        self.db.commit()
        # A process restart must never silently reactivate persisted triggers.
        with self._lock, self.db:
            for row in self.list():
                if row["state"] not in TERMINAL:
                    row["resume_state"] = row.get("resume_state") or row["state"]
                    row["state"] = "PAUSED"
                    row["note"] = "Backend restarted. Resume preview after checking prices."
                    self._save(row)

    def close(self):
        self.db.close()

    def _save(self, row):
        self.db.execute("INSERT OR REPLACE INTO managed_preview VALUES (?, ?)", (row["request_id"], json.dumps(row, allow_nan=False)))

    def list(self):
        with self._lock:
            return [json.loads(row[0]) for row in self.db.execute("SELECT payload FROM managed_preview ORDER BY rowid DESC")]

    def get(self, trade_id):
        record = self.db.execute("SELECT payload FROM managed_preview WHERE id=?", (trade_id,)).fetchone()
        if not record:
            raise ValueError("Preview trade not found.")
        return json.loads(record[0])

    def create(self, request: ManagedTradeRequest):
        payload = request.model_dump()
        with self._lock, self.db:
            existing = self.db.execute("SELECT payload FROM managed_preview WHERE id=?", (request.request_id,)).fetchone()
            if existing:
                row = json.loads(existing[0])
                if any(row[key] != value for key, value in payload.items()):
                    raise ValueError("This request ID was already used with different values.")
                return row
            for row in self.list():
                if row["state"] not in TERMINAL and all(row[k] == payload[k] for k in ("instrument_token", "exchange_segment", "product")):
                    raise ValueError("An active preview already manages this instrument and product.")
            if sum(row["state"] not in TERMINAL for row in self.list()) >= 10:
                raise ValueError("Maximum 10 active preview trades.")
            row = {**payload, "state": "WAITING_ENTRY", "mode": "preview", "created_at": time.time(),
                   "entry_price": None, "exit_price": None, "selected_leg": None, "exit_limit": None,
                   "filled_quantity": 0, "note": "Waiting for a live tick. No real orders.", "events": []}
            self._save(row)
            return row

    def action(self, trade_id, action):
        with self._lock, self.db:
            row = self.get(trade_id)
            if row["state"] in TERMINAL:
                return row
            if action == "cancel":
                row["state"] = "CANCELLED"
                row["note"] = "Preview discarded; no broker orders were changed."
            elif action == "pause" and row["state"] != "PAUSED":
                row["resume_state"] = row["state"]
                row["state"] = "PAUSED"
                row["note"] = "Preview paused; no triggers evaluated."
            elif action == "resume" and row["state"] == "PAUSED":
                row["state"] = row.pop("resume_state", "WAITING_ENTRY")
                row["note"] = "Waiting for a new live tick."
            self._save(row)
            return row

    def tick(self, segment, token, price, *, received_at, now=None):
        """Accept only freshly received ticks. This is NOT exchange timestamp validation."""
        now = time.time() if now is None else now
        if not math.isfinite(price) or price <= 0 or not 0 <= now - received_at <= 2:
            return
        with self._lock, self.db:
            for row in self.list():
                if row["exchange_segment"] != segment or row["instrument_token"] != str(token) or row["state"] in TERMINAL | {"PAUSED"}:
                    continue
                before = row["state"]
                buy = row["side"] == "BUY"
                if before == "WAITING_ENTRY":
                    crosses = row["entry_type"] == "MKT" or (price <= row["entry_limit"] if buy else price >= row["entry_limit"])
                    if not crosses:
                        continue
                    row.update(state="ARMED", entry_price=price, filled_quantity=row["quantity"], note="Simulated full entry fill at LTP; both local triggers armed.")
                if row["state"] == "ARMED":
                    target = price >= row["target_trigger"] if buy else price <= row["target_trigger"]
                    stop = price <= row["stop_trigger"] if buy else price >= row["stop_trigger"]
                    leg = "target" if target else "stop" if stop else None
                    if leg:
                        row.update(state="EXIT_PENDING", selected_leg=leg, exit_limit=row[f"{leg}_limit"], trigger_at=now,
                                   note=f"{leg.title()} selected; other trigger disabled. Simulated limit exit pending.")
                if row["state"] == "EXIT_PENDING":
                    crosses = price >= row["exit_limit"] if buy else price <= row["exit_limit"]
                    if crosses:
                        row.update(state="CLOSED", exit_price=price, note="Simulated full exit fill at LTP. No broker order was sent.")
                if row["state"] != before:
                    row["events"].append({"at": now, "from": before, "to": row["state"], "price": price, "leg": row["selected_leg"]})
                    self._save(row)
