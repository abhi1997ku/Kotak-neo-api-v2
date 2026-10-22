"""Pure, conservative recovery of a paper position; never submits orders."""
import copy
import math
import pandas as pd
from screener.smooth_trailing import minute_bars, manage_minute
from screener.trailing_swing import book


def session_minutes(start, end):
    times = pd.date_range(start, end, freq='min', inclusive='left')
    local = times.tz_convert('Asia/Kolkata')
    clock = local.hour*60+local.minute
    return times[(local.dayofweek < 5) & (clock >= 555) & (clock < 930)]


def recover(engine, raw, now):
    """Return a proposed state. Any validation failure leaves the engine intact."""
    if not engine.active or not engine.indicator_as_of or engine.last_tick is None:
        raise ValueError('Missing saved indicator boundary; cannot safely reconstruct this legacy position.')
    if any(v is None or not math.isfinite(v) for v in (engine.ema, engine.volatility, engine.previous_close)):
        raise ValueError('Missing saved EMA/volatility state.')
    start = pd.Timestamp(engine.indicator_as_of)
    if now-start > pd.Timedelta(days=7):
        raise ValueError('Gap exceeds the automatic seven-day recovery window.')
    ticks = list(engine.recovery_ticks)
    if not ticks or abs((now-ticks[-1][0]).total_seconds()) > 5:
        raise ValueError('Waiting for fresh Kotak ticks to finish recovery.')
    current = ticks[-1][0].floor('min')
    if current < start:
        raise ValueError('Recovery timestamps precede the saved indicator boundary.')
    prefix = [(at, price) for at, price in ticks if at >= current]
    if (not prefix or (prefix[0][0]-current).total_seconds() > 5
            or any((b[0]-a[0]).total_seconds() > 15 for a, b in zip(prefix, prefix[1:]))):
        raise ValueError('Waiting for a continuously observed live minute boundary.')
    if raw is None or raw.index.has_duplicates:
        raise ValueError('Historical recovery data unavailable or duplicated.')
    history = minute_bars(raw, now)
    # Complete buffered live minutes can bridge Yahoo publication latency.
    groups = {}
    for at, price in ticks:
        groups.setdefault(at.floor('min'), []).append((at, price))
    sources = {}
    for minute, values in groups.items():
        if minute >= current:
            continue
        complete = ((values[0][0]-minute).total_seconds() <= 5
                    and (minute+pd.Timedelta(minutes=1)-values[-1][0]).total_seconds() <= 15
                    and all((b[0]-a[0]).total_seconds() <= 15 for a, b in zip(values, values[1:])))
        if complete:
            prices = [p for _, p in values]
            history.loc[minute, ['Open', 'High', 'Low', 'Close']] = [prices[0], max(prices), min(prices), prices[-1]]
            sources[minute] = 'BUFFERED_KOTAK_TICKS'
    expected = session_minutes(start, current)
    missing = expected.difference(history.index)
    if len(missing):
        raise ValueError(f'Waiting for {len(missing)} missing regular-session minute candles; no interpolation.')
    trade = copy.deepcopy(engine.active)
    ema, volatility, previous = engine.ema, engine.volatility, engine.previous_close
    completed = {}
    exit_precision = None
    # First pre-disconnect minute may contain prices before entry or before the
    # last observed tick. A touched stop there cannot be assigned a safe time.
    for at in expected:
        row = history.loc[at].copy()
        if at < engine.last_tick and trade['status'] == 'OPEN':
            touches = row.Low <= trade['stop'] if trade['direction'] == 1 else row.High >= trade['stop']
            if touches or trade['exit_pending']:
                raise ValueError('Ambiguous stop timing within the disconnect minute; position remains paused.')
            entry_time = pd.Timestamp(trade['entry_at'])
            if at < entry_time < at+pd.Timedelta(minutes=1):
                pre_entry_extreme = row.High > trade['best_price'] if trade['direction'] == 1 else row.Low < trade['best_price']
                if pre_entry_extreme:
                    raise ValueError('Entry-minute extreme may predate entry; position remains paused.')
        width = max(row.High-row.Low, abs(row.High-previous), abs(row.Low-previous))
        ema += (float(row.Close)-ema)*2/22
        volatility += (float(width)-volatility)*2/15
        previous = float(row.Close)
        row['ema'], row['volatility'] = ema, volatility
        if trade['status'] == 'OPEN':
            manage_minute(trade, row, at, 1)
            if trade['status'] == 'CLOSED':
                exit_precision = 'MINUTE_ESTIMATE'
        completed[at] = {c: float(row[c]) for c in ('Open', 'High', 'Low', 'Close')}
    # These ticks really were observed during the pause; retain their timestamp.
    for at, price in prefix:
        if trade['status'] != 'OPEN':
            break
        if trade['exit_pending'] or trade['direction']*(price-trade['stop']) <= 0:
            book(trade, trade['remaining'], price, at, 'RECOVERY_BUFFERED_TICK_STOP')
            exit_precision = 'OBSERVED_BUFFERED_TICK'
        else:
            trade['best_price'] = max(trade['best_price'], price) if trade['direction'] == 1 else min(trade['best_price'], price)
    trade.update(recovered_from_candles=True, recovery_source='Yahoo OHLC plus buffered Kotak ticks',
                 recovery_at=now.isoformat(), exit_time_precision=exit_precision,
                 recovery_start=start.isoformat(), recovery_end=current.isoformat())
    prices = [p for _, p in prefix]
    candle = dict(at=current, Open=prices[0], High=max(prices), Low=min(prices), Close=prices[-1], complete=True)
    return dict(trade=trade, ema=ema, volatility=volatility, previous_close=previous,
                indicator_as_of=current.isoformat(), completed=completed, candle=candle,
                last_tick=prefix[-1][0], last_price=prices[-1], recovered_minutes=len(expected),
                candles=[dict(row, start=at.isoformat(), source=sources.get(at, 'YAHOO_RECOVERY'))
                         for at, row in completed.items()])
