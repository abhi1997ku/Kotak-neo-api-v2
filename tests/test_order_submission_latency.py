import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from backend.routes import orders
from backend.kotak_client import KotakClientError


def payload():
    return orders.OrderRequest(exchange_segment="nse_fo", product="NRML", order_type="L",
                               transaction_type="BUY", quantity=1, price=10, trading_symbol="TEST")


def test_submission_uses_order_worker_and_reports_timing(monkeypatch):
    worker_names = []
    def broker(**kwargs):
        worker_names.append(threading.current_thread().name)
        return {"order_id": "mock-order", "status": "SUBMITTED"}
    client = SimpleNamespace(place_order=Mock(side_effect=broker))
    monkeypatch.setattr(orders, "get_session_manager", lambda: SimpleNamespace(get_client=lambda: client))
    result = asyncio.run(orders.place_order(payload()))
    assert result["order_id"] == "mock-order"
    assert result["timing"]["queue_ms"] >= 0
    assert result["timing"]["broker_ms"] >= 0
    assert worker_names[0].startswith("broker-order")
    client.place_order.assert_called_once()


def test_broker_error_is_not_retried(monkeypatch):
    client = SimpleNamespace(place_order=Mock(side_effect=KotakClientError("Broker rejected order")))
    monkeypatch.setattr(orders, "get_session_manager", lambda: SimpleNamespace(get_client=lambda: client))
    with pytest.raises(HTTPException) as error:
        asyncio.run(orders.place_order(payload()))
    assert error.value.status_code == 400
    client.place_order.assert_called_once()


def test_amo_stays_blocked_before_broker_call(monkeypatch):
    manager = Mock()
    monkeypatch.setattr(orders, "get_session_manager", manager)
    request = payload()
    request.amo = "YES"
    with pytest.raises(HTTPException) as error:
        asyncio.run(orders.place_order(request))
    assert error.value.status_code == 503
    manager.assert_not_called()
