import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { managedTrades } from "../services/managedTrades";

const money = (value?: number | null) => value == null ? "—" : value.toFixed(2);

export function ManagedTradesPanel() {
  const cache = useQueryClient();
  const [open, setOpen] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<{ id: string; revision: number; target_trigger: string; target_limit: string; stop_trigger: string; stop_limit: string } | null>(null);
  async function savePrices(event: React.FormEvent) {
    event.preventDefault();
    if (!editing || busy) return;
    setBusy(editing.id); setError("");
    try {
      await managedTrades.edit(editing.id, { revision: editing.revision, target_trigger: Number(editing.target_trigger), target_limit: Number(editing.target_limit), stop_trigger: Number(editing.stop_trigger), stop_limit: Number(editing.stop_limit) });
      setEditing(null);
      await cache.invalidateQueries({ queryKey: ["managed-trades"] });
    } catch (err) { setError(err instanceof Error ? err.message : "Update failed."); }
    finally { setBusy(""); }
  }
  const query = useQuery({ queryKey: ["managed-trades"], queryFn: managedTrades.list, enabled: open, refetchInterval: open ? 1000 : false, retry: false });
  async function action(id: string, name: "reconcile" | "exit" | "release") {
    if (name === "release" && !window.confirm("Stop local management? Any remaining real position will have no managed target or stop. Pending broker orders must be resolved first.")) return;
    if (name === "exit" && !window.confirm("Submit a real limit exit at this trade's configured stop limit? It may remain unfilled.")) return;
    setBusy(id); setError("");
    try { await managedTrades.action(id, name); await cache.invalidateQueries({ queryKey: ["managed-trades"] }); }
    catch (err) { setError(err instanceof Error ? err.message : "Action failed."); }
    finally { setBusy(""); }
  }
  return <section className="rounded-lg border border-slate-800 bg-slate-900 p-4">
    <button type="button" className="flex w-full items-center justify-between text-left font-semibold" aria-expanded={open} onClick={() => setOpen(!open)}>
      <span>Managed Trades <span className="ml-2 text-xs text-red-300">Live · Real orders</span></span><span>{open ? "−" : "+"}</span>
    </button>
    {open && <div className="mt-3 space-y-3">
      <p className="text-xs text-amber-200">Local target and stop trigger real limit exits. Keep the backend and connection running. A triggered limit may not fill. One managed trade at a time; multiple whole lots of bought Nifty/Bank Nifty/Sensex options are supported, subject to broker order limits.</p>
      <p className="text-xs text-slate-400">Price feed: {query.data?.feed_state || "Loading"} · Order feed: {query.data?.order_feed_state || "Loading"}. Backend monitoring continues while this panel is closed.</p>
      {query.data?.error && <p role="alert" className="text-sm text-red-300">{query.data.error}</p>}
      {(error || query.error) && <p role="alert" className="text-sm text-red-300">{error || String(query.error)}</p>}
      {query.isLoading && <p className="text-sm text-slate-400">Loading managed orders…</p>}
      {query.data?.trades.length === 0 && <p className="text-sm text-slate-400">No managed trades yet. Enable “Live target & stop” in a buy option ticket.</p>}
      {query.data?.trades.map(row => <article key={row.request_id} className="rounded border border-slate-700 p-3 text-sm">
        <div className="flex flex-wrap justify-between gap-2"><strong>{row.symbol} · {row.side} {row.quantity} units</strong><span className="text-amber-200">{row.state.replace(/_/g, " ")}</span></div>
        <div className="mt-2 grid grid-cols-2 gap-2 text-xs text-slate-300 md:grid-cols-4">
          <span>Premium: {money(row.ltp)} · {row.tick_age_seconds == null ? "Waiting for tick" : row.tick_age_seconds > 2 ? `Stale (${row.tick_age_seconds}s)` : `${row.tick_age_seconds}s ago`}</span>
          <span>Entry: {money(row.entry_price)} · Exit: {money(row.exit_price)}</span>
          <span>Profit trigger / limit: {money(row.target_trigger)} / {money(row.target_limit)}</span>
          <span>Stop trigger / limit: {money(row.stop_trigger)} / {money(row.stop_limit)}</span>
        </div>
        <p className="mt-2 text-xs text-slate-400">{row.note}</p>
        <p className="mt-1 text-xs text-slate-400">Entry {row.entry_id || "Awaiting ID"}: {row.entry_filled}/{row.quantity} filled · Exit {row.exit_id || "Not submitted"}: {row.exit_filled} filled</p>
        {row.trigger_to_ack_ms != null && <p className="mt-1 text-xs text-slate-400">Trigger to broker acknowledgement: {row.trigger_to_ack_ms} ms (not fill time)</p>}
        {row.selected_leg && <p className="mt-1 text-xs text-amber-200">Selected: {row.selected_leg}. Other trigger disabled.</p>}
        {editing?.id === row.request_id && <form onSubmit={savePrices} className="mt-3 space-y-2 rounded border border-slate-600 p-3">
          <div className="grid grid-cols-2 gap-2">
            {([['target_trigger', 'Profit trigger'], ['target_limit', 'Profit limit'], ['stop_trigger', 'Stop trigger'], ['stop_limit', 'Stop limit']] as const).map(([key, label]) => <label key={key} className="text-xs text-slate-400">{label}<input required disabled={!!busy} type="number" min="0.05" step="0.05" value={editing[key]} onChange={event => setEditing({ ...editing, [key]: event.target.value })} className="mt-1 w-full rounded border border-slate-600 bg-slate-800 p-2 text-slate-100" /></label>)}
          </div>
          <p className="text-xs text-slate-400">Limits must be at or below their triggers. Set stop below current premium and profit above it. Existing protection stays active until saved. Exit quantity follows actual entry fills; this edit does not buy or sell additional units.</p>
          <button disabled={!!busy} className="mr-4 text-xs text-emerald-300 disabled:opacity-50" type="submit">{busy ? "Saving…" : "Save profit & stop"}</button>
          <button disabled={!!busy} className="text-xs text-slate-400" type="button" onClick={() => setEditing(null)}>Cancel edit</button>
        </form>}
        {!["CLOSED", "CANCELLED", "RELEASED"].includes(row.state) && <div className="mt-2 flex gap-3">
          {["WAITING_ENTRY", "ARMED"].includes(row.state) && !row.selected_leg && <button disabled={!!busy} className="text-xs text-emerald-300 disabled:opacity-50" onClick={() => { setError(""); setEditing({ id: row.request_id, revision: row.edit_revision || 0, target_trigger: String(row.target_trigger), target_limit: String(row.target_limit), stop_trigger: String(row.stop_trigger), stop_limit: String(row.stop_limit) }); }}>Edit profit & stop</button>}
          <button disabled={!!busy} className="text-xs text-sky-300 disabled:opacity-50" onClick={() => action(row.request_id, "reconcile")}>Reconcile / resume</button>
          {row.state === "ARMED" && <button disabled={!!busy} className="text-xs text-amber-300 disabled:opacity-50" onClick={() => action(row.request_id, "exit")}>Exit at stop limit</button>}
          <button disabled={!!busy} className="text-xs text-slate-400 disabled:opacity-50" onClick={() => action(row.request_id, "release")}>Release to manual</button>
        </div>}
      </article>)}
    </div>}
  </section>;
}
