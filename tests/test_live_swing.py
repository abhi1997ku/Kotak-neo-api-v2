import json
import copy
import pandas as pd
from screener.live_swing import LiveEngine, LiveJournal
from screener.smooth_trailing import _plan
from backend.live_swing_service import exchange_time


def fixture(tmp_path):
    now = pd.Timestamp('2026-10-09 10:15', tz='UTC')
    hours = pd.DataFrame(columns=['Open', 'High', 'Low', 'Close', 'Volume'], index=pd.DatetimeIndex([], tz='UTC'))
    journal = LiveJournal(tmp_path/'live.sqlite3')
    engine = LiveEngine('NIFTY', hours, journal, 'test')
    return engine, journal, now


def arm(engine, now):
    plan = _plan('NIFTY', pd.Series(dict(direction=1, width=40., upper=1000., lower=900.)), now-pd.Timedelta(hours=1), 1)
    engine.pending = plan
    engine.warmup = 30


def test_tick_entry_exit_and_persistent_provenance(tmp_path):
    e, journal, _ = fixture(tmp_path)
    now = pd.Timestamp('2026-10-09 06:00', tz='UTC')
    arm(e, now)
    e.tick(1002, now, now)
    assert e.active and e.active['entry'] == 1002
    stop = e.active['stop']
    e.tick(stop-4, now+pd.Timedelta(seconds=1), now+pd.Timedelta(seconds=1))
    assert e.active is None
    trade = journal.trades()[0]
    assert trade['legs'][0]['price'] == stop-4
    assert trade['provenance'] == 'KOTAK_FORWARD_PAPER' and trade['training_eligible']
    assert e.cooldown == 2


def test_stale_out_of_order_and_off_session_cannot_enter(tmp_path):
    e, _, now = fixture(tmp_path)
    arm(e, now)
    e.tick(1002, now, now+pd.Timedelta(seconds=20))
    assert e.active is None and e.rejected_ticks == 1
    # 10:15 UTC = after the regular session.
    e.tick(1002, now, now)
    assert e.active is None


def test_gap_preserves_trade_and_requires_reconciliation(tmp_path):
    e, journal, _ = fixture(tmp_path)
    now = pd.Timestamp('2026-10-09 06:00', tz='UTC')
    arm(e, now)
    e.tick(1002, now, now)
    e.heartbeat(now+pd.Timedelta(seconds=16))
    e.tick(1010, now+pd.Timedelta(seconds=20), now+pd.Timedelta(seconds=20))
    trade = journal.trades()[0]
    assert trade['status'] == 'OPEN' and not trade['training_eligible']
    assert not trade['legs'] and e.active is not None
    assert e.reconciliation_required and e.state == 'RECONCILIATION_REQUIRED'


def test_minute_close_updates_before_next_tick_not_intraminute(tmp_path):
    e, journal, _ = fixture(tmp_path)
    now = pd.Timestamp('2026-10-09 06:00', tz='UTC')
    arm(e, now)
    e.tick(1002, now, now)
    old = e.active['stop']
    e.ema, e.volatility, e.previous_close = 1060, 5, 1060
    for second in (10, 20, 30, 40, 50):
        stamp = now+pd.Timedelta(seconds=second)
        e.tick(1100, stamp, stamp)
        assert e.active['stop'] == old
    end = now+pd.Timedelta(minutes=1)
    e.heartbeat(end)
    assert e.active['stop'] > old
    stop = e.active['stop']
    e.tick(stop-1, end, end)
    assert e.active is None and journal.trades()[0]['status'] == 'CLOSED'


def test_partial_minute_is_discarded(tmp_path):
    e, _, _ = fixture(tmp_path)
    now = pd.Timestamp('2026-10-09 06:00:30', tz='UTC')
    for second in (0, 10, 20, 30):
        stamp = now+pd.Timedelta(seconds=second)
        e.tick(1000, stamp, stamp)
    assert e.warmup == 0


def test_restart_restores_unfinished_trade_and_indicators(tmp_path):
    e, journal, _ = fixture(tmp_path)
    now = pd.Timestamp('2026-10-09 06:00', tz='UTC')
    arm(e, now)
    e.tick(1002, now, now)
    reopened = LiveJournal(journal.path)
    restored = LiveEngine('NIFTY', e.hourly, reopened)
    assert restored.active['id'] == e.active['id']
    assert restored.active['stop'] == e.active['stop']
    assert restored.warmup == 30
    assert reopened.trades()[0]['status'] == 'OPEN'
    assert not reopened.trades()[0]['training_eligible']


