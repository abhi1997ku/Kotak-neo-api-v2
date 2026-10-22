import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { managedTrades } from "../services/managedTrades";

const money = (value?: number | null) => value == null ? "—" : value.toFixed(2);

export function ManagedTradesPanel() {
  const cache = useQueryClient();
  const [open, setOpen] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
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
      <p className="text-xs text-amber-200">Local target and stop trigger real limit exits. Keep the backend and connection running. A triggered limit may not fill. One managed trade, one lot of bought Nifty/Bank Nifty options at a time.</p>
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
        {!["CLOSED", "CANCELLED", "RELEASED"].includes(row.state) && <div className="mt-2 flex gap-3">
          <button disabled={!!busy} className="text-xs text-sky-300 disabled:opacity-50" onClick={() => action(row.request_id, "reconcile")}>Reconcile / resume</button>
          {row.state === "ARMED" && <button disabled={!!busy} className="text-xs text-amber-300 disabled:opacity-50" onClick={() => action(row.request_id, "exit")}>Exit at stop limit</button>}
          <button disabled={!!busy} className="text-xs text-slate-400 disabled:opacity-50" onClick={() => action(row.request_id, "release")}>Release to manual</button>
        </div>}
      </article>)}
    </div>}
  </section>;
}
