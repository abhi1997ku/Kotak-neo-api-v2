import { API_BASE_URL } from "../services/api";
import { useCallback, useEffect, useRef, useState } from "react";
import { IndexExperimentResults } from "./IndexExperimentResults";
import { LiveSwingPanel } from "./LiveSwingPanel";

type Plan = {
  id: string; side: string; signal_at: string; valid_after: string; entry_trigger: number;
  initial_stop: number; initial_risk_points: number; max_entry_gap_points: number;
  milestones: number[]; trailing_interval_minutes: number; hours_left: number; reason: string;
};
type Trade = Plan & {
  entry: number; entry_at: string; stop: number; remaining: number; status: string;
  realized_net_points: number; open_points: number | null; locked_points_per_remaining_unit: number;
  exit_at?: string; exit_reason?: string; net_r: number;
  trail_stage: string; exit_pending: boolean; favorable_move_points: number;
  ema_price?: number; volatility_buffer_points?: number; last_trail_update?: string;
  legs: { at: string; kind: string; fraction: number; price: number }[];
  events: { at: string; from: number; to: number }[];
};
type Swing = {
  symbol: string; status: string; data_status?: string; as_of?: string; last_price?: number;
  actionable?: boolean; desired_move_points?: number[]; trailing_interval_minutes?: number; reason: string;
  trade?: Trade | null; setup?: Plan | null; watch_levels?: { long: Plan; short: Plan };
  trailing_rules?: string[];
  support_resistance?: { support: number | null; resistance: number | null; previous_session_high: number | null; previous_session_low: number | null };
  journal?: { saved: number; strategy_version: string } | null;
  backtest?: { closed_trades: number; profitable_rate: number | null; net_weighted_points: number;
    net_r: number; max_closed_trade_drawdown_r: number; unknown_data_gap_trades: number;
    recent_trades: Trade[]; method: string; period_start: string; period_end: string; execution_bars: number };
};
const price = (value?: number | null) => value == null ? "—" : value.toLocaleString("en-IN", { maximumFractionDigits: 2 });
const date = (value: string) => new Date(value).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" });

