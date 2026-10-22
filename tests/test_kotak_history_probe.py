import pytest
from screener.kotak_history_probe import fetch, parse_candles, URL


def test_read_only_request_and_no_redirects():
    calls = []
    class Reply:
        status_code = 403
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return Reply()
    report, frame = fetch('secret', 'nse_cm|1333', '2026-10-08', '2026-10-09', get=get)
    assert calls[0][0] == URL
    assert calls[0][1]['allow_redirects'] is False
    assert calls[0][1]['params']['interval'] == '1min'
    assert frame is None and report['http_status'] == 403
    assert 'secret' not in str(report)


def test_candle_validation():
    payload = {'status': 'success', 'data': {'candles': [['2026-10-09T09:15:00+0530', 100, 102, 99, 101, 10]]}}
    frame = parse_candles(payload)
    assert len(frame) == 1 and str(frame.index.tz) == 'UTC'
    payload['data']['candles'][0][2] = 98
    with pytest.raises(ValueError, match='OHLCV'):
        parse_candles(payload)


def test_oversized_range_never_requests():
    def unexpected(*args, **kwargs):
        raise AssertionError('Request must not occur')
    with pytest.raises(ValueError, match='30'):
        fetch('secret', 'nse_cm|1333', '2026-01-01', '2026-10-09', get=unexpected)
