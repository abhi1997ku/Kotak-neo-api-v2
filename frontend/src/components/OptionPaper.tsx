import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { API_BASE_URL } from "../services/api";
type Trade = { symbol: string; quantity: number; premium: number; exit_premium?: number; gross_pnl: number | null; mark?: number; mark_stale?: boolean; result: string; entered_at: string; exited_at?: string; stop: number; target: number; exit_reason?: string };
type State = { status: string; checked_at?: string; error?: string; tick_status?: string; last_tick_at?: string; execution_source?: string; execution_decisions?: Record<string, { setup: string; reason: string; at: string }>; position?: Trade; trades: Trade[]; total: number; summary: { closed?: number; wins?: number; losses?: number; win_rate?: number | null; gross_pnl?: number } };
const money = (n?: number | null) => n == null ? "—" : `₹${n.toFixed(2)}`;
const time = (s?: string) => s ? new Date(s).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" }) : "—";
export default function OptionPaper() {
  const [page, setPage] = useState(0);
  const query = useQuery<State>({ queryKey: ["option-paper", page], queryFn: async () => {
    const response = await fetch(`${API_BASE_URL}/paper-data/indstocks/option-paper?limit=20&offset=${page * 20}`);
    if (!response.ok) throw new Error("Paper journal unavailable");
    return response.json();
  }, refetchInterval: 3000 });
  const state = query.data;
  const stale = !state?.checked_at || Date.now()-new Date(state.checked_at).getTime() > 90000;
  const position = state?.position;
  return <section className="rounded border border-amber-700 p-4 space-y-3">
    <h2 className="text-xl font-semibold">Options paper simulation · estimates only</h2>
    <p>Two-strike-ITM CE/PE · 2 lots · Buy at displayed asks, sell at displayed bids · Broker orders disabled.</p>
    <p className="text-sm text-amber-200">Exchange quote time is unverified. Fills use recently received snapshots and displayed depth. P&amp;L excludes fees and extra slippage. The 2R target refers to the underlying, not option premium.</p>
    <p>Status: {query.isError ? query.error.message : stale ? "Waiting / stale" : state?.status} {state?.error}</p>
    <p className="text-sm">Execution feed: {stale ? 'Stale' : state?.tick_status ?? 'Awaiting stream'} · Last tick: {time(state?.last_tick_at)} · {state?.execution_source}</p>
    <details><summary>Entry decisions and data interruptions</summary>
      <p className="text-xs">Recorded from this update onward. Earlier unfilled setups have no execution audit.</p>
      {Object.values(state?.execution_decisions ?? {}).slice(-30).reverse().map(e => <p key={e.setup} className="text-sm">{e.setup} · {e.reason} · {time(e.at)}</p>)}
    </details>
    <p className="text-sm">New entries end at 15:00 IST; exits are requested at 15:15. All times IST.</p>
    <h3 className="font-semibold">Active option position</h3>
    {position ? <div className="rounded border border-blue-500 bg-slate-800 p-3 space-y-1">
      <p>{position.symbol} · Qty {position.quantity} · OPEN</p>
      <p>Entered {time(position.entered_at)} · Premium {money(position.premium)}</p>
      <p>Underlying stop {position.stop} · Underlying target {position.target}</p>
      <p>Bid mark {money(position.mark)} · Unrealized gross P&amp;L: {position.mark_stale || stale || query.isError ? "Stale / unavailable" : money(position.gross_pnl)}</p>
      {position.exit_reason && <p>Exit pending: {position.exit_reason}</p>}
    </div> : <p>{query.isError ? "Active position unavailable" : !state ? "Loading position…" : "No active paper position."}</p>}
    <h3 className="font-semibold">Trade history · saved in SQLite</h3>
    <p>Closed: {state?.summary?.closed ?? 0} · Wins: {state?.summary?.wins ?? 0} · Losses: {state?.summary?.losses ?? 0} · Win rate: {state?.summary?.win_rate == null ? "—" : `${state.summary.win_rate}%`} · Realized gross P&amp;L: {money(state?.summary?.gross_pnl)}</p>
    <div className="overflow-x-auto"><table className="w-full text-sm text-left"><thead><tr>{["Contract / Qty", "Entry (IST)", "Exit (IST)", "Buy → Sell", "Exit reason", "Gross P&L"].map(h => <th className="p-2" key={h}>{h}</th>)}</tr></thead>
      <tbody>{state?.trades.map(t => <tr className="border-t border-slate-700" key={`${t.entered_at}-${t.symbol}`}>
        <td className="p-2">{t.symbol} / {t.quantity}</td><td className="p-2">{time(t.entered_at)}</td><td className="p-2">{time(t.exited_at)}</td><td className="p-2">{money(t.premium)} → {money(t.exit_premium)}</td><td className="p-2">{t.exit_reason}</td><td className="p-2">{money(t.gross_pnl)}</td>
      </tr>)}</tbody></table></div>
    {state && !state.total && <p>No closed paper trades recorded.</p>}
    <div className="flex gap-4"><button disabled={!page} onClick={() => setPage(page-1)}>Previous</button><span>Page {page+1} · {state?.total ?? 0} closed trades</span><button disabled={(page+1)*20 >= (state?.total ?? 0)} onClick={() => setPage(page+1)}>Next</button></div>
    <p className="text-xs text-slate-400">Entry and exit quote evidence is retained in the local database. Summary covers all closed paper trades; open marks are excluded.</p>
  </section>;
}
