"""Forward-only paper execution. No broker order or session dependencies."""
import json
import math
import sqlite3
import uuid
import copy
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

import pandas as pd
from screener.trailing_swing import features, open_trade, book, public_trade
from screener.smooth_trailing import _plan, ema_stop
from screener.support_resistance import add_levels
from screener.smooth_trailing import execution_features

LIVE_DB = Path(__file__).resolve().parent/'training_data'/'live_swing.sqlite3'


class LiveJournal:
    def __init__(self, path=LIVE_DB):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._connection = None
        with self.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, at TEXT, kind TEXT, payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS trades (id TEXT PRIMARY KEY, status TEXT, payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS checkpoints (symbol TEXT PRIMARY KEY, payload TEXT)')

    @contextmanager
    def transaction(self):
        # Nested engine operations share one commit; every connection is closed.
        if self._connection is not None:
            yield self._connection
            return
        db = sqlite3.connect(self.path)
        self._connection = db
        try:
            with db:
                yield db
        finally:
            self._connection = None
            db.close()

    def checkpoint(self, symbol, payload):
        with self.transaction() as db:
            db.execute('INSERT OR REPLACE INTO checkpoints VALUES (?,?)',
                       (symbol, json.dumps(payload, allow_nan=False)))

    def restore(self, symbol):
        with self.transaction() as db:
            row = db.execute('SELECT payload FROM checkpoints WHERE symbol=?', (symbol,)).fetchone()
            return json.loads(row[0]) if row else None

    def hours(self, symbol):
        with self.transaction() as db:
            events = [(kind, json.loads(payload)) for kind, payload in db.execute("SELECT kind,payload FROM events WHERE kind IN ('HOUR','RECOVERY') ORDER BY id")]
        rows = []
        for kind, event in events:
            rows.extend([event] if kind == 'HOUR' else event.get('hours', []))
        return [r for r in rows if r['symbol'] == symbol]

    def write(self, kind, at, payload):
        with self.transaction() as db:
            db.execute('INSERT INTO events(at,kind,payload) VALUES (?,?,?)',
                       (at.isoformat(), kind, json.dumps(payload, allow_nan=False)))
            if kind == 'TRADE':
                db.execute('INSERT OR REPLACE INTO trades VALUES (?,?,?)',
                           (payload['id'], payload['status'], json.dumps(payload, allow_nan=False)))

    def trades(self):
        with self.transaction() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT payload FROM trades ORDER BY rowid')]

    def commit_recovery(self, symbol, state, trade, event, at):
        # One transaction: a crash cannot persist only the outcome or only the
        # advanced indicator boundary and replay the same gap twice.
        with self.transaction() as db:
            db.execute('INSERT INTO events(at,kind,payload) VALUES (?,?,?)',
                       (at.isoformat(), 'RECOVERY', json.dumps(event, allow_nan=False)))
            db.execute('INSERT OR REPLACE INTO trades VALUES (?,?,?)',
                       (trade['id'], trade['status'], json.dumps(trade, allow_nan=False)))
            db.execute('INSERT OR REPLACE INTO checkpoints VALUES (?,?)',
                       (symbol, json.dumps(state, allow_nan=False)))


def atomic_transition(method):
    """Commit a complete engine transition, or restore memory and roll back SQL."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        if getattr(self, '_transition_active', False):
            return method(self, *args, **kwargs)
        before = {k: copy.deepcopy(v) for k, v in self.__dict__.items()
                  if k != 'journal'}
        self._transition_active = True
        try:
            with self.journal.transaction():
                result = method(self, *args, **kwargs)
                self.checkpoint()
            return result
        except BaseException:
            journal = self.journal
            self.__dict__.clear()
            self.__dict__.update(before, journal=journal)
            raise
        finally:
            self._transition_active = False
    return wrapped


class LiveEngine:
    def __init__(self, symbol, hourly, journal, run_id=None):
        self.symbol, self.hourly, self.journal = symbol, hourly.copy(), journal
        self.run_id = run_id or uuid.uuid4().hex
        self.active = self.pending = self.candle = None
        self.last_tick = None
        self.last_price = None
        self.ema = self.volatility = self.previous_close = None
        self.warmup = 0
        self.cooldown = 0
        self.minutes = {}
        self.closed = 0
        self.rejected_ticks = 0
        self.state = 'WAITING_FOR_TICKS'
        self.reconciliation_required = False
        self.indicator_source = 'LIVE_WARMUP'
        self.context_as_of = None
        self.indicator_as_of = None
        self.recovery_message = ''
        self.recovery_ticks = []
        saved = journal.restore(symbol)
        if saved:
            for key, value in saved.items():
                if key not in ('last_tick', 'candle', 'minutes'):
                    setattr(self, key, value)
            self.last_tick = pd.Timestamp(saved['last_tick']) if saved.get('last_tick') else None
            self.candle = saved.get('candle')
            if self.candle:
                self.candle['at'] = pd.Timestamp(self.candle['at'])
            self.minutes = {pd.Timestamp(k): v for k, v in saved.get('minutes', {}).items()}
            self.state = 'RESTORED_WAITING_FOR_FRESH_TICK'
        # Trade journal is authoritative if a crash occurred between an event
        # commit and its checkpoint. Never resurrect a recorded closed trade.
        open_records = [r for r in journal.trades() if r.get('symbol') == symbol and r['status'] == 'OPEN']
        if open_records:
            latest = open_records[-1]
            if (not self.active or self.active['id'] != latest['id']
                    or any(self.active.get(k) != latest.get(k) for k in
                           ('stop', 'last_trail_update', 'events', 'legs'))):
                self.active = latest
                self.reconciliation_required = True
                # Legacy split commits cannot prove which indicator state belongs
                # to this stop. Keep the recorded stop and fail closed.
                self.indicator_as_of = None
                self.recovery_message = 'Legacy journal/checkpoint mismatch; indicator state cannot be safely reconstructed.'
        elif self.active:
            self.active = None
            self.pending = None
            self.cooldown = max(self.cooldown, 2)
        for row in journal.hours(symbol):
            self.hourly.loc[pd.Timestamp(row['start']), ['Open', 'High', 'Low', 'Close', 'Volume']] = [row[k] for k in ('Open', 'High', 'Low', 'Close', 'Volume')]
        self.hourly = self.hourly.sort_index()

    def checkpoint_payload(self):
        keys = ('run_id', 'active', 'pending', 'last_price', 'ema', 'volatility', 'previous_close',
                'warmup', 'cooldown', 'closed', 'rejected_ticks', 'reconciliation_required',
                'indicator_source', 'context_as_of', 'indicator_as_of', 'recovery_message')
        payload = {key: getattr(self, key) for key in keys}
        payload.update(last_tick=self.last_tick.isoformat() if self.last_tick is not None else None,
                       candle=dict(self.candle, at=self.candle['at'].isoformat()) if self.candle else None,
                       minutes={k.isoformat(): v for k, v in self.minutes.items()})
        return payload

    def checkpoint(self):
        self.journal.checkpoint(self.symbol, self.checkpoint_payload())

    @staticmethod
    def continuous(before, after):
        if before is None:
            return False
        if after-before <= pd.Timedelta(seconds=15):
            return True
        a, b = before.tz_convert('Asia/Kolkata'), after.tz_convert('Asia/Kolkata')
        if not (a.date() != b.date() and a.hour == 15 and a.minute == 29
                and a.second >= 45 and b.hour == 9 and b.minute == 15 and b.second <= 15):
            return False
        # Skip weekends, but do not assume an unobserved weekday was a holiday.
        return len(pd.bdate_range(a.normalize()+pd.Timedelta(days=1), b.normalize())) == 1

    @atomic_transition
    def seed_context(self, minutes, now):
        """Warm indicators from completed historical minutes, never import fills."""
        if self.active:
            return  # Preserve exact saved stop/EMA until continuity is established.
        bars = execution_features(minutes, 1, now)
        if not bars.iloc[-1].ready:
            return
        self.ema, self.volatility = float(bars.iloc[-1].ema), float(bars.iloc[-1].volatility)
        self.previous_close = float(bars.iloc[-1].Close)
        self.warmup = 30
        self.indicator_source = 'COMPLETED_YAHOO_MINUTES'
        self.context_as_of = (bars.index[-1]+pd.Timedelta(minutes=1)).isoformat()
        self.indicator_as_of = self.context_as_of
        self.reconciliation_required = False
        self.last_tick = None
        self.candle = None
        # Carry partial-hour history forward so a midday start can complete
        # its current hour. Missing minutes still prevent a fabricated hour.
        local = now.tz_convert('Asia/Kolkata')
        start = local.normalize()+pd.Timedelta(hours=9, minutes=15)
        if now >= start:
            start += pd.Timedelta(hours=max(0, int((now-start).total_seconds()//3600)))
        self.minutes.update({t: {c: float(row[c]) for c in ('Open', 'High', 'Low', 'Close')}
                             for t, row in bars.loc[bars.index >= start].iterrows()})
        full, ctx = features(self.hourly, now)
        ctx = add_levels(ctx, full)
        self.pending = None
        if len(ctx) >= 60 and not self.cooldown:
            latest = ctx.iloc[-1]
            available = ctx.index[-1]+pd.Timedelta(hours=1)
            # Only the latest current-hour setup or last session's final full
            # hour can be prospectively armed. Never retro-fill its trigger.
            current_hour = available <= now < available+pd.Timedelta(hours=1)
            previous_close = full.index[-1].tz_convert('Asia/Kolkata')
            next_session = (previous_close.hour == 15 and previous_close.minute == 15
                            and available.tz_convert('Asia/Kolkata').date() == previous_close.date()
                            and local.hour*60+local.minute < 555
                            and len(pd.bdate_range(previous_close.normalize()+pd.Timedelta(days=1), local.normalize())) == 1)
            if current_hour or next_session:
                self.pending = _plan(self.symbol, latest, ctx.index[-1], 1)
                if self.pending:
                    self.pending['id'] += ':live:'+self.run_id
        self.checkpoint()

    @atomic_transition
    def save_trade(self, at):
        record = public_trade(self.active, self.last_price)
        record['events'] = self.active['events'].copy()
        record.update(provenance='KOTAK_FORWARD_PAPER', provider='Kotak SFeed',
                      strategy_version='hourly-breakout-minute-ema-live-v1',
                      hourly_bootstrap_source='Yahoo; subsequent complete hours from Kotak ticks',
                      training_eligible=record['status'] == 'CLOSED' and not record.get('recovered_from_candles', False), cost_per_side_bps=2,
                      timestamp_basis='validated exchange Unix timestamp', run_id=self.run_id)
        if record.get('recovered_from_candles'):
            record.update(provenance='MIXED_FORWARD_AND_RECOVERED_PAPER', provider='Kotak + Yahoo',
                          timestamp_basis='Observed ticks and labelled historical candle estimates')
        self.journal.write('TRADE', at, record)
        self.checkpoint()

    @atomic_transition
    def interrupt(self, reason, at):
        # Stop/logout/disconnect preserves the position and exact indicators.
        # The next tick determines whether the missed time contained trading.
        if self.state == reason:
            return
        self.state = reason
        self.checkpoint()
        self.journal.write('GAP', at, {'symbol': self.symbol, 'reason': reason})

    @atomic_transition
    def heartbeat(self, now):
        if self.reconciliation_required:
            return
        local = now.tz_convert('Asia/Kolkata')
        in_session = local.dayofweek < 5 and 555 <= local.hour*60+local.minute < 930
        if self.last_tick is not None:
            prior = self.last_tick.tz_convert('Asia/Kolkata')
            opening_grace = (prior.date() != local.date() and prior.hour == 15 and prior.minute == 29
                             and local.hour == 9 and local.minute == 15 and local.second <= 15)
            if opening_grace:
                return
        if in_session and self.last_tick is not None and now-self.last_tick > pd.Timedelta(seconds=15):
            self.interrupt('STALE_FEED', now)
        elif self.candle and now >= self.candle['at']+pd.Timedelta(minutes=1):
            # Require a recent tick near the minute boundary. No invented flat candles.
            if self.last_tick is not None and self.candle['at']+pd.Timedelta(minutes=1)-self.last_tick <= pd.Timedelta(seconds=15):
                self.finish_minute()

    @atomic_transition
    def finish_minute(self):
        candle, self.candle = self.candle, None
        if not candle or not candle['complete']:
            return
        at = candle.pop('at')
        candle.pop('complete')
        close = candle['Close']
        width = candle['High']-candle['Low']
        if self.previous_close is not None:
            width = max(width, abs(candle['High']-self.previous_close), abs(candle['Low']-self.previous_close))
        self.ema = close if self.ema is None else self.ema+(close-self.ema)*2/22
        self.volatility = width if self.volatility is None else self.volatility+(width-self.volatility)*2/15
        self.previous_close = close
        self.warmup += 1
        end = at+pd.Timedelta(minutes=1)
        self.indicator_as_of = end.isoformat()
        self.journal.write('MINUTE', end, dict(candle, symbol=self.symbol, start=at.isoformat(), source='KOTAK_TICKS'))
        self.minutes[at] = candle
        if self.active:
            old = self.active['stop']
            stop, buffer, multiplier = ema_stop(self.active, self.ema, self.volatility)
            self.active.update(stop=stop, ema_price=round(self.ema, 2), volatility_buffer_points=round(buffer, 2),
                               buffer_multiplier=multiplier, last_trail_update=end.isoformat(),
                               exit_pending=self.active['direction']*(close-stop) <= 0)
            if stop != old:
                self.active['events'].append({'at': end.isoformat(), 'from': old, 'to': stop, 'kind': 'EMA_STOP_UPDATE'})
            self.save_trade(end)
        local = end.tz_convert('Asia/Kolkata')
        if local.minute == 15 and 10 <= local.hour <= 15:
            self.finish_hour(end-pd.Timedelta(hours=1), 60, end)
        elif local.hour == 15 and local.minute == 30:
            self.finish_hour(end-pd.Timedelta(minutes=15), 15, end)
        self.checkpoint()

    @atomic_transition
    def finish_hour(self, start, count, end):
        times = pd.date_range(start, periods=count, freq='min')
        if not all(t in self.minutes for t in times):
            self.pending = None
            return
        rows = [self.minutes[t] for t in times]
        row = dict(Open=rows[0]['Open'], High=max(r['High'] for r in rows),
                   Low=min(r['Low'] for r in rows), Close=rows[-1]['Close'], Volume=0.)
        self.hourly.index = self.hourly.index.tz_convert('UTC')
        self.hourly.loc[start, list(row)] = list(row.values())
        self.hourly = self.hourly.sort_index().iloc[-2000:]
        self.journal.write('HOUR', end, dict(row, symbol=self.symbol, start=start.isoformat()))
        self.minutes = {t: r for t, r in self.minutes.items() if t >= end}
        if count != 60:
            return
        full, context = features(self.hourly, end)
        context = add_levels(context, full)
        signal = context.iloc[-1]
        if self.active:
            return
        if self.pending:
            self.pending['hours_left'] -= 1
            if self.pending['hours_left'] <= 0 or signal.direction != self.pending['direction']:
                self.pending = None
        if self.cooldown:
            self.cooldown -= 1
        elif self.pending is None and self.warmup >= 30:
            self.pending = _plan(self.symbol, signal, context.index[-1], 1)
            if self.pending:
                self.pending['id'] += ':live:'+self.run_id
                self.journal.write('SETUP', end, self.pending)

    @atomic_transition
    def tick(self, price, at, received_at):
        if (not math.isfinite(price) or price <= 0 or at.tzinfo is None
                or abs((received_at-at).total_seconds()) > 5
                or (self.last_tick is not None and at < self.last_tick)
                or (self.indicator_as_of and at < pd.Timestamp(self.indicator_as_of))):
            self.rejected_ticks += 1
            return
        at = at.tz_convert('UTC')
        local = at.tz_convert('Asia/Kolkata')
        if local.dayofweek >= 5 or not 555 <= local.hour*60+local.minute < 930:
            return
        if self.last_tick is None and self.context_as_of:
            anchor = pd.Timestamp(self.context_as_of)
            if at-anchor > pd.Timedelta(seconds=65) and not self.continuous(anchor-pd.Timedelta(seconds=1), at):
                self.warmup = 0
                self.pending = None
                self.ema = self.volatility = self.previous_close = None
            self.context_as_of = None
        if self.last_tick is not None and at-self.last_tick > pd.Timedelta(seconds=15):
            if not self.continuous(self.last_tick, at):
                self.reconciliation_required = bool(self.active)
                self.pending = self.candle = None
                if not self.active:
                    self.minutes.clear()
                    self.warmup = 0
                    self.ema = self.volatility = self.previous_close = None
                self.interrupt('TICK_GAP', at)
        if self.reconciliation_required:
            self.state = 'RECONCILIATION_REQUIRED'
            self.last_price = price
            if not self.recovery_ticks or at >= self.recovery_ticks[-1][0]:
                self.recovery_ticks.append((at, price))
                self.recovery_ticks = self.recovery_ticks[-20000:]
            self.checkpoint()
            return
        bucket = at.floor('min')
        if self.candle and bucket > self.candle['at']:
            self.finish_minute()
        self.last_tick, self.last_price = at, price
        self.state = 'LIVE' if self.warmup >= 30 else 'WARMING_UP'
        if self.active:
            trade = self.active
            if trade['exit_pending'] or trade['direction']*(price-trade['stop']) <= 0:
                book(trade, trade['remaining'], price, at, 'LIVE_TICK_STOP')
                self.save_trade(at)
                self.active = None
                self.closed += 1
                self.cooldown = 2
            else:
                trade['best_price'] = max(trade['best_price'], price) if trade['direction'] == 1 else min(trade['best_price'], price)
        elif self.pending and self.warmup >= 30:
            plan = self.pending
            gap = plan['direction']*(price-plan['entry_trigger'])
            if gap >= 0:
                if gap <= plan['max_entry_gap_points']:
                    self.active = open_trade(plan, price, at)
                    self.active.update(trail_stage='EMA', trailing_interval_minutes=1)
                    self.save_trade(at)
                self.pending = None
        if not self.candle:
            self.candle = dict(at=bucket, Open=price, High=price, Low=price, Close=price,
                               complete=(at-bucket).total_seconds() <= 5)
        else:
            self.candle.update(High=max(self.candle['High'], price), Low=min(self.candle['Low'], price), Close=price)
        self.checkpoint()

    def reconcile(self, raw, now):
        from screener.live_recovery import recover
        if not self.reconciliation_required or not self.active:
            return False
        try:
            result = recover(self, raw, now)
        except (ValueError, TypeError, KeyError) as exc:
            self.recovery_message = str(exc)
            return False
        proposed = copy.copy(self)
        proposed.minutes = dict(self.minutes)
        proposed.minutes.update(result['completed'])
        # Save reconstructed full hours to the state; never arm retroactive entries.
        proposed.hourly = self.hourly.copy()
        recovered_hours = []
        for at in result['completed']:
            local = at.tz_convert('Asia/Kolkata')
            count = 15 if local.hour == 15 and local.minute == 29 else 60 if local.minute == 14 else 0
            if not count:
                continue
            start = at-pd.Timedelta(minutes=count-1)
            times = pd.date_range(start, periods=count, freq='min')
            if all(t in proposed.minutes for t in times):
                rows = [proposed.minutes[t] for t in times]
                proposed.hourly.loc[start, ['Open', 'High', 'Low', 'Close', 'Volume']] = [rows[0]['Open'], max(r['High'] for r in rows), min(r['Low'] for r in rows), rows[-1]['Close'], 0.]
                recovered_hours.append(dict(symbol=self.symbol, start=start.isoformat(), Open=rows[0]['Open'],
                                            High=max(r['High'] for r in rows), Low=min(r['Low'] for r in rows), Close=rows[-1]['Close'], Volume=0.))
        proposed.minutes = {t: r for t, r in proposed.minutes.items() if t >= result['last_tick']-pd.Timedelta(hours=1)}
        for key in ('ema', 'volatility', 'previous_close', 'indicator_as_of', 'candle', 'last_tick', 'last_price'):
            setattr(proposed, key, result[key])
        proposed.active = result['trade'] if result['trade']['status'] == 'OPEN' else None
        proposed.pending = None
        proposed.closed = self.closed+int(proposed.active is None)
        proposed.cooldown = 2 if proposed.active is None else self.cooldown
        proposed.warmup = self.warmup+result['recovered_minutes']
        proposed.reconciliation_required = False
        proposed.recovery_ticks = []
        proposed.state = 'LIVE'
        proposed.recovery_message = f"Recovered {result['recovered_minutes']} minutes; outcome marked as mixed-source paper evidence."
        record = public_trade(result['trade'], result['last_price'])
        record.update(events=result['trade']['events'], provenance='MIXED_FORWARD_AND_RECOVERED_PAPER',
                      provider='Kotak + Yahoo', training_eligible=False,
                      strategy_version='hourly-breakout-minute-ema-live-v1', run_id=self.run_id, cost_per_side_bps=2)
        event = dict(symbol=self.symbol, trade_id=record['id'], start=record['recovery_start'],
                     end=record['recovery_end'], candles=result['candles'], hours=recovered_hours, outcome=record['status'])
        self.journal.commit_recovery(self.symbol, proposed.checkpoint_payload(), record, event, now)
        self.__dict__.update(proposed.__dict__)
        return True

    def snapshot(self):
        return dict(symbol=self.symbol, state=self.state, last_price=self.last_price,
                    last_tick=self.last_tick.isoformat() if self.last_tick is not None else None,
                    warmup_minutes=self.warmup, closed_trades=self.closed, rejected_ticks=self.rejected_ticks,
                    hourly_context_bars=len(self.hourly), indicator_source=self.indicator_source,
                    reconciliation_required=self.reconciliation_required,
                    recovery_message=self.recovery_message,
                    trade=public_trade(self.active, self.last_price) if self.active else None, setup=self.pending)
