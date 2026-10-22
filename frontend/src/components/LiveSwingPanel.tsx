import { API_BASE_URL } from "../services/api";
import { useEffect, useState } from 'react';

type IndexState = { symbol: string; state: string; last_price: number | null; last_tick: string | null;
  warmup_minutes: number; closed_trades: number; rejected_ticks: number;
  hourly_context_bars: number; indicator_source: string; reconciliation_required: boolean;
  recovery_message: string;
  trade: { side: string; entry: number; stop: number; entry_at: string } | null;
  setup: { side: string; entry_trigger: number; initial_stop: number } | null };
type State = { state: string; message: string; running: boolean; indices: IndexState[] };
const base = `${API_BASE_URL}/index-swing/live`;

export function LiveSwingPanel() {
  const [state, setState] = useState<State | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    let pending = false;
    const refresh = async () => {
      if (pending) return;
      pending = true;
      try {
        const response = await fetch(`${base}/status`, { signal: controller.signal });
        if (!response.ok) throw new Error('Live tracker status unavailable');
        const data = await response.json();
        if (!controller.signal.aborted) { setState(data); setError(''); }
      } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Status unavailable'); }
      finally { pending = false; }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 2000);
    return () => { clearInterval(timer); controller.abort(); };
  }, []);
  const control = async (action: 'start' | 'stop') => {
    setBusy(true); setError('');
    try {
      const response = await fetch(`${base}/${action}`, { method: 'POST' });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Tracker request failed');
      setState(data);
    } catch (e) { setError(e instanceof Error ? e.message : 'Request failed'); }
    finally { setBusy(false); }
  };
  return <section className="mb-5 rounded-lg border border-emerald-800 bg-emerald-950/20 p-4">
    <div className="flex items-center justify-between gap-3">
      <div><h3 className="font-semibold text-emerald-200">Kotak live forward paper test</h3>
        <p className="text-xs text-slate-400">1-hour entries · 1-minute EMA trail · Stop checks on received ticks · No broker orders</p></div>
      <button disabled={busy || !state} onClick={() => void control(state?.running ? 'stop' : 'start')} className="rounded bg-emerald-700 px-3 py-2 text-sm disabled:opacity-50">{busy ? 'Please wait…' : state?.running ? 'Stop paper test' : 'Start paper test'}</button>
    </div>
    <p className="mt-2 text-sm">{state?.state || 'Loading status'} · {state?.message}</p>
    {error && <p role="alert" className="text-sm text-amber-300">{error} Displayed tracker information may be stale.</p>}
    <div className="mt-3 grid gap-3 md:grid-cols-2">{state?.indices.map(row => <div key={row.symbol} className="rounded border border-slate-700 p-3 text-sm">
      <p className="font-semibold">{row.symbol} · {row.state} · {row.last_price ?? 'No fresh tick'}</p>
      <p className="text-xs text-slate-400">Hourly history: {row.hourly_context_bars} candles across sessions · Minute indicators: {row.warmup_minutes >= 30 ? 'Ready' : `${row.warmup_minutes}/30 candles`} · {row.closed_trades} completed trades · {row.rejected_ticks} rejected ticks</p>
      <p className="text-xs text-slate-400">Indicator source: {row.indicator_source.split('_').join(' ')}</p>
      {row.reconciliation_required && <p className="mt-2 text-amber-300">Position and stop preserved. Automatic recovery is checking missed candles; entries and exits remain paused until the data is sufficient.</p>}
      {row.recovery_message && <p className="mt-1 text-xs text-amber-200">{row.recovery_message}</p>}
      {row.last_tick && <p className="text-xs">Last tick {new Date(row.last_tick).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' })} IST</p>}
      {row.trade ? <p className="mt-2 text-emerald-200">{row.trade.side} paper entry {row.trade.entry} · Trailing stop {row.trade.stop}</p> : row.setup ? <p className="mt-2">{row.setup.side} armed at {row.setup.entry_trigger} · Initial stop {row.setup.initial_stop}</p> : <p className="mt-2 text-slate-400">Waiting for live warmup and a completed eligible hourly setup.</p>}
    </div>)}</div>
    <p className="mt-3 text-xs text-slate-400">Log into Kotak before starting. Hourly context spans prior sessions; saved positions and stops survive restarts. Completed historical minute candles initialise EMA/volatility, so there is no routine daily warmup. Live candles and paper fills use Kotak ticks. Missing trading time pauses an affected position for reconciliation. Collection continues while the backend runs.</p>
    <a className="mt-2 inline-block text-xs text-emerald-300 underline" href={`${base}/export`}>Download forward paper trades</a>
  </section>;
}
