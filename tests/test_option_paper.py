import pandas as pd
import pytest
from backend.option_paper import depth_fill, select_contract, process


def test_contract_two_strikes_itm():
    rows = [{"EXCH": "NSE", "INSTRUMENT_NAME": "OPTIDX", "TRADING_SYMBOL": f"BANKNIFTY-X-{k}-{kind}",
             "OPTION_TYPE": kind, "EXPIRY_DATE": "2026-10-27", "STRIKE_PRICE": str(k), "LOT_UNITS": "30", "SECURITY_ID": str(k)}
            for kind in ("CE", "PE") for k in range(54000, 55100, 100)]
    now = pd.Timestamp("2026-10-19 10:00", tz="Asia/Kolkata")
    assert select_contract(rows, 54500, "long", now)["strike"] == 54300
    assert select_contract(rows, 54500, "short", now)["strike"] == 54700


def book():
    return {"market_depth": {"depth": [{"buy": {"price": 100, "quantity": 30}, "sell": {"price": 101, "quantity": 30}},
                                       {"buy": {"price": 99, "quantity": 30}, "sell": {"price": 102, "quantity": 30}}]}}


def test_depth_and_quantity():
    assert depth_fill(book(), "X", 60, True) == 101.5
    assert depth_fill(book(), "X", 60, False) == 99.5
    with pytest.raises(ValueError, match="INSUFFICIENT_DEPTH"):
        depth_fill(book(), "X", 90, True)


def test_stale_snapshot_rejected():
    now = pd.Timestamp("2026-10-19 10:00", tz="Asia/Kolkata")
    with pytest.raises(ValueError, match="STALE_RECEIPT"):
        process({}, [], {"received_at": (now-pd.Timedelta(seconds=4)).isoformat()}, [], now)


def test_exit_uses_option_bid_and_retains_original_contract():
    now = pd.Timestamp("2026-10-19 15:15", tz="Asia/Kolkata")
    position = {"side": "long", "entered_at": now.isoformat(), "stop": 90, "target": 120,
                "code": "O", "quantity": 60, "premium": 95}
    state = {"position": position, "trades": [], "future_code": "F"}
    process(state, [], {"received_at": now.isoformat(), "data": {"F": {"live_price": 100}, "O": book()}}, [], now)
    assert state["position"] is None
    assert state["trades"][0]["gross_pnl"] == 270
    assert state["trades"][0]["exit_premium"] == 99.5


def test_temporary_gap_does_not_exit_position():
    now = pd.Timestamp('2026-10-21 10:01', tz='Asia/Kolkata')
    position = {'side': 'long', 'entered_at': (now-pd.Timedelta(minutes=1)).isoformat(),
                'stop': 90, 'target': 120, 'code': 'O', 'quantity': 60, 'premium': 95, 'data_gap': True}
    state = {'position': position, 'trades': [], 'future_code': 'F'}
    process(state, [], {'received_at': now.isoformat(), 'data': {'F': {'live_price': 100}, 'O': book()}}, [], now)
    assert state['position'] is position
    assert not position.get('exit_reason')
    assert not state['trades']


def test_tick_crossing_survives_rebound_and_missing_depth():
    from backend.option_paper import latch_exit
    now = pd.Timestamp('2026-10-21 10:01', tz='Asia/Kolkata')
    position = {'side': 'short', 'entered_at': (now-pd.Timedelta(minutes=1)).isoformat(),
                'stop': 110, 'target': 80, 'code': 'O', 'quantity': 60, 'premium': 95}
    state = {'position': position, 'trades': [], 'future_code': 'F'}
    latch_exit(position, 79, now)
    process(state, [], {'received_at': now.isoformat(), 'data': {'F': {'live_price': 100}}}, [], now)
    assert state['position']['exit_reason'] == 'UNDERLYING_2R_TARGET'
    process(state, [], {'received_at': now.isoformat(), 'data': {'F': {'live_price': 100}, 'O': book()}}, [], now)
    assert state['position'] is None
    assert state['trades'][0]['exit_trigger_price'] == 79


def test_entry_expiry_is_audited():
    from backend.order_block_strategy import STRATEGY
    now = pd.Timestamp('2026-10-21 10:01', tz='Asia/Kolkata')
    setup = {'strategy': STRATEGY, 'timestamp': '2026-10-21T09:55:00+05:30', 'valid_until': '2026-10-21T10:00:00+05:30', 'side': 'short'}
    state = {'position': None, 'trades': [], 'seen': [], 'started_at': '2026-10-21T09:00:00+05:30', 'future_code': 'F'}
    process(state, [setup], {'received_at': now.isoformat(), 'data': {'F': {'live_price': 100}}}, [], now)
    assert state['execution_events'][0]['reason'] == 'ENTRY_EXPIRED'
