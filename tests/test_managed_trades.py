from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from backend.managed_trades import ManagedTradeRequest, PreviewStore


def request(**changes):
    return ManagedTradeRequest(**{
        "request_id": "preview-123", "symbol": "NIFTYTESTCE", "instrument_token": "123",
        "exchange_segment": "nse_fo", "product": "NRML", "side": "BUY", "quantity": 75,
        "entry_type": "MKT", "target_trigger": 110, "target_limit": 109,
        "stop_trigger": 95, "stop_limit": 94, **changes,
    })


@pytest.fixture
def store(tmp_path):
    instance = PreviewStore(tmp_path / "preview.sqlite3")
    yield instance
    instance.close()


def tick(store, price, **changes):
    store.tick("nse_fo", "123", price, **{"received_at": 100, "now": 100, **changes})


def test_target_selects_only_one_exit_even_with_concurrent_ticks(store):
    store.create(request())
    tick(store, 100)
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: tick(store, 110), range(20)))
    tick(store, 90)
    row = store.get("preview-123")
    assert row["state"] == "CLOSED"
    assert row["selected_leg"] == "target"
    assert row["exit_price"] == 110
    assert len(row["events"]) == 2


def test_gap_below_stop_limit_stays_pending_and_cannot_switch_to_target(store):
    store.create(request())
    tick(store, 100)
    tick(store, 90)
    row = store.get("preview-123")
    assert row["state"] == "EXIT_PENDING"
    assert row["selected_leg"] == "stop"
    tick(store, 110)
    row = store.get("preview-123")
    assert row["state"] == "CLOSED"
    assert row["selected_leg"] == "stop"
    assert row["exit_limit"] == 94


def test_limit_entry_waits_and_unrelated_or_stale_prices_do_not_trigger(store):
    store.create(request(entry_type="L", entry_limit=100))
    tick(store, 105)
    tick(store, 99, now=103)
    store.tick("nse_fo", "OTHER", 99, received_at=100, now=100)
    assert store.get("preview-123")["state"] == "WAITING_ENTRY"
    tick(store, 99)
    assert store.get("preview-123")["state"] == "ARMED"


def test_idempotent_create_and_conflicting_payload(store):
    first = store.create(request())
    assert store.create(request()) == first
    with pytest.raises(ValueError, match="different values"):
        store.create(request(quantity=150))
    with pytest.raises(ValueError, match="active preview"):
        store.create(request(request_id="preview-456"))


def test_restart_pauses_pending_exit_and_preserves_winner(tmp_path):
    path = tmp_path / "preview.sqlite3"
    store = PreviewStore(path)
    store.create(request())
    tick(store, 100)
    tick(store, 90)
    store.close()
    restored = PreviewStore(path)
    assert restored.get("preview-123")["state"] == "PAUSED"
    tick(restored, 110)
    assert restored.get("preview-123")["exit_price"] is None
    restored.action("preview-123", "resume")
    assert restored.get("preview-123")["state"] == "EXIT_PENDING"
    tick(restored, 110)
    assert restored.get("preview-123")["selected_leg"] == "stop"
    restored.close()


def test_short_trade_uses_opposite_trigger_directions(store):
    store.create(request(side="SELL", target_trigger=90, target_limit=91, stop_trigger=105, stop_limit=106))
    tick(store, 100)
    tick(store, 90)
    assert store.get("preview-123")["selected_leg"] == "target"


def test_discard_does_not_simulate_exit(store):
    store.create(request())
    tick(store, 100)
    store.action("preview-123", "cancel")
    tick(store, 110)
    assert store.get("preview-123")["state"] == "CANCELLED"
    assert store.get("preview-123")["exit_price"] is None


@pytest.mark.parametrize("changes", [
    {"target_trigger": float("nan")}, {"stop_limit": 96}, {"quantity": 1.5},
    {"stop_trigger": 111}, {"entry_type": "L"}, {"entry_type": "L", "entry_limit": 120},
])
def test_invalid_trade_rejected(changes):
    with pytest.raises(ValidationError):
        request(**changes)


@pytest.mark.parametrize("changes,message", [
    ({"target_trigger": 160, "target_limit": 161}, "Profit limit (161.00) must be at or below Profit trigger (160.00)"),
    ({"stop_limit": 96}, "Stop limit (96.00) must be at or below Stop trigger (95.00)"),
    ({"stop_trigger": 111}, "Stop trigger must be below Profit trigger"),
    ({"entry_type": "L", "entry_limit": 120}, "Entry limit (120.00) must be above Stop trigger"),
    ({"side": "SELL", "target_trigger": 90, "target_limit": 89, "stop_trigger": 105, "stop_limit": 106}, "Profit limit (89.00) must be at or above Profit trigger (90.00)"),
    ({"side": "SELL", "target_trigger": 90, "target_limit": 91, "stop_trigger": 105, "stop_limit": 104}, "Stop limit (104.00) must be at or above Stop trigger (105.00)"),
])
def test_price_errors_identify_field_and_correction(changes, message):
    with pytest.raises(ValidationError) as error:
        request(**changes)
    assert message in str(error.value)


@pytest.mark.parametrize("price", [float("nan"), float("inf"), -1, 0])
def test_invalid_tick_ignored(store, price):
    store.create(request())
    tick(store, price)
    assert store.get("preview-123")["state"] == "WAITING_ENTRY"


def test_backend_stream_drives_preview_without_broker_submission(store, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from backend.managed_preview_service import PreviewService

    class QuotesOnlyClient:
        async def option_chain_tick_stream(self, tokens):
            for price in (100, 110):
                yield {"type": "quote", "exchange_segment": "nse_fo", "instrument_token": "123", "ltp": price}

        def place_order(self, **kwargs):
            pytest.fail("Preview must never submit a broker order")

    client = QuotesOnlyClient()
    monkeypatch.setattr("backend.managed_preview_service.get_session_manager", lambda: SimpleNamespace(get_client=lambda: client))
    store.create(request())
    service = PreviewService(store)
    asyncio.run(service.consume(client, []))
    assert store.get("preview-123")["state"] == "CLOSED"
    assert service.snapshot()["live_execution_enabled"] is False


def test_preview_route_is_removed_from_live_app():
    from backend.live_app import app
    paths = {route.path for route in app.routes}
    assert "/api/managed-trades/live" in paths
    assert "/api/managed-trades/preview" not in paths
