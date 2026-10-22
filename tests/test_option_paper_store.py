import json
import sqlite3

from backend.option_paper_store import dashboard, load_state, save_state


def test_restart_close_and_idempotent_history(tmp_path):
    database, legacy = tmp_path / "paper.sqlite", tmp_path / "old.json"
    trade = {"entered_at": "2026-10-19T10:00:00+05:30", "code": "O", "side": "long",
             "result": "OPEN_ESTIMATE", "premium": 100, "quantity": 60,
             "entry_snapshot": {"data": {"O": {"ask": 100}}}, "gross_pnl": None}
    state = {"position": trade, "trades": [], "seen": ["signal"]}
    legacy.write_text(json.dumps(state))
    recovered = load_state(database, legacy)
    legacy.unlink()
    assert load_state(database, legacy) == state
    assert dashboard(database)["position"]["premium"] == 100
    assert "entry_snapshot" not in dashboard(database)["position"]
    recovered["position"].update(result="CLOSED_ESTIMATE", gross_pnl=1200,
                                 exit_snapshot={"bid": 120}, exit_reason="UNDERLYING_2R_TARGET")
    recovered["trades"] = [recovered["position"]]
    recovered["position"] = None
    save_state(database, recovered)
    save_state(database, recovered)
    report = dashboard(database)
    assert report["position"] is None
    assert report["total"] == 1
    assert report["summary"]["gross_pnl"] == 1200
    assert report["summary"]["win_rate"] == 100
    assert dashboard(database, offset=1)["trades"] == []
    with sqlite3.connect(database) as db:
        payload = json.loads(db.execute("SELECT payload FROM trades").fetchone()[0])
    assert payload["entry_snapshot"] == trade["entry_snapshot"]
    assert payload["exit_snapshot"] == {"bid": 120}


def test_invalid_write_leaves_committed_state(tmp_path):
    import pytest
    database = tmp_path / "paper.sqlite"
    original = {"position": None, "trades": [], "status": "OK"}
    save_state(database, original)
    with pytest.raises(ValueError):
        save_state(database, {**original, "bad": float("nan")})
    assert load_state(database, tmp_path / "missing.json") == original