def test_weekend_restart_does_not_reset_position(tmp_path):
    e, journal, _ = fixture(tmp_path)
    close = pd.Timestamp('2026-10-09 15:29:55', tz='Asia/Kolkata')
    arm(e, close)
    e.tick(1002, close, close)
    e.interrupt('TRACKER_STOPPED', close+pd.Timedelta(seconds=1))
    restored = LiveEngine('NIFTY', e.hourly, LiveJournal(journal.path))
    opening = pd.Timestamp('2026-10-12 09:15:01', tz='Asia/Kolkata')
    restored.tick(1010, opening, opening)
    assert restored.active and not restored.reconciliation_required
    assert restored.warmup == 30


def test_missing_weekday_cannot_be_treated_as_overnight(tmp_path):
    e, _, _ = fixture(tmp_path)
    assert not e.continuous(pd.Timestamp('2026-10-08 15:29:55', tz='Asia/Kolkata'),
                            pd.Timestamp('2026-10-12 09:15:01', tz='Asia/Kolkata'))


def test_prior_session_minutes_seed_indicators_without_daily_wait(tmp_path):
    e, journal, _ = fixture(tmp_path)
    days = pd.bdate_range(end='2026-10-09', periods=20, tz='Asia/Kolkata')
    index = pd.DatetimeIndex([d+pd.Timedelta(hours=9+h, minutes=15) for d in days for h in range(7)])
    prices = [20000+i*5 for i in range(len(index))]
    e.hourly = pd.DataFrame(dict(Open=prices, High=[p+10 for p in prices],
                                Low=[p-5 for p in prices], Close=[p+5 for p in prices], Volume=0), index=index)
    mi = pd.date_range('2026-10-09 14:30', periods=60, freq='min', tz='Asia/Kolkata')
    minutes = pd.DataFrame(dict(Open=20700., High=20702., Low=20698., Close=20701.), index=mi)
    now = pd.Timestamp('2026-10-12 08:55', tz='Asia/Kolkata')
    e.seed_context(minutes, now)
    assert e.warmup == 30 and len(e.hourly) == 140
    assert e.ema == 20701 and e.active is None
    assert e.pending is not None  # Prior-session completed hourly setup, prospective only.
    opening = pd.Timestamp('2026-10-12 09:15:01', tz='Asia/Kolkata')
    e.tick(20701., opening, opening)
    assert e.warmup == 30


def test_saved_closed_trade_cannot_be_resurrected(tmp_path):
    e, journal, _ = fixture(tmp_path)
    now = pd.Timestamp('2026-10-09 06:00', tz='UTC')
    arm(e, now)
    e.tick(1002, now, now)
    e.tick(e.active['stop']-1, now+pd.Timedelta(seconds=1), now+pd.Timedelta(seconds=1))
    restored = LiveEngine('NIFTY', e.hourly, LiveJournal(journal.path))
    assert restored.active is None


def test_exchange_timestamp_requires_fresh_known_encoding():
    now = pd.Timestamp('2026-10-09 06:00', tz='UTC')
    assert exchange_time(int(now.timestamp()), now) == now
    assert exchange_time(int(now.timestamp()*1000), now) == now
    assert exchange_time(0, now) is None
    assert exchange_time(int(now.timestamp())-60, now) is None


def test_regular_overnight_gap_checks_stop_at_next_tick(tmp_path):
    e, journal, _ = fixture(tmp_path)
    close = pd.Timestamp('2026-10-08 15:29:55', tz='Asia/Kolkata')
    arm(e, close)
    e.tick(1002, close, close)
    opening = pd.Timestamp('2026-10-09 09:15:01', tz='Asia/Kolkata')
    e.heartbeat(opening)
    assert e.active is not None
    price = e.active['stop']-20
    e.tick(price, opening, opening)
    assert journal.trades()[0]['status'] == 'CLOSED'
    assert journal.trades()[0]['legs'][0]['price'] == price


def test_hourly_setup_uses_only_complete_live_hour(tmp_path, monkeypatch):
    import screener.live_swing as module
    e, _, _ = fixture(tmp_path)
    end = pd.Timestamp('2026-10-09 10:15', tz='Asia/Kolkata')
    start = end-pd.Timedelta(hours=1)
    context = pd.DataFrame([dict(direction=1, width=40., upper=1000., lower=900.)], index=[start])
    monkeypatch.setattr(module, 'features', lambda raw, now: (raw, context))
    monkeypatch.setattr(module, 'add_levels', lambda ctx, raw: ctx)
    e.warmup = 60
    e.minutes = {at: dict(Open=995., High=1000., Low=990., Close=998.)
                 for at in pd.date_range(start, periods=59, freq='min')}
    e.finish_hour(start, 60, end)
    assert e.pending is None
    e.minutes[start+pd.Timedelta(minutes=59)] = dict(Open=995., High=1000., Low=990., Close=998.)
    e.finish_hour(start, 60, end)
    assert e.pending is not None and ':live:' in e.pending['id']
    assert pd.Timestamp(e.pending['valid_after']) == end


