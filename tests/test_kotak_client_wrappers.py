from types import SimpleNamespace

from backend.kotak_client import KotakClient


class DummyClient:
    def __init__(self):
        self.calls = []

    def positions(self):
        self.calls.append('positions')
        return {'positions': [{'symbol': 'TCS'}]}

    def holdings(self):
        self.calls.append('holdings')
        return {'holdings': [{'symbol': 'INFY'}]}

    def limits(self, **kwargs):
        self.calls.append('limits')
        return {'available': 100}

    def search_scrip(self, **kwargs):
        self.calls.append('search_scrip')
        return {'results': [{'symbol': 'NIFTY 50'}]}

    def order_report(self):
        self.calls.append('order_report')
        return {'orders': [{'order_id': '1'}]}

    def trade_report(self):
        self.calls.append('trade_report')
        return {'trades': [{'trade_id': '1'}]}


def test_backend_wrapper_uses_real_sdk_methods():
    client = KotakClient.__new__(KotakClient)
    client.session = SimpleNamespace()
    client.client = DummyClient()

    assert client.get_positions()['positions'][0]['symbol'] == 'TCS'
    assert client.get_holdings()['holdings'][0]['symbol'] == 'INFY'
    assert client.get_margin_and_funds()['available'] == 100
    assert client.search_instruments('NIFTY 50')['results'][0]['symbol'] == 'NIFTY 50'
    assert client.get_order_book()['orders'][0]['order_id'] == '1'
    assert client.get_trade_book()['trades'][0]['trade_id'] == '1'
