"""Data-only boundaries and provider failure handling without network access."""
from unittest.mock import Mock

import pytest
import requests

from backend.indstocks_data import INDstocksData, INDstocksError


def test_observed_double_encoded_stream():
    import json
    from backend.indstocks_data import decode_stream_event
    event = {"mode": "quote", "instrument": "48699", "data": {"close": 55930}}
    assert decode_stream_event(json.dumps(json.dumps(event))) == event
    assert decode_stream_event(json.dumps(event)) == event


def test_observed_nested_depth_and_invalid_spread():
    from backend.indstocks_data import best_bid_ask
    levels = [{"buy": {"price": "1,234.50"}, "sell": {"price": "1,235"}}]
    quote = {"market_depth": {"NFO_123": {"depth": levels}}}
    result = best_bid_ask("NFO_123", quote)
    assert result["bid"] == 1234.5 and result["ask"] == 1235
    assert result["timestamp_verified"] is False
    levels[0]["buy"]["price"] = "1,240"
    assert not best_bid_ask("NFO_123", quote)["depth_valid"]
    assert not best_bid_ask("NFO_123", {})["depth_valid"]


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setenv("INDSTOCKS_ACCESS_TOKEN", "secret-test-token")
    response = Mock(status_code=200)
    response.json.return_value = {"status": "success", "data": {"NFO_123": {"live_price": 100}}}
    get = Mock(return_value=response)
    monkeypatch.setattr("backend.indstocks_data.requests.get", get)
    return INDstocksData(), get, response


def test_quotes_are_read_only_and_do_not_invent_exchange_time(transport):
    client, get, _ = transport
    result = client.quotes(["NFO_123", "NFO_456"])
    assert result["timestamp_verified"] is False
    assert result["missing_codes"] == ["NFO_456"]
    assert get.call_args.args == ("https://api.indstocks.com/market/quotes/full",)
    assert get.call_args.kwargs["allow_redirects"] is False
    assert get.call_args.kwargs["headers"] == {"Authorization": "secret-test-token"}


def test_order_paths_blocked_before_network(transport):
    client, get, _ = transport
    with pytest.raises(ValueError):
        client._get("/order")
    get.assert_not_called()


@pytest.mark.parametrize("status", [302, 401, 403, 429, 500])
def test_http_errors_redacted(transport, status):
    client, _, response = transport
    response.status_code = status
    with pytest.raises(INDstocksError) as error:
        client.check_connection()
    assert "secret-test-token" not in str(error.value)


def test_network_error_redacted(transport):
    client, get, _ = transport
    get.side_effect = requests.ConnectionError("secret-test-token")
    with pytest.raises(INDstocksError, match="network"):
        client.check_connection()


def test_missing_token_no_network(transport, monkeypatch):
    client, get, _ = transport
    monkeypatch.delenv("INDSTOCKS_ACCESS_TOKEN")
    with pytest.raises(INDstocksError, match="Set INDSTOCKS"):
        client.check_connection()
    get.assert_not_called()


def test_profile_not_exposed(transport):
    client, _, response = transport
    response.json.return_value = {"status": "success", "data": {"email": "private", "is_nse_fno_onboarded": True}}
    result = client.check_connection()
    assert "private" not in str(result)
    assert result["orders_enabled"] is False
    assert result["market_data_verified"] is False


def test_expired_history_keeps_contract_and_timestamp_units(transport):
    client, get, response = transport
    response.json.return_value = {"success": True, "data": {}}
    result = client.history(["BANKNIFTY26OCT2755000CE"], 1000, 61000, expired=True)
    assert get.call_args.kwargs["params"]["trading-symbols"] == "BANKNIFTY26OCT2755000CE"
    assert get.call_args.kwargs["params"]["start_time"] == 1000
    assert result["timestamp_label"] == "candle_open"
    assert result["missing_codes"] == ["BANKNIFTY26OCT2755000CE"]


def test_oversized_history_rejected_before_network(transport):
    client, get, _ = transport
    with pytest.raises(ValueError):
        client.history(["NFO_123"], 0, 8 * 86400000)
    get.assert_not_called()


@pytest.mark.parametrize("payload", [{}, [], {"success": False, "data": {}}, {"status": "success", "data": None}])
def test_invalid_payload_fails_closed(transport, payload):
    client, _, response = transport
    response.json.return_value = payload
    with pytest.raises(INDstocksError):
        client.check_connection()
