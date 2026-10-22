import sqlite3
from screener.trade_journal import save_trades, export_records


def record(status='OPEN'):
    return dict(id='NIFTY:signal:1:ema1', symbol='NIFTY', trailing_interval_minutes=1,
                status=status, entry=24000, entry_features={'slope': 10},
                events=[{'at': str(i), 'to': 23900+i} for i in range(65)],
                legs=[] if status == 'OPEN' else [{'price': 24100, 'fraction': 1}])


def test_persistence_deduplication_revision_and_export(tmp_path):
    path = tmp_path/'trades.sqlite3'
    args = dict(path=path, as_of='2026-10-09T09:00:00+00:00', period_start='2026-10-01')
    save_trades([record()], **args)
    save_trades([record()], **args)
    rows = export_records(path)
    assert len(rows) == 1 and len(rows[0]['events']) == 65
    assert not rows[0]['training_eligible']
    assert not export_records(path, closed_only=True)
    args['as_of'] = '2026-10-09T09:01:00+00:00'
    save_trades([record('CLOSED')], **args)
    rows = export_records(path, closed_only=True)
    assert len(rows) == 1 and rows[0]['training_eligible']
    assert rows[0]['entry_features'] == {'slope': 10}
    assert rows[0]['provenance'] == 'SIMULATED_CANDLE_REPLAY'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM observations').fetchone()[0] == 2
    args['as_of'] = '2026-10-09T08:00:00+00:00'
    save_trades([record()], **args)
    assert export_records(path)[0]['status'] == 'CLOSED'


def test_gap_and_interval_records_are_distinct(tmp_path):
    path = tmp_path/'trades.sqlite3'
    one = record('UNKNOWN_DATA_GAP')
    five = dict(record('CLOSED'), id='NIFTY:signal:1:ema5', trailing_interval_minutes=5)
    save_trades([one, five], path=path, as_of='2026-10-09', period_start='2026-10-01')
    assert len(export_records(path)) == 2
    assert len(export_records(path, closed_only=True)) == 1


def test_journal_receives_all_replay_trades_and_full_events(monkeypatch):
    import pandas as pd
    import screener.smooth_trailing as engine
    index = pd.DatetimeIndex(['2026-10-09 10:15'], tz='UTC')
    bars = pd.DataFrame({'Close': [24000.], 'ema': [23990.]}, index=index)
    hours = pd.DataFrame({'direction': [0], 'width': [40.], 'upper': [24050.], 'lower': [23900.]}, index=index-pd.Timedelta(hours=1))
    captured = []
    def replay(*args):
        args[-1].extend([dict(record('CLOSED'), id=str(i)) for i in range(25)])
        return bars, hours, None, None, [], []
    monkeypatch.setattr(engine, 'replay', replay)
    def journal(rows, **kwargs):
        captured.extend(rows)
        return {'saved': len(rows)}
    report = engine.analyze(None, None, 'NIFTY', now=index[0], journal=journal)
    assert report['journal']['saved'] == 25
    assert len(captured[0]['events']) == 65