def test_late_tick_cannot_use_new_stop_or_reopen_closed_minute(tmp_path):
    e, journal, _ = fixture(tmp_path)
    start = pd.Timestamp('2026-10-09 06:00', tz='UTC')
    arm(e, start)
    e.tick(1002, start, start)
    e.ema, e.volatility, e.previous_close = 1060, 5, 1060
    for second in (10, 20, 30, 40, 50):
        at = start+pd.Timedelta(seconds=second)
        e.tick(1100, at, at)
    end = start+pd.Timedelta(minutes=1)
    e.heartbeat(end)
    assert e.active['stop'] > 1005
    state = e.checkpoint_payload()
    e.tick(1005, end-pd.Timedelta(seconds=1), end+pd.Timedelta(milliseconds=200))
    assert e.active and e.candle is None
    assert e.rejected_ticks == state['rejected_ticks']+1
    assert e.indicator_as_of == state['indicator_as_of']
    assert e.ema == state['ema']
    assert journal.trades()[0]['status'] == 'OPEN'
    # A current tick must still execute the tightened stop.
    e.tick(1005, end+pd.Timedelta(seconds=1), end+pd.Timedelta(seconds=1))
    assert e.active is None


def test_transition_failure_rolls_back_entry_trail_and_exit(tmp_path, monkeypatch):
    import pytest
    e, journal, _ = fixture(tmp_path)
    start = pd.Timestamp('2026-10-09 06:00', tz='UTC')
    arm(e, start)
    e.checkpoint()
    original_checkpoint = journal.checkpoint

    def fail_after_write(symbol, payload):
        original_checkpoint(symbol, payload)
        raise OSError('simulated disk failure after SQL writes')

    def assert_rollback(action):
        before = copy.deepcopy(e.checkpoint_payload())
        records = journal.trades()
        saved = journal.restore('NIFTY')
        with monkeypatch.context() as patch:
            patch.setattr(journal, 'checkpoint', fail_after_write)
            with pytest.raises(OSError):
                action()
        assert e.checkpoint_payload() == before
        assert journal.trades() == records
        assert journal.restore('NIFTY') == saved
        restored = LiveEngine('NIFTY', e.hourly, LiveJournal(journal.path))
        assert restored.checkpoint_payload() == saved

    assert_rollback(lambda: e.tick(1002, start, start))
    e.tick(1002, start, start)
    e.ema, e.volatility, e.previous_close = 1060, 5, 1060
    for second in (10, 20, 30, 40, 50):
        at = start+pd.Timedelta(seconds=second)
        e.tick(1100, at, at)
    end = start+pd.Timedelta(minutes=1)
    assert_rollback(lambda: e.heartbeat(end))
    e.heartbeat(end)
    assert_rollback(lambda: e.tick(e.active['stop']-1, end, end))
    e.tick(e.active['stop']-1, end, end)
    restored = LiveEngine('NIFTY', e.hourly, LiveJournal(journal.path))
    assert restored.active is None and restored.cooldown == 2 and restored.closed == 1
    assert restored.pending is None


def test_legacy_same_trade_mismatch_preserves_new_stop_and_pauses(tmp_path):
    e, journal, _ = fixture(tmp_path)
    start = pd.Timestamp('2026-10-09 06:00', tz='UTC')
    arm(e, start)
    e.tick(1002, start, start)
    newer = journal.trades()[0]
    newer['stop'] += 30
    journal.write('TRADE', start, newer)
    restored = LiveEngine('NIFTY', e.hourly, LiveJournal(journal.path))
    assert restored.active['stop'] == newer['stop']
    assert restored.reconciliation_required and restored.indicator_as_of is None
    assert 'mismatch' in restored.recovery_message


def test_journal_connections_close_without_garbage_collection(tmp_path, monkeypatch):
    import sqlite3
    import pytest
    import screener.live_swing as module
    original = sqlite3.connect
    connections = []
    def connect(*args, **kwargs):
        db = original(*args, **kwargs)
        connections.append(db)
        return db
    monkeypatch.setattr(module.sqlite3, 'connect', connect)
    e, journal, _ = fixture(tmp_path)
    at = pd.Timestamp('2026-10-09 06:00', tz='UTC')
    arm(e, at)
    e.tick(1002, at, at)
    journal.restore('NIFTY')
    journal.trades()
    journal.hours('NIFTY')
    for db in connections:
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            db.execute('SELECT 1')
    journal.path.unlink()  # Windows refuses deletion when SQLite retains handles.
