import copy
import time

import pytest

from backend.managed_live import LiveManager
from tests.test_managed_trades import request


class Broker:
    account = "test-account"

    def __init__(self):
        self.book = []
        self.calls = []
        self.net = 0
        self.fail = None

    def validate(self, request):
        pass

    def orders(self):
        return copy.deepcopy(self.book)

    def position(self, trade):
        return self.net

    def place(self, trade, leg, quantity, client_id):
        self.calls.append((leg, quantity, client_id))
        if self.fail == leg:
            raise TimeoutError("lost response")
        oid = str(len(self.book) + 1)
        self.book.append({"id": oid, "tag": client_id, "symbol": trade["symbol"], "token": trade["instrument_token"],
                          "segment": trade["exchange_segment"], "product": trade["product"], "status": "open",
                          "filled": 0, "quantity": quantity, "average": 0})
        return oid

    def cancel(self, order_id):
        self.calls.append(("cancel", order_id))
        # Acknowledgement alone must not advance the order status.

    def fill_entry(self, qty, final=False):
        self.book[0].update(filled=qty, status="complete" if final else "open", average=100)
        self.net = qty


@pytest.fixture
def setup(tmp_path):
    manager = LiveManager(tmp_path / "live.sqlite3")
    broker = Broker()
    yield manager, broker
    manager.db.close()


def trigger(manager, broker, price=110):
    manager.tick(broker, price, time.time())


def test_ack_is_not_fill_and_exit_ack_does_not_close(setup):
    m, b = setup
    m.create(request(), b)
    trigger(m, b)
    assert len(b.calls) == 1
    b.fill_entry(75, True)
    m.reconcile(b)
    trigger(m, b)
    assert m.get("preview-123")["state"] == "EXIT_PENDING"
    assert [c[0] for c in b.calls] == ["entry", "exit"]
    for price in [90, 120, 80]:
        trigger(m, b, price)
    assert len(b.calls) == 2
    b.book[1].update(status="complete", filled=75, average=110)
    b.net = 0
    m.reconcile(b)
    assert m.get("preview-123")["state"] == "CLOSED"


def test_partial_entry_cancel_race_uses_final_filled_quantity(setup):
    m, b = setup
    m.create(request(), b)
    b.fill_entry(25)
    m.reconcile(b)
    trigger(m, b, 90)
    assert b.calls[-1][0] == "cancel"
    m.reconcile(b)
    assert len(b.calls) == 2
    b.fill_entry(50)
    b.book[0]["status"] = "cancelled"
    m.reconcile(b)
    assert b.calls[-1][:2] == ("exit", 50)
    assert m.get("preview-123")["selected_leg"] == "stop"


def test_duplicate_request_does_not_place_again(setup):
    m, b = setup
    first = m.create(request(), b)
    assert m.create(request(), b) == first
    assert len(b.calls) == 1
    with pytest.raises(ValueError):
        m.create(request(quantity=150), b)


def test_unknown_entry_never_retried(setup):
    m, b = setup
    b.fail = "entry"
    m.create(request(), b)
    m.reconcile(b, explicit=True)
    m.create(request(), b)
    assert len(b.calls) == 1
    assert m.get("preview-123")["state"] == "ENTRY_UNKNOWN"


def test_unknown_exit_survives_restart_without_duplicate(tmp_path):
    path = tmp_path / "live.sqlite3"
    m, b = LiveManager(path), Broker()
    m.create(request(), b)
    b.fill_entry(75, True)
    m.reconcile(b)
    b.fail = "exit"
    trigger(m, b)
    m.db.close()
    m = LiveManager(path)
    b.fail = None
    m.reconcile(b, explicit=True)
    trigger(m, b)
    assert m.get("preview-123")["state"] == "EXIT_UNKNOWN"
    assert len(b.calls) == 2
    m.db.close()


def test_recovery_requires_explicit_resume(setup):
    m, b = setup
    row = m.create(request(), b)
    b.fill_entry(75, True)
    m.save(row, state="RECOVERY")
    m.reconcile(b)
    trigger(m, b)
    assert len(b.calls) == 1
    m.reconcile(b, explicit=True)
    trigger(m, b)
    assert len(b.calls) == 2


