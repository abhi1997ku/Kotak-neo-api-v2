"""Five-minute, confirmed order-block midpoint-retest paper strategy."""
from dataclasses import dataclass
import pandas as pd

STRATEGY = 'order_block_5m_midpoint_2r_v1'


@dataclass(frozen=True)
class OrderBlockParams:
    periods: int = 5
    threshold: float = 0.0
    usewicks: bool = False
    tick: float = 0.05
    target_r: float = 2.0
    entry_start: str = '09:30'
    entry_end: str = '15:00'
    force_close: str = '15:15'


PARAMS = OrderBlockParams()


def signals(five: pd.DataFrame, now: pd.Timestamp) -> list[dict]:
    """Detect at the close of the fifth confirming candle, never at zone origin."""
    from screener.pine_ob_research import detect
    closed = five[five.index + pd.Timedelta(minutes=5) <= now]
    if closed.empty:
        return []
    # Pine evaluates offsets on the next bar. The placeholder supplies only its
    # timestamp; detect never reads its prices for that evaluation.
    next_at = closed.index[-1] + pd.Timedelta(minutes=5)
    extended = pd.concat([closed, pd.DataFrame([closed.iloc[-1]], index=[next_at])])
    zones = detect(extended, PARAMS.periods, PARAMS.threshold, PARAMS.usewicks)
    result = []
    for z in zones:
        at = pd.Timestamp(z['detected_at'])
        origin = pd.Timestamp(z['origin_at'])
        if at > now or at.date() != now.date() or origin.date() != at.date():
            continue
        if at.strftime('%H:%M') >= PARAMS.entry_end:
            continue
        # Reject detection spanning absent bars or an overnight gap.
        expected = pd.date_range(origin, periods=PARAMS.periods+1, freq='5min')
        if not expected.isin(closed.index).all():
            continue
        sign = 1 if z['side'] == 'long' else -1
        stop = z['low']-PARAMS.tick if sign == 1 else z['high']+PARAMS.tick
        risk = sign*(z['midpoint']-stop)
        result.append({**z, 'timestamp':at.isoformat(), 'accepted':True,
                       'entry':z['midpoint'], 'stop':stop, 'risk':risk,
                       'target':z['midpoint']+sign*PARAMS.target_r*risk,
                       'valid_until':at.normalize().replace(hour=15,minute=0).isoformat(),
                       'strategy':STRATEGY, 'reason_codes':['CONFIRMED_ORDER_BLOCK','MIDPOINT_RETEST','TARGET_2R']})
    return result


def eligible(state: dict, setups: list[dict], now: pd.Timestamp, record) -> list[dict]:
    """Latest zone per side; retire missed, superseded and occupied setups permanently."""
    latest = {}
    for s in sorted(setups, key=lambda x:x['timestamp']):
        if s.get('strategy') != STRATEGY or pd.Timestamp(s['timestamp']) > now:
            continue
        latest[s['side']] = s
    candidates = []
    for s in setups:
        key = s['timestamp']+':'+s['side']
        if key in state['seen']:
            continue
        at = pd.Timestamp(s['timestamp'])
        if at > now:
            continue
        reason = None
        if s.get('strategy') != STRATEGY:
            reason = 'OLD_STRATEGY_IGNORED'
        elif at < pd.Timestamp(state['started_at']):
            reason = 'BEFORE_START_OR_RECOVERY'
        elif now >= pd.Timestamp(s['valid_until']):
            reason = 'ENTRY_EXPIRED'
        elif latest.get(s['side']) is not s:
            reason = 'SUPERSEDED_ZONE'
        elif state.get('position'):
            reason = 'POSITION_ALREADY_OPEN'
        elif any(pd.Timestamp(t['entered_at']) <= at <= pd.Timestamp(t['exited_at']) for t in state['trades']):
            reason = 'DETECTED_DURING_POSITION'
        if reason:
            record(state,key,reason,now)
            state['seen'].append(key)
        else:
            candidates.append(s)
    return candidates
