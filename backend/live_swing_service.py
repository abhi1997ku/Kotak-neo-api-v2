"""One backend-owned index feed for forward paper testing; no broker orders."""
import asyncio
import time
import pandas as pd
from neo_api_client.urls import SFEED_WEBSOCKET_URL
from neo_api_client.websocket.feed import SFeedWebSocket, SFeedIndex, WsToken
from backend.session_manager import get_session_manager
from screener.data_fetcher import DataFetcher, yf
from screener.index_pattern import clean_bars
from screener.live_swing import LiveEngine, LiveJournal


def exchange_time(value, now):
    # SFeed wire timestamps must be plausible Unix s/ms/us/ns. Reject unknown
    # encodings rather than silently timestamping stale snapshots as new ticks.
    for unit in ('s', 'ms', 'us', 'ns'):
        try:
            result = pd.Timestamp(int(value), unit=unit, tz='UTC')
            if abs((now-result).total_seconds()) <= 5:
                return result
        except (ValueError, OverflowError, TypeError):
            pass
    return None


def bootstrap():
    histories = {}
    for symbol, ticker in [('NIFTY', '^NSEI'), ('BANKNIFTY', '^NSEBANK')]:
        raw = yf.download(ticker, period='1y', interval='1h', ignore_tz=False,
                          auto_adjust=False, progress=False, timeout=20)
        data = DataFetcher._normalize_ohlcv(raw, ticker)
        if data is None:
            raise ValueError('Hourly bootstrap unavailable')
        hourly = clean_bars(data)
        raw_minutes = yf.download(ticker, period='7d', interval='1m', ignore_tz=False,
                                  auto_adjust=False, progress=False, timeout=20)
        minutes = DataFetcher._normalize_ohlcv(raw_minutes, ticker)
        histories[symbol] = (hourly, minutes)
    return histories


def recovery_history(symbol):
    ticker = {'NIFTY': '^NSEI', 'BANKNIFTY': '^NSEBANK'}[symbol]
    raw = yf.download(ticker, period='7d', interval='1m', ignore_tz=False,
                      auto_adjust=False, progress=False, timeout=20)
    return DataFetcher._normalize_ohlcv(raw, ticker)


class LiveSwingService:
    def __init__(self):
        self.task = None
        self.engines = {}
        self.state = 'STOPPED'
        self.message = 'Start after logging into Kotak. Paper trades only.'
        self.journal = None
        self.recovery_jobs = {}
        self.recovery_attempts = {}
        self.recovery_lock = asyncio.Lock()

    async def fetch_recovery(self, symbol):
        async with self.recovery_lock:
            return await asyncio.to_thread(recovery_history, symbol)

    def service_recovery(self, now):
        for symbol, engine in self.engines.items():
            job = self.recovery_jobs.get(symbol)
            if job and job.done():
                del self.recovery_jobs[symbol]
                try:
                    engine.reconcile(job.result(), now)
                except Exception as exc:
                    engine.recovery_message = f'Recovery unavailable ({type(exc).__name__}); position remains paused.'
                job = None
            if (engine.reconciliation_required and engine.active and not job
                    and time.monotonic()-self.recovery_attempts.get(symbol, -float('inf')) >= 30):
                self.recovery_attempts[symbol] = time.monotonic()
                engine.recovery_message = 'Fetching missing completed minutes; live ticks are being buffered.'
                self.recovery_jobs[symbol] = asyncio.create_task(self.fetch_recovery(symbol))

    async def start(self):
        if self.task and not self.task.done():
            return self.snapshot()
        client = get_session_manager().get_client()
        if not client:
            raise ValueError('Log into Kotak first using the existing login screen.')
        self.state, self.message = 'STARTING', 'Restoring swing positions and loading prior hourly/minute context.'
        self.task = asyncio.create_task(self.run(client))
        return self.snapshot()

    def interrupt(self, reason):
        for engine in self.engines.values():
            engine.interrupt(reason, pd.Timestamp.now(tz='UTC'))

    async def run(self, client):
        next_tick = None
        try:
            histories = await asyncio.to_thread(bootstrap)
            self.journal = LiveJournal()
            self.engines = {}
            for symbol, (hours, minutes) in histories.items():
                engine = LiveEngine(symbol, hours, self.journal)
                if minutes is not None and not engine.active:
                    engine.seed_context(minutes, pd.Timestamp.now(tz='UTC'))
                self.engines[symbol] = engine
            if get_session_manager().get_client() is not client:
                raise ValueError('Session changed')
            config = client.client.configuration
            feed = SFeedWebSocket(access_token=getattr(config, 'edit_token', None) or client.session.trade_token,
                                  sid=getattr(config, 'edit_sid', None) or client.session.edit_sid,
                                  ucc=client.config['ucc'], url=client.session.feed_url or SFEED_WEBSOCKET_URL)
            def disconnected():
                self.state = 'RECONNECTING'
                self.interrupt('DISCONNECTED')
            feed.on_disconnect = disconnected
            async with feed:
                await feed.subscribe_index([WsToken('nse_cm', 'Nifty 50'), WsToken('nse_cm', 'Nifty Bank')])
                self.state, self.message = 'CONNECTED', 'Context restored. Waiting for fresh ticks; missed trading time requires reconciliation.'
                next_tick = asyncio.create_task(feed.__anext__())
                while get_session_manager().get_client() is client:
                    done, _ = await asyncio.wait({next_tick}, timeout=1)
                    now = pd.Timestamp.now(tz='UTC')
                    for engine in self.engines.values():
                        engine.heartbeat(now)
                    self.service_recovery(now)
                    if next_tick not in done:
                        continue
                    message = next_tick.result()
                    next_tick = asyncio.create_task(feed.__anext__())
                    if not isinstance(message, SFeedIndex):
                        continue
                    name = ''.join(c for c in message.name.upper() if c.isalnum())
                    symbol = {'NIFTY50': 'NIFTY', 'NIFTYBANK': 'BANKNIFTY', 'BANKNIFTY': 'BANKNIFTY'}.get(name)
                    if symbol:
                        stamp = exchange_time(message.last_trade_time, now)
                        if stamp is None:
                            self.engines[symbol].rejected_ticks += 1
                            self.message = 'Ignoring stale or unrecognized exchange timestamps; no paper entry from these ticks.'
                            continue
                        self.state = 'CONNECTED'
                        self.engines[symbol].tick(float(message.last_traded_price), stamp, now)
                self.state, self.message = 'SESSION_ENDED', 'Kotak login ended; paper tracking stopped.'
        except asyncio.CancelledError:
            self.state, self.message = 'STOPPED', 'Paper tracking stopped.'
            raise
        except Exception as exc:
            self.state, self.message = 'ERROR', f'Paper feed stopped ({type(exc).__name__}); check login/feed and restart.'
        finally:
            for job in self.recovery_jobs.values():
                job.cancel()
            await asyncio.gather(*self.recovery_jobs.values(), return_exceptions=True)
            self.recovery_jobs.clear()
            if next_tick:
                next_tick.cancel()
                await asyncio.gather(next_tick, return_exceptions=True)
            self.interrupt('TRACKER_STOPPED')

    async def stop(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        return self.snapshot()

    def snapshot(self):
        return {'state': self.state, 'message': self.message,
                'running': bool(self.task and not self.task.done()),
                'mode': 'KOTAK_FORWARD_PAPER', 'broker_orders_enabled': False,
                'indices': [e.snapshot() for e in self.engines.values()]}


service = LiveSwingService()
