import pytest
from backend.routes.indstocks import index_tick


def test_spot_tick_normalization():
    tick = index_tick({"received_at": "now", "raw": {"mode": "ltp", "instrument": "40000003", "timestamp": 123, "data": {"ltp": 55000}}})
    assert tick["code"] == "NIDX_40000003"
    assert tick["price"] == 55000
    assert not tick["exchange_timestamp_verified"]


@pytest.mark.parametrize("raw", [{}, {"mode": "ltp", "instrument": "48699", "data": {"ltp": 55000}},
    {"mode": "ltp", "instrument": "40000001", "data": {"ltp": float("nan")}},
    {"mode": "ltp", "instrument": "40000001", "data": {"ltp": -1}},
    {"mode": "quote", "instrument": "40000001", "data": {"ltp": 25000}}])
def test_non_index_and_invalid_events_ignored(raw):
    assert index_tick({"raw": raw, "received_at": "now"}) is None
