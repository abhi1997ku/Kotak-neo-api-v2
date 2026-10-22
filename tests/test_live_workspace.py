from unittest.mock import Mock
from types import SimpleNamespace

from backend.kotak_client import KotakClient
from backend.live_app import app


def test_live_app_excludes_research_and_paper_routes():
    paths = {route.path for route in app.routes}
    assert "/api/orders/place" in paths
    assert "/api/market/option-chain" in paths
    assert "/api/positions/" in paths
    assert not any(any(word in path for word in ("screener", "paper-trading", "index-swing", "live-swing")) for path in paths)


def test_indices_catalog_does_not_download_stock_master(monkeypatch):
    client = KotakClient.__new__(KotakClient)
    master = Mock(side_effect=AssertionError("Live indices must not load the stock master"))
    constituents = Mock(side_effect=AssertionError("Live indices must not fetch constituents"))
    client._load_scrip_master = master
    monkeypatch.setattr("backend.kotak_client.get_nifty50_constituents", constituents)
    catalog = client._watchlist_catalog(indices_only=True)
    assert catalog["stocks"] == []
    assert {row["instrument_token"] for row in catalog["indices"]} == {"Nifty 50", "Nifty Bank", "SENSEX"}
    master.assert_not_called()
    constituents.assert_not_called()


def test_indices_snapshot_only_requests_index_quotes():
    client = KotakClient.__new__(KotakClient)
    client.ensure_logged_in = Mock()
    client.get_quotes = Mock(return_value={"quotes": []})
    snapshot = client.get_watchlist(indices_only=True)
    assert snapshot["stocks"] == []
    requested = [token for call in client.get_quotes.call_args_list for token in call.args[0]]
    assert set(requested) == {"Nifty 50", "Nifty Bank", "SENSEX"}


def test_contract_cache_reuses_metadata_and_expires(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("backend.kotak_client.time.monotonic", lambda: clock[0])
    client = KotakClient.__new__(KotakClient)
    client.client = SimpleNamespace(search_scrip=Mock(return_value=[{"pSymbol": "123"}]))
    client._option_contract_search("nse_fo", "NIFTY", None)
    client._option_contract_search("nse_fo", "NIFTY", None)
    assert client.client.search_scrip.call_count == 1
    client._option_contract_search("nse_fo", "BANKNIFTY", None)
    assert client.client.search_scrip.call_count == 2
    clock[0] = 301.0
    client._option_contract_search("nse_fo", "NIFTY", None)
    assert client.client.search_scrip.call_count == 3


def test_contract_cache_does_not_retain_broker_errors():
    client = KotakClient.__new__(KotakClient)
    client.client = SimpleNamespace(search_scrip=Mock(side_effect=[{"Error": "offline"}, [{"pSymbol": "123"}]]))
    assert client._option_contract_search("nse_fo", "NIFTY", None) == {"Error": "offline"}
    assert client._option_contract_search("nse_fo", "NIFTY", None) == [{"pSymbol": "123"}]
    assert client.client.search_scrip.call_count == 2


def test_cached_contracts_still_fetch_fresh_prices_on_every_chain_open():
    client = KotakClient.__new__(KotakClient)
    client.ensure_logged_in = Mock()
    client.get_quotes = Mock(return_value={"quotes": [{"ltp": 25000}]})
    client.client = SimpleNamespace(
        search_scrip=Mock(return_value=[{
            "pSymbolName": "NIFTY", "pOptionType": "CE", "strike_price": 25000,
            "pTrdSymbol": "NIFTYTESTCE", "pSymbol": "123", "lLotSize": 75,
        }]),
        quotes=Mock(return_value=[]),
    )
    assert client.get_option_chain("NIFTY")["chains"]
    assert client.get_option_chain("NIFTY")["chains"]
    assert client.client.search_scrip.call_count == 1
    assert client.get_quotes.call_count == 2
    assert client.client.quotes.call_count == 2
