import copy
import pandas as pd
from screener.live_swing import LiveEngine, LiveJournal
from screener.smooth_trailing import _plan
from screener.trailing_swing import open_trade


def fixture(tmp_path):
    start = pd.Timestamp('2026-10-09 06:00', tz='UTC')
    hourly = pd.DataFrame(columns=['Open', 'High', 'Low', 'Close', 'Volume'], index=pd.DatetimeIndex([], tz='UTC'))
    journal = LiveJournal(tmp_path/'live.sqlite3')
    e = LiveEngine('NIFTY', hourly, journal)
    plan = _plan('NIFTY', pd.Series(dict(direction=1, width=40., upper=1000., lower=900.)), start-pd.Timedelta(hours=1), 1)
    e.active = open_trade(plan, 1002, start-pd.Timedelta(minutes=5))
    e.warmup = 30
    e.ema, e.volatility, e.previous_close = 1002., 10., 1002.
    e.indicator_as_of = start.isoformat()
    e.last_tick = start+pd.Timedelta(seconds=20)
    e.last_price = 1002
    e.reconciliation_required = True
    e.save_trade(e.last_tick)
    now = start+pd.Timedelta(minutes=3, seconds=2)
    e.recovery_ticks = [(now-pd.Timedelta(seconds=1), 1020.), (now, 1021.)]
    bars = pd.DataFrame(dict(Open=[1002., 1005., 1010.], High=[1006., 1011., 1020.],
                             Low=[1001., 1004., 1009.], Close=[1005., 1010., 1019.]),
                        index=pd.date_range(start, periods=3, freq='min'))
    return e, journal, bars, now


def test_complete_gap_recovers_and_restart_is_idempotent(tmp_path):
    e, journal, bars, now = fixture(tmp_path)
    old = e.active['stop']
    assert e.reconcile(bars, now)
    assert not e.reconciliation_required and e.active['stop'] >= old
    assert e.last_tick == now and e.indicator_as_of == now.floor('min').isoformat()
    assert journal.trades()[0]['provenance'] == 'MIXED_FORWARD_AND_RECOVERED_PAPER'
    assert not journal.trades()[0]['training_eligible']
    restored = LiveEngine('NIFTY', e.hourly, LiveJournal(journal.path))
    assert restored.active['stop'] == e.active['stop']
    assert not restored.reconcile(bars, now)


def test_missing_minute_changes_no_trade_or_indicators(tmp_path):
    e, _, bars, now = fixture(tmp_path)
    before = copy.deepcopy(e.active)
    ema = e.ema
    assert not e.reconcile(bars.drop(bars.index[1]), now)
    assert e.active == before and e.ema == ema and e.reconciliation_required
    assert 'missing' in e.recovery_message


def test_overlap_stop_touch_is_ambiguous(tmp_path):
    e, _, bars, now = fixture(tmp_path)
    bars.iloc[0, bars.columns.get_loc('Low')] = e.active['stop']-1
    assert not e.reconcile(bars, now)
    assert 'Ambiguous' in e.recovery_message


def test_later_gap_stop_exit_is_labelled_estimate(tmp_path):
    e, journal, bars, now = fixture(tmp_path)
    bars.iloc[1] = [900., 910., 890., 905.]
    assert e.reconcile(bars, now)
    assert e.active is None
    trade = journal.trades()[0]
    assert trade['status'] == 'CLOSED'
    assert trade['legs'][0]['price'] == 900.
    assert trade['exit_time_precision'] == 'MINUTE_ESTIMATE'
    assert not trade['training_eligible']
    restored = LiveEngine('NIFTY', e.hourly, LiveJournal(journal.path))
    assert restored.active is None


def test_buffered_minute_bridges_publication_delay(tmp_path):
    e, _, bars, now = fixture(tmp_path)
    start = bars.index[-1]
    e.recovery_ticks = [(start+pd.Timedelta(seconds=i), 1010.+i/10) for i in (1, 10, 20, 30, 40, 50)]+e.recovery_ticks
    assert e.reconcile(bars.iloc[:2], now)


def test_live_prefix_must_be_continuous(tmp_path):
    e, _, bars, now = fixture(tmp_path)
    e.recovery_ticks = [(now+pd.Timedelta(seconds=20), 1020.)]
    assert not e.reconcile(bars, now+pd.Timedelta(seconds=20))
    assert 'boundary' in e.recovery_message


def test_atomic_write_failure_keeps_in_memory_state(tmp_path, monkeypatch):
    import pytest
    e, journal, bars, now = fixture(tmp_path)
    before = copy.deepcopy(e.active)
    def fail(*args):
        raise OSError('disk unavailable')
    monkeypatch.setattr(journal, 'commit_recovery', fail)
    with pytest.raises(OSError):
        e.reconcile(bars, now)
    assert e.active == before and e.reconciliation_required


def test_short_recovery_stop_only_tightens(tmp_path):
    e, _, bars, now = fixture(tmp_path)
    e.active.update(direction=-1, side='SHORT', stop=1074., initial_stop=1074., best_price=1002.)
    bars['Open'] = [1002., 999., 994.]
    bars['High'] = [1003., 1000., 995.]
    bars['Low'] = [998., 993., 984.]
    bars['Close'] = [999., 994., 985.]
    e.recovery_ticks = [(now-pd.Timedelta(seconds=1), 984.), (now, 983.)]
    assert e.reconcile(bars, now)
    assert e.active and e.active['stop'] <= 1074.


def test_sql_transaction_rolls_back_all_recovery_records(tmp_path):
    import pytest
    e, journal, _, now = fixture(tmp_path)
    original = journal.trades()
    original_state = journal.restore('NIFTY')
    invalid_state = {'not_json': {1, 2}}
    changed = dict(original[0], status='CLOSED')
    with pytest.raises(TypeError):
        journal.commit_recovery('NIFTY', invalid_state, changed, {'symbol': 'NIFTY'}, now)
    assert journal.trades() == original
    assert journal.restore('NIFTY') == original_state


def test_service_automatically_fetches_and_applies_without_blocking(monkeypatch):
    import asyncio
    from backend.live_swing_service import LiveSwingService
    async def scenario():
        service = LiveSwingService()
        class Engine:
            reconciliation_required = True
            active = {'id': 'example'}
            recovery_message = ''
            def reconcile(self, data, now):
                assert data == 'history'
                self.reconciliation_required = False
        engine = Engine()
        service.engines = {'NIFTY': engine}
        async def fetch(symbol):
            assert symbol == 'NIFTY'
            return 'history'
        monkeypatch.setattr(service, 'fetch_recovery', fetch)
        now = pd.Timestamp('2026-10-09 06:00', tz='UTC')
        service.service_recovery(now)
        await asyncio.sleep(0)
        service.service_recovery(now)
        assert not engine.reconciliation_required
        assert not service.recovery_jobs
    asyncio.run(scenario())
