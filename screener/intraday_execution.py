"""Deterministic minute-bar paper execution. No broker dependencies."""
from dataclasses import dataclass, asdict
import math
import pandas as pd


@dataclass(frozen=True)
class Risk:
    equity: float = 100000.
    per_trade: float = .0025
    open_risk: float = .005
    daily_loss: float = .01
    positions: int = 2
    entries: int = 3
    utilization: float = .9
    position_cap: float = .45
    slippage: float = .0005
    fee_rate: float = .001  # Conservative configurable all-in estimate per side.
    participation: float = .01


def new_account(cfg=Risk()):
    return dict(balance=cfg.equity, day=None, day_equity=cfg.equity, halted=False,
                last_bar=None, trades=[], orders=[], events=[], config=asdict(cfg))


def equity(account):
    return account['balance'] + sum(t['quantity']*t['sign']*(t['mark']-t['entry'])
                                   for t in account['trades'] if t['status'] == 'OPEN')


def queue(account, signal, observed_at):
    at = pd.Timestamp(observed_at)
    identity = f"{signal['symbol']}:{signal['side']}:{signal['timestamp']}"
    if any(o['id'] == identity for o in account['orders']):
        return
    # Activate at the next full minute after actual observation, never in the past.
    activate = at.ceil('min')
    expires = pd.Timestamp(signal['valid_until'])
    status = 'QUEUED' if activate < expires else 'EXPIRED'
    account['orders'].append(dict(signal, id=identity, status=status,
                                  activate_at=activate.isoformat(), observed_at=at.isoformat()))


