import pandas as pd
import pytest
from backend.option_paper import depth_fill, select_contract, process


def test_tick_expires_during_loop_processing():
    from backend.option_paper import tick_is_fresh
    now = pd.Timestamp('2026-11-02 13:00', tz='Asia/Kolkata')
    tick = {'received_at': now.isoformat(), 'price': 55000}
    assert tick_is_fresh(tick, now+pd.Timedelta(seconds=2), True)
    assert not tick_is_fresh(tick, now+pd.Timedelta(seconds=4), True)
    assert not tick_is_fresh(tick, now, False)


def test_missing_underlying_has_actionable_error():
    now = pd.Timestamp('2026-11-02 13:00', tz='Asia/Kolkata')
    with pytest.raises(ValueError, match='MISSING_UNDERLYING_QUOTE'):
        process({'future_code': 'F'}, [], {'received_at': now.isoformat(), 'data': {}}, [], now)


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


def test_feed_wait_preserves_terminal_decisions():
    from backend.option_paper import record_feed_wait
    now = pd.Timestamp('2026-10-28 14:00', tz='Asia/Kolkata')
    setup = {'timestamp': '2026-10-28T13:25:00+05:30', 'side': 'short',
             'valid_until': '2026-10-28T15:00:00+05:30'}
    key = setup['timestamp']+':short'
    state = {'seen': [key], 'execution_decisions': {key: {'reason': 'FILLED'}}}
    record_feed_wait(state, [setup], now)
    assert state['execution_decisions'][key]['reason'] == 'FILLED'
    assert not state.get('execution_events')


def test_prefetch_before_touch(monkeypatch):
    import backend.option_paper as engine
    now = pd.Timestamp('2026-10-28 10:00', tz='Asia/Kolkata')
    monkeypatch.setattr(engine, 'eligible', lambda *args: [{'side': 'long'}, {'side': 'short'}])
    monkeypatch.setattr(engine, 'select_contract', lambda rows, spot, side, now: {'code': side})
    assert engine.prepare_options({}, [], {'NIDX_40000003': {'live_price': 54000}}, [], now) == ['long', 'short']
    assert engine.prepare_options({'position': {'code': 'held'}}, [], {}, [], now) == ['held']


def test_slow_quote_rejected(monkeypatch):
    import backend.option_paper as engine
    from types import SimpleNamespace
    times = iter([0, 4])
    monkeypatch.setattr(engine.time, 'monotonic', lambda: next(times))
    with pytest.raises(ValueError, match='SLOW_QUOTE_RESPONSE'):
        engine.fetch_quotes(SimpleNamespace(quotes=lambda codes: {'received_at': 'fresh'}), ['F'])


def test_history_request_does_not_block_quote_worker():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    release = Event()
    with ThreadPoolExecutor(max_workers=1) as history, ThreadPoolExecutor(max_workers=1) as quotes:
        blocked = history.submit(release.wait, 2)
        try:
            assert quotes.submit(lambda: 'quote').result(timeout=1) == 'quote'
            assert not blocked.done()
        finally:
            release.set()
