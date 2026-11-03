import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { API_BASE_URL } from "../services/api";

type Row = { symbol: string; side: string; score: number; relative_volume: number; relative_strength: number; close: number; reason: string };
type Snapshot = { day: string; slot: string; historical_reconstruction: boolean; rows: Row[]; status: string; universe_count: number };
type Signal = { symbol: string; side: string; timestamp: string; entry: number; stop: number; target: number; execution_status: string; valid_until: string };
type Trade = {id: string; symbol: string; side: string; entry: number; quantity: number; stop: number; target: number; mark: number; status: string; net_pnl: number | null; entry_at: string; exit_reason?: string};
type Report = { state: string; message: string; snapshots: Snapshot[]; provisional?: Row[]; signals?: Signal[]; as_of?: string; account?: {equity: number; halted: boolean; trades: Trade[]; orders: {id: string; symbol: string; status: string; reason?: string}[]}; worker?: {state: string; checked_at?: string; error_type?: string}; reconstruction?: { date: string; snapshots: Record<string, { rows: Row[] }> } };

export default function IntradayStocksPanel() {
  const [result, setResult] = useState<Report>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const query = useQuery<Report>({ queryKey: ["intraday-stocks"], queryFn: async () => {
    const response = await fetch(`${API_BASE_URL}/paper/intraday-stocks`);
    if (!response.ok) throw new Error("Paper service unavailable");
    return response.json();
  }, refetchInterval: 10000 });
  const data = query.data ? {...query.data, provisional: result?.provisional, as_of: result?.as_of} : result;
  async function scan() {
    setBusy(true); setError("");
    try {
      const response = await fetch(`${API_BASE_URL}/paper/intraday-stocks/scan`, { method: "POST" });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "Scan failed");
      setResult(body);
    } catch (e) { setError(e instanceof Error ? e.message : "Scan failed"); }
    finally { setBusy(false); }
  }
  const table = (rows: Row[]) => <div className="overflow-auto"><table className="w-full text-left text-sm"><thead><tr>{["Stock", "Side", "Score", "Price", "Same-time RVOL", "Relative strength %", "Reason"].map(x => <th key={x} className="p-2">{x}</th>)}</tr></thead><tbody>{rows.map(r => <tr key={`${r.symbol}-${r.side}`} className="border-t border-slate-700"><td className="p-2">{r.symbol}</td><td>{r.side}</td><td>{r.score}</td><td>{r.close.toFixed(2)}</td><td>{r.relative_volume}×</td><td>{r.relative_strength}</td><td>{r.reason}</td></tr>)}</tbody></table>{!rows.length && <p className="p-3">NO TRADE — no qualifying stocks.</p>}</div>;
  return <section className="space-y-5 rounded-xl bg-slate-900 p-5 text-slate-100">
    <h1 className="text-2xl font-semibold">Intraday stock opportunities · Paper research</h1>
    <p>Up to 3 longs and 3 shorts. Main shortlist 09:45 IST · Separate refresh 11:00 · Entries end 14:30.</p>
    <p className="text-amber-300">Paper simulation only. No broker orders. Candle-based fills include modeled slippage and costs; they are not exchange executions.</p>
    <p>Worker: {data?.worker?.state ?? "STOPPED"} · Last check: {data?.worker?.checked_at ?? "—"} {data?.worker?.error_type}</p>
    <p>Risk: 0.25% per trade · 2 open positions · 3 entries/day · 1% daily loss cutoff · 15:15 exit · No leverage</p>
    <p>Paper equity: ₹{data?.account?.equity?.toFixed(2) ?? "100000.00"} · {data?.account?.halted ? "Daily loss limit reached" : "Risk checks enabled"}</p>
    <h2 className="text-lg font-semibold">Paper positions and trade history</h2>
    <div className="overflow-auto"><table className="w-full text-left text-sm"><thead><tr>{["Stock", "Side", "Status", "Entry time", "Entry", "Qty", "Stop", "Target", "Mark", "Net P&L", "Exit reason"].map(x=><th key={x} className="p-2">{x}</th>)}</tr></thead><tbody>{data?.account?.trades.slice().reverse().map(t=><tr key={t.id} className="border-t border-slate-700"><td>{t.symbol}</td><td>{t.side}</td><td>{t.status}</td><td>{t.entry_at}</td><td>{t.entry.toFixed(2)}</td><td>{t.quantity}</td><td>{t.stop.toFixed(2)}</td><td>{t.target.toFixed(2)}</td><td>{t.mark.toFixed(2)}</td><td>{t.net_pnl?.toFixed(2) ?? "Unrealized"}</td><td>{t.exit_reason ?? "—"}</td></tr>)}</tbody></table></div>
    {!data?.account?.trades.length && <p>No paper fills recorded. A shortlist alone does not open a position.</p>}
    <details><summary>Pending and skipped entries</summary>{data?.account?.orders.slice(-100).reverse().map(o=><p key={o.id}>{o.symbol} · {o.status} · {o.reason}</p>)}</details>
    <p>{data?.state ?? "Loading…"} — {data?.message}</p>
    <button disabled={busy} onClick={scan} className="rounded bg-emerald-700 px-4 py-2 disabled:opacity-50">{busy ? "Evaluating…" : "Evaluate cached candles"}</button>
    {(error || query.isError) && <p role="alert" className="text-red-300">{error || query.error?.message} Previous results may be stale.</p>}
    {data?.provisional && <div><h2>Current provisional ranking · {data.as_of}</h2>{table(data.provisional)}</div>}
    <h2 className="text-lg font-semibold">Recorded shortlists</h2>
    {data?.reconstruction && <div className="space-y-3"><p className="text-amber-300">Historical reconstruction · {data.reconstruction.date} · Corrected scoring · Not live entry recommendations</p>{Object.entries(data.reconstruction.snapshots).map(([slot, snapshot]) => <article key={slot}><h3>{slot} IST</h3>{table(snapshot.rows)}</article>)}</div>}
    {!data?.snapshots.length && <p>No shortlist recorded. Verified intraday stock history is required.</p>}
    {data?.snapshots.map(s => <article key={`${s.day}-${s.slot}`} className="rounded border border-slate-700 p-3"><h3>{s.day} · {s.slot} IST · {s.universe_count} stocks · {s.status}</h3>{s.historical_reconstruction && <p className="text-amber-300">Historical reconstruction — not a timely trade recommendation</p>}{table(s.rows)}</article>)}
    <h2 className="text-lg font-semibold">Confirmed order-block entry signals</h2>
    {data?.signals?.map(s => <p key={`${s.symbol}-${s.timestamp}-${s.side}`}>{s.symbol} {s.side} · {s.timestamp} · Trigger {s.entry.toFixed(2)} · Stop {s.stop.toFixed(2)} · Reference target {s.target.toFixed(2)} · {s.execution_status} · {new Date(s.valid_until).getTime() <= Date.now() ? "Expired observation" : "Awaiting trigger"}</p>)}
    {!data?.signals?.length && <p>No confirmed entry signals.</p>}
  </section>;
}
