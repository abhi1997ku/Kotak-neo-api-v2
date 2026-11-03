from unittest.mock import Mock

import pytest

from backend.kotak_client import KotakClient, KotakClientError


def client_with(rows):
    client = KotakClient.__new__(KotakClient)
    client.ensure_logged_in = Mock()
    client._load_scrip_master = Mock(return_value=rows)
    client._scrip_master_warnings = {}
    return client


def contract(**overrides):
    return {"pSymbolName": "TCS", "pTrdSymbol": "TCS99DEC3000CE",
            "pSymbol": "123", "pOptionType": "CE", "pExpiryDate": "31DEC2099",
            "lLotSize": "175", **overrides}


def test_option_search_preserves_trade_identity_and_filters_invalid_contracts():
    client = client_with([contract(), contract(pOptionType="PE", pTrdSymbol="TCS99DEC3000PE"),
                          contract(pExpiryDate="01JAN2000"), contract(lLotSize="0"),
                          contract(pOptionType="XX"), contract(pSymbol="")])
    results = client.search_instruments("tcs 3000 ce", "OPTIONS")["results"]
    assert len(results) == 1
    assert results[0]["symbol"] == "TCS99DEC3000CE"
    assert results[0]["instrument_token"] == "123"
    assert results[0]["lot_size"] == 175
    assert results[0]["exchange_segment"] == "nse_fo"
    assert len(client.search_instruments("TCS", "OPTIONS")["results"]) == 2
    assert client.search_instruments("INFY", "OPTIONS")["results"] == []


def test_option_search_does_not_use_stale_master():
    client = client_with([contract()])
    client._scrip_master_warnings = {"nse_fo": "stale"}
    with pytest.raises(KotakClientError, match="Current option contracts"):
        client.search_instruments("TCS", "OPTIONS")