def process(account, bars, stamp, cfg=Risk()):
    """Process a completed minute once. Stop-first for ambiguous OHLC paths."""
    stamp = pd.Timestamp(stamp)
    if account['last_bar'] and stamp <= pd.Timestamp(account['last_bar']):
        return
    active = [t for t in account['trades'] if t['status'] == 'OPEN']
    required = {t['symbol'] for t in active}
    if required - bars.keys():
        raise ValueError('Missing position candles; execution paused')
    if active and account['last_bar'] and stamp-pd.Timestamp(account['last_bar']) != pd.Timedelta(minutes=1):
        raise ValueError('Gap in position history; execution paused')
    for b in bars.values():
        if any(not math.isfinite(float(b[k])) for k in ('Open','High','Low','Close','Volume')) or b['Low'] <= 0 or b['Volume'] < 0 or b['High'] < max(b['Open'], b['Close'], b['Low']) or b['Low'] > min(b['Open'], b['Close']):
            raise ValueError('Invalid execution candle')
    day, clock = str(stamp.date()), stamp.strftime('%H:%M')
    opening_equity = equity(account)
    opening_exposure = sum(t['quantity']*t['mark'] for t in active)
    opening_risk = sum(t['quantity']*(max(0, t['sign']*(t['mark']-t['stop']))+t['mark']*(cfg.fee_rate+cfg.slippage)) for t in active)
    if account['day'] != day:
        if active:
            raise ValueError('Unresolved prior-session positions')
        account.update(day=day, day_equity=equity(account), halted=False)

    def close(t, price, reason):
        fill = price*(1-t['sign']*cfg.slippage)
        fee = t['quantity']*fill*cfg.fee_rate
        profit = t['quantity']*t['sign']*(fill-t['entry'])-fee
        account['balance'] += profit
        t.update(status='CLOSED', exit=fill, exit_at=stamp.isoformat(), exit_reason=reason,
                 net_pnl=profit-t['entry_fee'], mark=fill)
        account['events'].append(dict(type='EXIT_EXECUTED', at=stamp.isoformat(), symbol=t['symbol'], reason=reason))

    # Exit existing positions before assessing current portfolio risk.
    for t in active:
        b, sign = bars[t['symbol']], t['sign']
        if clock >= '15:15':
            close(t, b['Open'], 'TIME_EXIT')
        elif sign*(b['Open']-t['stop']) <= 0:
            close(t, b['Open'], 'GAP_STOP')
        elif (b['Low'] <= t['stop'] if sign == 1 else b['High'] >= t['stop']):
            close(t, t['stop'], 'STOP')
        elif (b['High'] >= t['target'] if sign == 1 else b['Low'] <= t['target']):
            close(t, t['target'], 'TARGET_2R')
        else:
            t['mark'] = b['Close']
    if equity(account) <= account['day_equity']*(1-cfg.daily_loss):
        account['halted'] = True
    if account['halted']:
        for t in account['trades']:
            if t['status'] == 'OPEN':
                close(t, bars[t['symbol']]['Close'], 'DAILY_LOSS_EXIT')

    for order in account['orders']:
        if order['status'] != 'QUEUED' or stamp < pd.Timestamp(order['activate_at']):
            continue
        def reject(reason):
            order.update(status='SKIPPED', reason=reason)
        if stamp >= pd.Timestamp(order['valid_until']) or clock >= '14:30' or stamp.date() != pd.Timestamp(order['timestamp']).date():
            reject('ENTRY_EXPIRED'); continue
        b = bars.get(order['symbol'])
        if b is None:
            reject('MISSING_ENTRY_CANDLE'); continue
        sign = 1 if order['side'] == 'long' else -1
        if sign*(b['Open']-order['stop']) <= 0:
            reject('INVALIDATED'); continue
        if not (b['High'] >= order['entry'] if sign == 1 else b['Low'] <= order['entry']):
            if b['Low'] <= order['stop'] if sign == 1 else b['High'] >= order['stop']:
                reject('INVALIDATED')
            continue
        active = [t for t in account['trades'] if t['status'] == 'OPEN']
        filled_today = [t for t in account['trades'] if t['entry_at'][:10] == day]
        if account['halted'] or len(active) >= cfg.positions or len(filled_today) >= cfg.entries or any(t['symbol'] == order['symbol'] for t in filled_today):
            reject('PORTFOLIO_LIMIT'); continue
        opening_fill = sign*(b['Open']-order['entry']) >= 0
        price = b['Open'] if opening_fill else order['entry']
        fill = price*(1+sign*cfg.slippage)
        distance = sign*(fill-order['stop'])
        if distance <= 0 or distance/fill > .03:
            reject('GAP_OR_STOP_TOO_WIDE'); continue
        # Require room to the detected opposing structure after costs.
        cost_per_share = (fill+order['stop'])*cfg.fee_rate + order['stop']*cfg.slippage
        risk = distance+cost_per_share
        target = fill+sign*2*risk
        if sign*(order['target']-target) < 0:
            reject('INSUFFICIENT_NET_REWARD'); continue
        value = min(opening_equity, equity(account))
        exposure = max(opening_exposure, sum(t['quantity']*t['mark'] for t in active))
        current_risk = max(opening_risk, sum(t['quantity']*(max(0, t['sign']*(t['mark']-t['stop']))+t['mark']*(cfg.fee_rate+cfg.slippage)) for t in active))
        budget = min(value*cfg.per_trade, value*cfg.open_risk-current_risk)
        capital = min(value*cfg.position_cap, value*cfg.utilization-exposure)
        qty = max(0, math.floor(min(budget/risk, capital/(fill*(1+cfg.fee_rate)), order.get('reference_volume', 0)*cfg.participation)))
        if qty == 0:
            reject('INSUFFICIENT_CAPITAL_OR_LIQUIDITY'); continue
        fee = qty*fill*cfg.fee_rate
        account['balance'] -= fee
        trade = dict(id=order['id'], symbol=order['symbol'], side=order['side'], sign=sign,
                     entry=fill, entry_at=stamp.isoformat(), quantity=qty, entry_fee=fee,
                     stop=order['stop'], target=target, mark=fill, status='OPEN', net_pnl=None)
        account['trades'].append(trade)
        order['status'] = 'FILLED'
        account['events'].append(dict(type='ENTRY_EXECUTED', at=stamp.isoformat(), symbol=trade['symbol']))
        # Conservative same-bar stop precedence; never assume the target came first.
        if b['Low'] <= trade['stop'] if sign == 1 else b['High'] >= trade['stop']:
            close(trade, trade['stop'], 'SAME_BAR_STOP')
        elif opening_fill and (b['High'] >= target if sign == 1 else b['Low'] <= target):
            close(trade, target, 'TARGET_2R')
        else:
            trade['mark'] = b['Close']
        if equity(account) <= account['day_equity']*(1-cfg.daily_loss):
            account['halted'] = True
    account['last_bar'] = stamp.isoformat()
