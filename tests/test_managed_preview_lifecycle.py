"""Fault injection with fake feeds only; no network or real broker requests."""
import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.managed_preview_service import PreviewService
from backend.managed_trades import PreviewStore
from tests.test_managed_trades import request, tick


@pytest.fixture
def store(tmp_path):
    result = PreviewStore(tmp_path / "preview.sqlite3")
    yield result
    result.close()


def quote(price):
    return {"type": "quote", "exchange_segment": "nse_fo", "instrument_token": "123", "ltp": price}


def bind(monkeypatch, client):
    manager = SimpleNamespace(get_client=lambda: client)
    monkeypatch.setattr("backend.managed_preview_service.get_session_manager", lambda: manager)
    return manager


def test_malformed_price_does_not_drop_stream(store, monkeypatch):
    store.create(request())
    async def feed(tokens):
        for value in ("bad", None, float("nan"), 100, 110):
            yield quote(value)
    client = SimpleNamespace(option_chain_tick_stream=feed)
    bind(monkeypatch, client)
    service = PreviewService(store)
    asyncio.run(service.consume(client, []))
    assert store.get("preview-123")["state"] == "CLOSED"
    assert service.feed_state == "disconnected"


def test_session_change_discards_old_session_tick(store, monkeypatch):
    store.create(request())
    async def feed(tokens):
        manager.get_client = lambda: object()
        yield quote(100)
    client = SimpleNamespace(option_chain_tick_stream=feed)
    manager = bind(monkeypatch, client)
    asyncio.run(PreviewService(store).consume(client, []))
    assert store.get("preview-123")["state"] == "WAITING_ENTRY"


def test_disconnect_preserves_pending_exit_without_rearming_other_leg(store, monkeypatch):
    store.create(request())
    tick(store, 100)
    tick(store, 90)
    async def feed(tokens):
        raise ConnectionError("fake outage")
        yield
    client = SimpleNamespace(option_chain_tick_stream=feed)
    bind(monkeypatch, client)
    service = PreviewService(store)
    asyncio.run(service.consume(client, []))
    assert service.feed_state == "disconnected"
    assert store.get("preview-123")["state"] == "EXIT_PENDING"
    assert store.get("preview-123")["selected_leg"] == "stop"


def test_no_feed_when_no_active_previews(store, monkeypatch):
    feed = Mock(side_effect=AssertionError("Idle service must not subscribe"))
    bind(monkeypatch, SimpleNamespace(option_chain_tick_stream=feed))
    async def scenario():
        service = PreviewService(store)
        task = asyncio.create_task(service.run())
        await asyncio.sleep(0.1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert service.feed_state == "stopped"
    asyncio.run(scenario())
    feed.assert_not_called()


def test_shutdown_closes_active_feed(store, monkeypatch):
    store.create(request())
    closed = []
    async def scenario():
        started = asyncio.Event()
        async def feed(tokens):
            try:
                started.set()
                await asyncio.Event().wait()
                yield
            finally:
                closed.append(True)
        bind(monkeypatch, SimpleNamespace(option_chain_tick_stream=feed))
        service = PreviewService(store)
        task = asyncio.create_task(service.run())
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())
    assert closed == [True]


def test_slow_storage_does_not_block_live_event_loop(store, monkeypatch):
    original = store.list
    entered, release = threading.Event(), threading.Event()
    def slow_list():
        entered.set()
        assert release.wait(2), "Storage read blocked the event loop"
        return original()
    monkeypatch.setattr(store, "list", slow_list)
    bind(monkeypatch, None)
    async def scenario():
        task = asyncio.create_task(PreviewService(store).run())
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(0.005)
            assert entered.is_set()
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_paused_trade_ignores_target_then_resumes_only_on_new_tick(store):
    store.create(request())
    tick(store, 100)
    store.action("preview-123", "pause")
    tick(store, 110)
    assert store.get("preview-123")["selected_leg"] is None
    store.action("preview-123", "resume")
    assert store.get("preview-123")["state"] == "ARMED"
    tick(store, 110)
    assert store.get("preview-123")["state"] == "CLOSED"
