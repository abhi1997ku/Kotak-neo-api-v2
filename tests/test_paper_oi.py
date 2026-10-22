import pytest

from backend.paper_oi import OIShadow


def sample(shadow, at, price=55000, oi=1000, volume=100, day="2026-10-13", contract="FUT", latency=1):
    return shadow.observe(day, contract, {"ltp": price, "open_interest": oi, "volume": volume}, at, latency)


@pytest.mark.parametrize("price,oi,expected", [
    (55100, 1100, "bullish_participation"), (54900, 1100, "bearish_participation"),
    (55100, 900, "possible_short_covering"), (54900, 900, "possible_long_unwinding"),
    (55100, 1000, "inconclusive"),
])
def test_price_and_oi_context(price, oi, expected):
    shadow = OIShadow()
    sample(shadow, 100)
    sample(shadow, 161, price, oi, 120)
    result = shadow.status(162)
    assert result["state"] == expected
    assert result["affects_trades"] is False
    assert result["exchange_oi_timestamp_verified"] is False


@pytest.mark.parametrize("oi", [None, 0, -1, "", float("nan")])
def test_missing_or_invalid_oi_never_confirms(oi):
    shadow = OIShadow()
    sample(shadow, 100)
    sample(shadow, 161, 55100, oi, 120)
    assert shadow.status(162)["state"] == "unavailable"


def test_stale_slow_or_no_volume_never_confirms():
    shadow = OIShadow()
    sample(shadow, 100)
    sample(shadow, 161, 55100, 1100, 100)
    assert shadow.status(162)["state"] == "inconclusive"
    assert shadow.status(210)["state"] == "unavailable"
    sample(shadow, 220, 55200, 1200, 200, latency=4)
    assert shadow.status(221)["state"] == "unavailable"


def test_contract_and_day_reset_baseline():
    shadow = OIShadow()
    sample(shadow, 100)
    sample(shadow, 161, contract="NEXT")
    assert shadow.status(162)["state"] == "collecting"
    sample(shadow, 230, contract="NEXT", day="2026-10-14")
    assert shadow.status(231)["state"] == "collecting"
    assert shadow.status(231, "2026-10-15")["state"] == "unavailable"


def test_short_window_and_failed_collection_do_not_confirm():
    shadow = OIShadow()
    sample(shadow, 100)
    sample(shadow, 120, 55100, 1100, 120)
    assert shadow.status(121)["state"] == "collecting"
    shadow.error = "Collection unavailable"
    assert shadow.status(121)["state"] == "unavailable"
