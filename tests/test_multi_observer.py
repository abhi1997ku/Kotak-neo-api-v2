import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

PATH = Path(__file__).parents[1]/'indicators/research/multi_observer.py'
spec = importlib.util.spec_from_file_location('multi_observer', PATH)
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)
NOW = datetime(2026, 11, 3, 10, 0, 5, tzinfo=timezone(timedelta(hours=5, minutes=30)))


def table(period=10, age=5):
    row = ['0.00']*18
    row[0] = 'test candle'
    row[7] = str(NOW.timestamp()-age)
    row[12] = str(period)
    return [['Date', 'NIFTY call', 'TTC', 'TRENDIKATERR'], [], row]


def test_correct_streams():
    for tf, period in observer.PERIODS.items():
        assert observer.inspect(table(period), tf, NOW)['state'] == 'capturing'


def test_wrong_timeframe_is_not_accepted():
    assert observer.inspect(table(60), '10s', NOW)['state'] == 'wrong_timeframe'


def test_heartbeat_does_not_make_old_candle_fresh():
    assert observer.inspect(table(10, 100), '10s', NOW)['state'] == 'stale_chart'
    assert observer.inspect(table(300, 200), '5m', NOW)['state'] == 'capturing'
    assert observer.inspect(table(300, 340), '5m', NOW)['state'] == 'stale_chart'


def test_missing_or_changed_schema():
    assert observer.inspect([], '1m', NOW)['state'] == 'setup_required'
    rows = table()
    rows[2][7] = 'unexpected'
    assert observer.inspect(rows, '10s', NOW)['state'] == 'schema_error'


def test_previous_day_and_future_candles_are_stale():
    assert observer.inspect(table(10, 86400), '10s', NOW)['state'] == 'stale_chart'
    assert observer.inspect(table(10, -100), '10s', NOW)['state'] == 'stale_chart'