def test_external_order_pauses_exit(setup):
    m, b = setup
    m.create(request(), b)
    b.fill_entry(75, True)
    m.reconcile(b)
    b.book.append({**b.book[0], "id": "external", "tag": "manual"})
    trigger(m, b)
    assert m.get("preview-123")["state"] == "ATTENTION"
    assert len(b.calls) == 1


def test_position_mismatch_blocks_exit(setup):
    m, b = setup
    m.create(request(), b)
    b.fill_entry(75, True)
    m.reconcile(b)
    b.net = 0
    trigger(m, b)
    assert m.get("preview-123")["state"] == "ATTENTION"
    assert len(b.calls) == 1


def test_partial_exit_rejection_never_rearms_or_replaces(setup):
    m, b = setup
    m.create(request(), b)
    b.fill_entry(75, True)
    m.reconcile(b)
    trigger(m, b)
    b.book[1].update(status="cancelled", filled=25)
    b.net = 50
    m.reconcile(b)
    trigger(m, b, 90)
    assert m.get("preview-123")["state"] == "ATTENTION"
    assert len(b.calls) == 2


def test_stale_tick_and_missing_fill_quantity_do_not_exit(setup):
    m, b = setup
    m.create(request(), b)
    b.fill_entry(75, True)
    m.reconcile(b)
    m.tick(b, 110, time.time() - 10)
    assert len(b.calls) == 1
    b.book[0]["filled"] = None
    m.reconcile(b)
    trigger(m, b)
    assert m.get("preview-123")["state"] == "ATTENTION"
    assert len(b.calls) == 1


def test_prior_day_trade_cannot_rearm(setup):
    m, b = setup
    row = m.create(request(), b)
    b.fill_entry(75, True)
    m.save(row, trade_date="2000-01-01")
    m.reconcile(b, explicit=True)
    trigger(m, b)
    assert len(b.calls) == 1


def test_release_blocked_with_pending_order(setup):
    m, b = setup
    m.create(request(), b)
    with pytest.raises(ValueError, match="pending"):
        m.action("preview-123", "release", b)


def test_manual_terminal_guard_rejects_conflicting_orders(setup, monkeypatch):
    from types import SimpleNamespace
    from backend import managed_live_service as service
    m, b = setup
    row = m.create(request(), b)
    monkeypatch.setattr(service, "_service", SimpleNamespace(manager=m))
    monkeypatch.setattr(service, "ManagedBroker", lambda _: b)
    with pytest.raises(ValueError, match="managed"):
        with service.manual_order_guard(None, symbol=row["symbol"]):
            pytest.fail("Conflicting order allowed")
    with pytest.raises(ValueError, match="managed"):
        with service.manual_order_guard(None, order_id=row["entry_id"]):
            pytest.fail("Managed modification allowed")
    with service.manual_order_guard(None, symbol="OTHER"):
        pass


def test_price_queue_preserves_brief_crossing(setup, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from backend.managed_live_service import LiveService
    m, _ = setup
    async def scenario():
        parked = asyncio.Event()
        async def feed(tokens):
            for price in (100, 110, 99):
                yield {"type": "quote", "exchange_segment": "nse_fo", "instrument_token": "123", "ltp": price}
            parked.set()
            await asyncio.Event().wait()
        c = SimpleNamespace(option_chain_tick_stream=feed)
        monkeypatch.setattr("backend.managed_live_service.get_session_manager", lambda: SimpleNamespace(get_client=lambda: c))
        s = LiveService(m)
        task = asyncio.create_task(s.prices(c, ("nse_fo", "123")))
        await asyncio.wait_for(parked.wait(), 1)
        assert [q["ltp"] for q in s.tick_queue] == [100, 110, 99]
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_route_rejects_missing_login(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from fastapi import HTTPException
    from backend.routes import managed_trades as routes
    from backend.managed_live_service import LiveService
    monkeypatch.setattr("backend.managed_live_service.get_session_manager", lambda: SimpleNamespace(get_client=lambda: None))
    monkeypatch.setattr(routes, "get_live_service", lambda: LiveService(None))
    with pytest.raises(HTTPException) as error:
        asyncio.run(routes.create_managed(request()))
    assert error.value.status_code == 409
