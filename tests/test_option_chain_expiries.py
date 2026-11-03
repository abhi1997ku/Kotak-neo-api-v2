from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.kotak_client import KotakClient


@pytest.mark.parametrize("selection,expected", [(None, "03NOV2026"), ("next_week", "10NOV2026"), ("next_month", "24NOV2026"), ("17NOV2026", "17NOV2026")])
def test_chain_selects_listed_expiry_without_weekday_guess(monkeypatch, selection, expected):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 29, 10, tzinfo=tz)
    monkeypatch.setattr("backend.kotak_client.datetime", Clock)
    c = KotakClient.__new__(KotakClient)
    c.ensure_logged_in = Mock()
    contracts = [{"pSymbolName": "NIFTY", "pExpiryDate": date, "pOptionType": "CE",
                  "pTrdSymbol": date, "pSymbol": str(i), "strike_price": 25000, "lLotSize": 65}
                 for i, date in enumerate(["27OCT2026", "03NOV2026", "10NOV2026", "17NOV2026", "24NOV2026"])]
    c._option_contract_search = Mock(return_value=contracts)
    c.get_quotes = Mock(return_value={"quotes": [{"ltp": 25000}]})
    c.client = SimpleNamespace(quotes=Mock(return_value=[]))
    result = c.get_option_chain("NIFTY", selection)
    assert result["expiry"] == expected
    assert result["chains"][0]["call_symbol"] == expected
    c._option_contract_search.assert_called_once_with("nse_fo", "NIFTY", "" if selection in {None, "next_week", "next_month"} else selection)
    c._option_contract_search.return_value = contracts[:2]
    assert c.get_option_chain("NIFTY", "next_week")["chains"] == []
    assert c.get_option_chain("NIFTY", "12NOV2026")["chains"] == []