export function IndexSwingPanel() {
  const [results, setResults] = useState<Swing[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [updated, setUpdated] = useState("");
  const [trailMinutes, setTrailMinutes] = useState(1);
  const inFlight = useRef<AbortController | null>(null);
  const scan = useCallback(async () => {
    if (inFlight.current) return;
    const controller = new AbortController();
    inFlight.current = controller;
    setBusy(true); setError("");
    try {
      const response = await fetch(`${API_BASE_URL}/index-swing/scan?trail_minutes=${trailMinutes}`, { signal: controller.signal });
      if (!response.ok) throw new Error("Could not refresh swing plans. Check the backend and retry.");
      const data = await response.json();
      if (controller.signal.aborted) return;
      setResults(data.results); setUpdated(data.generated_at);
    } catch (e) {
      if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Refresh failed");
    } finally {
      if (inFlight.current === controller) { inFlight.current = null; setBusy(false); }
    }
  }, [trailMinutes]);
  useEffect(() => {
    void scan();
    const timer = setInterval(() => { void scan(); }, 30_000);
    return () => { clearInterval(timer); inFlight.current?.abort(); inFlight.current = null; };
  }, [scan]);
  return <section className="rounded-xl border border-slate-800 bg-slate-900 p-4">
    <LiveSwingPanel />
    <div className="flex items-center justify-between gap-4">
      <div><h2 className="text-lg font-semibold">Historical replay · Nifty & Bank Nifty</h2>
        <p className="text-sm text-slate-400">1-hour mathematical breakout · Open target · Full position trails until exit</p></div>
      <button onClick={() => void scan()} disabled={busy} className="rounded bg-indigo-600 px-4 py-2 text-sm disabled:opacity-50">{busy ? "Updating trades…" : "Refresh trades"}</button>
    </div>
    <label className="mt-3 block text-sm text-slate-300">EMA trailing candle: <select value={trailMinutes} onChange={e => { setResults([]); setUpdated(""); setTrailMinutes(Number(e.target.value)); }} className="rounded bg-slate-800 px-2 py-1"><option value={1}>1 minute</option><option value={5}>5 minutes</option></select></label>
    <a href={`${API_BASE_URL}/index-swing/history/export`} className="mt-2 inline-block text-sm text-indigo-300 underline">Download saved entry/exit history (JSONL)</a>
    {updated && <p className="mt-1 text-xs text-slate-500">Last refresh {date(updated)} IST · checks every 30 seconds; EMA stop updates each completed {trailMinutes}-minute candle</p>}
    {error && <p role="alert" className="mt-3 text-amber-300">{error} {results.length > 0 && "Displayed plans are from the previous successful refresh; do not treat them as current."}</p>}
    {!results.length && busy && <p className="mt-4 text-sm text-slate-400">Loading hourly candles and rebuilding the trade tracker…</p>}
    <div className="mt-4 grid gap-4 xl:grid-cols-2">{results.map(r => <article key={r.symbol} className="rounded-lg border border-slate-700 bg-slate-950/50 p-4">
      <div className="flex items-center justify-between"><h3 className="font-semibold">{r.symbol} · {price(r.last_price)}</h3><span className="text-xs text-indigo-300">{r.status.split("_").join(" ")}</span></div>
      {r.desired_move_points && <p className="mt-1 text-sm text-slate-400">Desired swing: {r.desired_move_points.join("–")} points · target stays OPEN</p>}
      {r.trailing_interval_minutes && <p className="mt-1 text-xs text-indigo-300">EMA21 on {r.trailing_interval_minutes}-minute candles with a smoothly adapting volatility buffer. No partial profit exits.</p>}
      {r.as_of && <p className="text-xs text-slate-500">Last completed bar starts {date(r.as_of)} IST</p>}
      {r.support_resistance && <div className="mt-2 rounded border border-slate-700 p-2 text-xs">
        <p>Hourly support: {price(r.support_resistance.support)} · Resistance: {price(r.support_resistance.resistance)}</p>
        <p>Previous session high: {price(r.support_resistance.previous_session_high)} · Low: {price(r.support_resistance.previous_session_low)}</p>
        <p className="mt-1 text-slate-500">Confirmed hourly swings and previous-session levels. Context only: these do not change entries or the EMA stop. A dash means no qualifying level.</p>
      </div>}
      {r.data_status === "STALE" && <p className="mt-2 rounded bg-amber-950 p-2 text-sm text-amber-300">Stale market data — these are historical levels, not an entry instruction.</p>}
      {r.data_status === "LAST_SESSION" && <p className="mt-2 text-xs text-amber-300">Last session snapshot. Recheck after the next session opens; gaps can invalidate entries.</p>}
      {r.trade && <div className="mt-3 rounded border border-indigo-700 bg-indigo-950/30 p-3">
        <h4 className="font-semibold text-indigo-200">{r.trade.side} model position · {Math.round(r.trade.remaining*100)}% remaining</h4>
        <p className="text-xs text-slate-400">Model entry {date(r.trade.entry_at)} IST; this is not a broker position or a new entry.</p>
        <dl className="mt-3 grid grid-cols-2 gap-3 text-sm">
          <div><dt className="text-slate-400">Entry</dt><dd>{price(r.trade.entry)}</dd></div>
          <div><dt className="text-amber-300">Current trailing stop</dt><dd className="text-lg font-semibold text-amber-200">{price(r.trade.stop)}</dd></div>
          <div><dt className="text-slate-400">Initial stop</dt><dd>{price(r.trade.initial_stop)}</dd></div>
          <div><dt className="text-slate-400">Exit target</dt><dd>OPEN — follow the stop</dd></div>
          <div><dt className="text-slate-400">Net realized points, including costs</dt><dd>{price(r.trade.realized_net_points)}</dd></div>
          <div><dt className="text-slate-400">Remaining unrealized points</dt><dd>{price(r.trade.open_points)}</dd></div>
        </dl>
        <p className="mt-3 text-sm text-indigo-300">Trail stage: {r.trade.trail_stage} · Peak favorable move: {price(r.trade.favorable_move_points)} points</p>
        <p className="mt-1 text-xs text-slate-400">EMA21: {price(r.trade.ema_price)} · Volatility buffer: {price(r.trade.volatility_buffer_points)} points{r.trade.last_trail_update && ` · Stop calculated ${date(r.trade.last_trail_update)} IST`}</p>
        {r.trade.exit_pending && <p className="mt-2 text-sm text-amber-300">The completed close breached the newly calculated stop. Exit is pending at the next available bar open.</p>}
        <details className="mt-2 text-xs"><summary className="cursor-pointer text-indigo-300">Stop changes & exit history</summary>
          {r.trade.events.map((e, i) => <p key={i}>{date(e.at)} · Stop {price(e.from)} → {price(e.to)}</p>)}
          {r.trade.legs.map((leg, i) => <p key={`leg-${i}`}>{date(leg.at)} · {Math.round(leg.fraction*100)}% at {price(leg.price)} · {leg.kind}</p>)}
        </details>
      </div>}
      {r.setup && <div className="mt-3 rounded border border-emerald-800 bg-emerald-950/20 p-3">
        <h4 className="font-semibold text-emerald-200">{r.setup.side} conditional entry {r.actionable ? "armed" : "plan"}</h4>
        <div className="mt-2 grid grid-cols-2 gap-3">
          <div><p className="text-xs text-slate-400">{r.setup.side === "LONG" ? "Buy above" : "Sell below"}</p><p className="text-xl font-semibold">{price(r.setup.entry_trigger)}</p></div>
          <div><p className="text-xs text-slate-400">Initial stop-loss</p><p className="text-xl font-semibold text-amber-300">{price(r.setup.initial_stop)}</p></div>
        </div>
        <p className="mt-2 text-xs">Trigger risk {price(r.setup.initial_risk_points)} points · Open target · {r.setup.hours_left} full candles of validity left</p>
        <p className="mt-1 text-xs text-slate-400">{r.setup.reason}</p>
        <p className="mt-1 text-xs text-slate-400">Valid after {date(r.setup.valid_after)} IST. Cancel if the opening gap goes more than {price(r.setup.max_entry_gap_points)} points beyond the entry trigger or the hourly trend reverses.</p>
      </div>}
      <p className="mt-3 text-sm text-slate-400">{r.reason}</p>
      {r.journal && <p className="mt-1 text-xs text-emerald-400">Trade journal saved · {r.journal.saved} model trade records checked this refresh</p>}
      {!r.trade && !r.setup && r.watch_levels && <div className="mt-3 grid grid-cols-2 gap-3 text-xs">
        {Object.values(r.watch_levels).map(p => <div key={p.side} className="rounded border border-slate-700 p-2"><p className="font-semibold">{p.side} watch only</p><p>Trigger {price(p.entry_trigger)}</p><p>Stop {price(p.initial_stop)}</p><p className="text-slate-500">Requires hourly trend confirmation.</p></div>)}
      </div>}
      {!r.trade && r.backtest && r.backtest.recent_trades.length > 0 && (() => {
        const last = r.backtest.recent_trades[r.backtest.recent_trades.length-1];
        return <div className="mt-3 rounded border border-slate-700 p-3 text-sm">
          <p className="font-semibold">Last completed model trade: {last.side}</p>
          <p>Entry {price(last.entry)} · Net {price(last.realized_net_points)} weighted points</p>
          <p className="text-xs text-slate-400">Opened {date(last.entry_at)} · Closed {last.exit_at ? date(last.exit_at) : "—"} · {last.exit_reason}</p>
        </div>;
      })()}
      {r.backtest && <details className="mt-4 text-sm"><summary className="cursor-pointer text-indigo-300">Recent completed trades & replay statistics ({r.backtest.closed_trades})</summary>
        <p className="mt-2 text-xs text-slate-400">Minute-data window: {date(r.backtest.period_start)} to {date(r.backtest.period_end)} · {r.backtest.execution_bars} execution candles</p>
        <p className="my-2 text-xs">Profitable complete trades: {r.backtest.profitable_rate == null ? "No closed trades" : `${r.backtest.profitable_rate}%`} · Net {r.backtest.net_r}R · Closed-trade drawdown {r.backtest.max_closed_trade_drawdown_r}R</p>
        <p className="mb-2 text-xs text-slate-400">{r.backtest.method} Excluded {r.backtest.unknown_data_gap_trades} trades with unknown outcomes due to missing data. Costs assume 4 bps round trip.</p>
        <div className="max-h-60 overflow-auto"><table className="w-full text-left text-xs"><thead><tr><th>Entry date</th><th>Side / entry</th><th>Exit</th><th>Net points</th></tr></thead>
          <tbody>{[...r.backtest.recent_trades].reverse().map(t => <tr key={t.id} className="border-t border-slate-800"><td className="py-2 pr-2">{date(t.entry_at)}</td><td>{t.side} {price(t.entry)}</td><td>{t.exit_reason}</td><td>{price(t.realized_net_points)}</td></tr>)}</tbody>
        </table></div>
      </details>}
      {r.trailing_rules && <details className="mt-3 text-xs text-slate-400"><summary className="cursor-pointer">Trailing rules</summary><ul className="mt-2 list-disc space-y-1 pl-4">{r.trailing_rules.map(rule => <li key={rule}>{rule}</li>)}</ul></details>}
    </article>)}</div>
    <p className="mt-4 text-xs text-slate-500">Index-point plans and a simulated model tracker, not broker orders. Yahoo candles can be delayed. Desired moves are planning references, not exits or forecasts. The full position stays open until stopped. Entries use the hourly chart; the EMA stop follows each completed {trailMinutes}-minute candle. This is a polled candle feed, not a broker tick stream.</p>
    <details className="mt-3"><summary className="cursor-pointer text-xs text-slate-500">Previous fixed-target research</summary><IndexExperimentResults /></details>
  </section>;
}
