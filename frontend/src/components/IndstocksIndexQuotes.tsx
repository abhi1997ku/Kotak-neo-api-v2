import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { API_BASE_URL } from "../services/api";

type Quote = { live_price?: number; day_change?: number; day_change_percentage?: number; day_high?: number; day_low?: number; day_open?: number; prev_close?: number };
type Snapshot = { data: Record<string, Quote>; received_at: string; outside_regular_session: boolean };
type Tick = { price: number; received_at: string };
const number = (v?: number) => typeof v === "number" && Number.isFinite(v) ? v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—";

export default function IndstocksIndexQuotes() {
  const [ticks, setTicks] = useState<Record<string, Tick>>({});
  const [connection, setConnection] = useState("Connecting");
  const [clock, setClock] = useState(Date.now());
  const query = useQuery<Snapshot>({ queryKey: ["indstocks-index-reference"], queryFn: async () => {
    const response = await fetch(`${API_BASE_URL}/paper-data/indstocks/indices`);
    if (!response.ok) throw new Error("Reference snapshot unavailable");
    return response.json();
  }, retry: false, staleTime: Infinity, refetchOnWindowFocus: false });
  useEffect(() => {
    let stopped = false;
    let socket: WebSocket;
    let retry: ReturnType<typeof setTimeout>;
    const connect = () => {
      setConnection("Connecting");
      socket = new WebSocket(`${API_BASE_URL.replace(/^http/, "ws")}/paper-data/indstocks/indices-stream`);
      socket.onopen = () => setConnection("Connected · waiting for ticks");
      socket.onmessage = event => {
        try {
          const message = JSON.parse(event.data);
          if (message.state === "tick" && ["NIDX_40000001", "NIDX_40000003"].includes(message.code) && Number.isFinite(message.price)) {
            setTicks(old => ({ ...old, [message.code]: { price: message.price, received_at: message.received_at } }));
            setConnection("Receiving ticks");
          } else if (message.state === "disconnected") setConnection("Disconnected · reconnecting");
        } catch { setConnection("Invalid update"); }
      };
      socket.onclose = () => { if (!stopped) { setConnection("Disconnected · retrying in 30 seconds"); retry = setTimeout(connect, 30000); } };
      socket.onerror = () => socket.close();
    };
    connect();
    const timer = setInterval(() => setClock(Date.now()), 1000);
    return () => { stopped = true; clearTimeout(retry); clearInterval(timer); socket?.close(); };
  }, []);
  return <section aria-label="INDstocks index quotes" className="space-y-2">
    <div className="flex items-center justify-between text-sm text-slate-400"><span>Spot indices · INDstocks LTP stream · {connection}</span><button className="rounded border border-slate-600 px-3 py-1" onClick={() => query.refetch()} disabled={query.isFetching}>Refresh reference snapshot</button></div>
    <p className="text-xs text-slate-400">Prices update on received ticks. No five-second price polling. On holidays or outside market hours, values may be from the last session.</p>
    {query.isError && <p className="text-amber-300 text-sm">{query.error.message}; streaming prices can still update.</p>}
    <div className="grid gap-4 md:grid-cols-2">{[["NIFTY 50", "NIDX_40000001"], ["BANKNIFTY", "NIDX_40000003"]].map(([name, code]) => {
      const q = query.data?.data[code];
      const tick = ticks[code];
      const fresh = !!tick && connection === "Receiving ticks" && clock-new Date(tick.received_at).getTime() < 15000;
      const price = tick?.price ?? q?.live_price;
      const change = price !== undefined && q?.prev_close ? price-q.prev_close : undefined;
      const percent = change !== undefined && q?.prev_close ? 100*change/q.prev_close : undefined;
      return <article key={code} className="rounded-xl border border-slate-700 bg-slate-800 p-4">
        <h2 className="text-sm text-slate-300">{name} Spot · {fresh ? "Streaming" : tick ? "Stale / no recent tick" : "Snapshot / awaiting ticks"}</h2>
        <p className="mt-2 text-3xl font-semibold">{number(price)}</p>
        <p className={change === undefined || change === 0 ? "text-slate-300" : change > 0 ? "text-emerald-300" : "text-red-300"}>{number(change)} ({number(percent)}%)</p>
        <p className="text-xs text-slate-400 mt-2">Previous close: {number(q?.prev_close)} · Reference snapshot: {query.data ? new Date(query.data.received_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" }) : "—"}</p>
        <p className="text-xs text-slate-400">Last tick received: {tick ? new Date(tick.received_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" }) : "None"}</p>
      </article>;
    })}</div>
    <p className="text-xs text-slate-500">Streaming indicates receipt of provider updates; exchange quote time remains unverified.</p>
  </section>;
}
