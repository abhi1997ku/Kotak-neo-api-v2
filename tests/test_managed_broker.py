from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.managed_broker import ManagedBroker, order_row, records


def client(monkeypatch):
    result = SimpleNamespace(config={"ucc": "TEST"}, client=SimpleNamespace())
    monkeypatch.setattr("backend.session_manager.get_session_manager", lambda: SimpleNamespace(get_client=lambda: result))
    return result


@pytest.mark.parametrize("payload", [None, {}, {"stat": "Ok"}, {"stat": "Not_Ok", "errMsg": "offline"}])
def test_bad_report_is_not_empty_position(payload):
    with pytest.raises(ValueError):
        records(payload)


def test_missing_filled_qty_stays_unknown():
    assert order_row({"nOrdNo": "1", "ordSt": "complete"})["filled"] is None


def test_place_uses_exact_persisted_tag_and_real_limit_parameters(monkeypatch):
    c = client(monkeypatch)
    c.client.place_order = Mock(return_value={"stat": "Ok", "nOrdNo": "123"})
    broker = ManagedBroker(c)
    row = {"exchange_segment": "nse_fo", "product": "NRML", "symbol": "NIFTYCE", "instrument_token": "123", "exit_limit": 94}
    assert broker.place(row, "exit", 25, "MXpersistent") == "123"
    kwargs = c.client.place_order.call_args.kwargs
    assert kwargs["tag"] == "MXpersistent"
    assert kwargs["transaction_type"] == "S"
    assert kwargs["order_type"] == "L"
    assert kwargs["price"] == "94"
    assert kwargs["quantity"] == "25"


def test_unknown_ack_not_retried(monkeypatch):
    c = client(monkeypatch)
    c.client.place_order = Mock(return_value={"stat": "Not_Ok", "emsg": "timeout"})
    broker = ManagedBroker(c)
    row = {"exchange_segment": "nse_fo", "product": "NRML", "symbol": "NIFTYCE", "instrument_token": "123", "exit_limit": 94}
    with pytest.raises(ValueError):
        broker.place(row, "exit", 25, "MXpersistent")
    c.client.place_order.assert_called_once()


def test_position_uses_contract_units_and_rejects_ambiguous_rows(monkeypatch):
    c = client(monkeypatch)
    broker = ManagedBroker(c)
    trade = {"symbol": "NIFTYCE", "instrument_token": "123", "exchange_segment": "nse_fo", "product": "NRML"}
    row = {"trdSym": "NIFTYCE", "prod": "NRML", "cfBuyQty": "0", "flBuyQty": "75", "cfSellQty": "0", "flSellQty": "25", "lotSz": "75"}
    c.client.positions = Mock(return_value={"stat": "Ok", "data": [row]})
    assert broker.position(trade) == 50
    c.client.positions.return_value = {"data": [{"trdSym": "NIFTYCE", "prod": "NRML", "qty": 1}]}
    with pytest.raises(ValueError, match="contract units"):
        broker.position(trade)


def test_session_replacement_blocks_submission(monkeypatch):
    c = client(monkeypatch)
    broker = ManagedBroker(c)
    monkeypatch.setattr("backend.session_manager.get_session_manager", lambda: SimpleNamespace(get_client=lambda: object()))
    with pytest.raises(ValueError, match="session changed"):
        broker.orders()
