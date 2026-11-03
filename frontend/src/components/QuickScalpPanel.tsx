import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../services/api";
import { openScalpFills } from "../services/openScalpFills";

interface ContractRow {
  strike_price: number;
  call_symbol?: string; call_token?: string; call_lot_size?: number; call_ltp?: number;
  put_symbol?: string; put_token?: string; put_lot_size?: number; put_ltp?: number;
}

export function QuickScalpPanel({ chains, symbol, expiry, ready }: { chains: ContractRow[]; symbol: string; expiry: string; ready: boolean }) {
  const cache = useQueryClient();
  const lock = useRef(false);
  const [enabled, setEnabled] = useState(false);
  const [busy, setBusy] = useState("");
  const [result, setResult] = useState("");
  const [error, setError] = useState("");
  const [submitted, setSubmitted] = useState<{ id: string; symbol: string; side: string; quantity: number }[]>([]);
  const positions = useQuery({ queryKey: ["positions"], queryFn: () => apiClient.getPositions(), refetchInterval: 2000 });
  const fills = useQuery({ queryKey: ["trade-book"], queryFn: () => apiClient.getTradeBook(), refetchInterval: 2000 });
  const [product, setProduct] = useState("NRML");
  const [orderType, setOrderType] = useState<"MKT" | "L">("MKT");
  const [legs, setLegs] = useState({ call: { token: "", lots: "1", price: "" }, put: { token: "", lots: "1", price: "" } });
  const trackedSymbols = new Set(submitted.map(item => item.symbol));
  for (const leg of ["call", "put"] as const) {
    const selected = chains.find(item => item[`${leg}_token`] === legs[leg].token);
    if (selected?.[`${leg}_symbol`]) trackedSymbols.add(selected[`${leg}_symbol`]!);
  }
  const openPositions = (positions.data?.positions || []).filter(position =>
    position.qty !== 0 && trackedSymbols.has(position.symbol)
    && (!position.exchange_segment || position.exchange_segment === (symbol === "SENSEX" ? "bse_fo" : "nse_fo")));
  function change(leg: "call" | "put", values: Partial<typeof legs.call>) {
    if (lock.current) return;
    setLegs(current => ({ ...current, [leg]: { ...current[leg], ...values } }));
  }
  async function submit(leg: "call" | "put", side: "BUY" | "SELL") {
    if (lock.current || !enabled || !ready) return;
    const config = legs[leg];
    const row = chains.find(item => item[`${leg}_token`] === config.token);
    const lot = Number(row?.[`${leg}_lot_size`]);
    const lots = Number(config.lots);
    const quantity = lot * lots;
    const price = Number(config.price);
    if (!row?.[`${leg}_symbol`] || !config.token || !Number.isInteger(lot) || lot <= 0 || !Number.isInteger(lots) || lots <= 0 || !Number.isSafeInteger(quantity)) {
      setError("Select a contract with a valid lot size and enter a positive whole number of lots."); return;
    }
    if (orderType === "L" && (!Number.isFinite(price) || price <= 0 || Math.abs(price * 20 - Math.round(price * 20)) > 0.000001)) {
      setError("Enter a positive limit price in steps of 0.05."); return;
    }
    lock.current = true; setBusy(`${leg}-${side}`); setError(""); setResult("");
    const started = performance.now();
    try {
      const ack = await apiClient.placeOrder({ exchange_segment: symbol === "SENSEX" ? "bse_fo" : "nse_fo",
        trading_symbol: row[`${leg}_symbol`], scrip_token: config.token, transaction_type: side,
        product, quantity, order_type: orderType, price: orderType === "L" ? price : undefined,
        validity: "DAY", amo: "NO", tag: "quick-scalp" });
      const elapsed = Math.round(performance.now() - started);
      cache.setQueryData(["last-order-ack"], { ...ack, total_ms: elapsed });
      setSubmitted(current => [{ id: ack.order_id, symbol: row[`${leg}_symbol`]!, side, quantity }, ...current.filter(item => item.symbol !== row[`${leg}_symbol`])]);
      setResult(`${side} ${quantity} units · ${row[`${leg}_symbol`]} · Order ${ack.order_id} acknowledged in ${elapsed} ms. Check Order Book for fills.`);
      for (const key of ["orders", "positions", "trade-book"]) void cache.invalidateQueries({ queryKey: [key] });
    } catch (err) {
      setEnabled(false);
      setError(`${err instanceof Error ? err.message : "Submission failed."} One-click orders disabled. Check Order Book before enabling again; no automatic retry was sent.`);
    } finally { lock.current = false; setBusy(""); }
  }
  return <section className="mb-5 rounded-lg border border-sky-800 bg-slate-950 p-3" aria-label="Quick Scalp">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><h3 className="font-semibold text-sky-200">Quick Scalp · {symbol}</h3><p className="text-xs text-slate-400">Expiry: {expiry}. Direct real orders; no order popup.</p></div>
      <label className="text-sm text-amber-200"><input type="checkbox" checked={enabled} disabled={!!busy || !ready} onChange={e => setEnabled(e.target.checked)} /> Enable one-click orders</label>
    </div>
    <fieldset disabled={!!busy} className="mt-3 flex gap-3 text-sm">
      <label>Product <select aria-label="Scalp product" value={product} onChange={e => { setProduct(e.target.value); setEnabled(false); }} className="rounded bg-slate-800 p-2"><option>NRML</option><option>MIS</option></select></label>
      <label>Order type <select aria-label="Scalp order type" value={orderType} onChange={e => { setOrderType(e.target.value as "MKT" | "L"); setEnabled(false); }} className="rounded bg-slate-800 p-2"><option value="MKT">Market</option><option value="L">Limit</option></select></label>
    </fieldset>
    <div className="mt-3 grid gap-3 md:grid-cols-2">
      {(["call", "put"] as const).map(leg => {
        const row = chains.find(item => item[`${leg}_token`] === legs[leg].token);
        const lot = Number(row?.[`${leg}_lot_size`]);
        const premium = Number(row?.[`${leg}_ltp`]);
        const label = leg === "call" ? "CE" : "PE";
        return <fieldset key={leg} disabled={!!busy} className="space-y-2 rounded border border-slate-700 p-3">
          <legend className="px-1 font-semibold">{label}</legend>
          <select aria-label={`${label} contract`} value={legs[leg].token} onChange={e => {
            const chosen = chains.find(item => item[`${leg}_token`] === e.target.value);
            change(leg, { token: e.target.value, price: Number(chosen?.[`${leg}_ltp`]) > 0 ? Number(chosen?.[`${leg}_ltp`]).toFixed(2) : "" }); setEnabled(false);
          }} className="w-full rounded bg-slate-800 p-2"><option value="">Select {label} strike</option>
            {chains.filter(item => item[`${leg}_token`] && item[`${leg}_symbol`]).map(item => <option key={item[`${leg}_token`]} value={item[`${leg}_token`]}>{item.strike_price} {label} · {item[`${leg}_symbol`]}</option>)}
          </select>
          <p className="text-xs text-slate-400">LTP: {premium > 0 ? premium.toFixed(2) : "—"} · Lot size: {lot > 0 ? lot : "—"} · Quantity: {lot > 0 ? lot * Number(legs[leg].lots) : "—"}</p>
          <div className="flex gap-3"><label className="text-xs">Lots<input aria-label={`${label} lots`} type="number" min="1" step="1" value={legs[leg].lots} onChange={e => change(leg, { lots: e.target.value })} className="mt-1 w-full rounded bg-slate-800 p-2" /></label>
            {orderType === "L" && <label className="text-xs">Limit price<input aria-label={`${label} limit price`} type="number" min="0.05" step="0.05" value={legs[leg].price} onChange={e => change(leg, { price: e.target.value })} className="mt-1 w-full rounded bg-slate-800 p-2" /></label>}
          </div>
          <div className="grid grid-cols-2 gap-2">{(["BUY", "SELL"] as const).map(side => <button key={side} type="button" disabled={!enabled || !ready || !row || !!busy} onClick={() => void submit(leg, side)} className={`rounded py-3 font-bold disabled:opacity-40 ${side === "BUY" ? "bg-emerald-600" : "bg-red-600"}`}>{busy === `${leg}-${side}` ? "Sending…" : `${side} ${label}`}</button>)}</div>
        </fieldset>;
      })}
    </div>
    <p className="mt-2 text-xs text-amber-200">Sell can open a short position if you do not hold sufficient quantity. These orders have no automatic target/stop; attach protection from Positions after buying.</p>
    {!ready && <p className="mt-2 text-xs text-amber-200">Waiting for the option price feed and current contract data.</p>}
    {result && <p role="status" className="mt-2 text-sm text-emerald-300">{result}</p>}
    <div className="mt-3 space-y-2 border-t border-slate-700 pt-3">
      <h4 className="text-sm font-semibold">Open scalp positions</h4>
      {positions.isError && <p role="alert" className="text-xs text-amber-200">Position refresh failed. Displayed positions may be outdated.</p>}
      {!positions.isError && openPositions.length === 0 && <p className="text-xs text-slate-400">{positions.isLoading ? "Loading positions…" : "No open positions for the selected or recently traded contracts."}</p>}
      {openPositions.map(position => {
        const entries = fills.isError || !fills.data ? null : openScalpFills(position, fills.data.trades);
        return <div key={`${position.symbol}-${position.product}-${position.exchange_segment}`} className="flex flex-wrap justify-between gap-2 rounded bg-slate-900 p-2 text-xs">
          <span>{position.qty > 0 ? "LONG" : "SHORT"} · {position.symbol}<span className="block text-slate-500">{position.product}</span></span>
          <span>Open quantity: {Math.abs(position.order_quantity ?? position.qty)}</span>
          <div className="text-emerald-300">{entries ? entries.map((entry, index) => <div key={`${entry.orderId}-${index}`}><strong>Entry fill: ₹{entry.price.toFixed(2)}</strong> · {entry.quantity} units remaining <span className="text-slate-500">(order {entry.orderId})</span></div>) : <span className="text-amber-200">Waiting for matching execution history; day average is not used.</span>}</div>
        </div>;
      })}
      <p className="text-xs text-slate-500">Only remaining entry fills are shown. Partial exits consume earliest fills first (FIFO); closed trades are excluded.</p>
    </div>
    {error && <p role="alert" className="mt-2 text-sm text-red-300">{error}</p>}
  </section>;
}
