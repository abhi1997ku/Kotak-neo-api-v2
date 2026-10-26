import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { API_BASE_URL } from "../services/api";
import IndstocksIndexQuotes from "./IndstocksIndexQuotes";
import OptionPaper from "./OptionPaper";


type Signal = { timestamp: string; side: string; accepted: boolean; entry_price: number | null; stop_price: number | null; reason_codes: string[]; historical_observation: boolean };
type Report = { date: string; stale: boolean; accepted: number; rejected: number; signals: Signal[]; status: { state?: string; checked_at?: string; last_closed_bar?: string; contract?: string; today_bars?: number; error_type?: string } };
const time = (value?: string) => value ? new Date(value).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" }) : "-";

export default function OrderBlockPaperPanel() {
  const [live, setLive] = useState<{ state: string; price?: number; received?: string }>({ state: "Connecting" });
  useEffect(() => {
    let stopped = false;
    let outsideSession = false;
    let socket: WebSocket;
    let retry: ReturnType<typeof setTimeout>;
    const connect = () => {
      socket = new WebSocket(`${API_BASE_URL.replace(/^http/, "ws")}/paper-data/indstocks/stream`);
      socket.onmessage = event => {
        const message = JSON.parse(event.data);
        outsideSession = message.state === "Outside regular session";
        const price = message.raw?.data?.close ?? message.raw?.data?.ltp;
        setLive({ state: message.state, price: typeof price === "number" ? price : undefined, received: message.received_at });
      };
      socket.onclose = () => { if (!stopped) { if (!outsideSession) setLive({ state: "Disconnected  /  retrying" }); retry = setTimeout(connect, outsideSession ? 60000 : 5000); } };
      socket.onerror = () => socket.close();
    };
    connect();
    return () => { stopped = true; clearTimeout(retry); socket?.close(); };
  }, []);
  const [filter, setFilter] = useState("all");
  const query = useQuery<Report>({ queryKey: ["order-block-paper-monitor"], queryFn: async () => {
    const response = await fetch(`${API_BASE_URL}/paper-data/indstocks/monitor`);
    if (!response.ok) throw new Error("Paper monitor unavailable. Check the paper backend on port 8003.");
    return response.json();
  }, refetchInterval: 15000 });
  const report = query.data;
  const rows = report?.signals.filter(s => filter === "all" || (filter === "accepted" ? s.accepted : !s.accepted)) ?? [];
  return <section className="space-y-5 rounded-xl border border-slate-700 bg-slate-900 p-5 text-slate-100">
    <header className="flex flex-wrap items-center justify-between gap-3"><div><h1 className="text-2xl font-semibold">Order blocks / 5 minute  /  2R  /  BANKNIFTY</h1><p className="mt-1 text-sm text-slate-400">INDstocks  /  Signal observation  /  {report?.date ?? "Today"}  /  IST</p></div><button className="rounded border border-slate-600 px-4 py-2" onClick={() => query.refetch()} disabled={query.isFetching}>{query.isFetching ? "Refreshing..." : "Refresh"}</button></header>
    <IndstocksIndexQuotes />
    <p className="text-sm text-slate-400">Five confirming candles / Midpoint retest / 09:30-15:00 entries / Latest zone per side / Stop one tick beyond zone / 2R from actual underlying fill / 15:15 exit. Paper options only.</p>
    <div className="rounded-lg bg-slate-800 p-4"><p className="text-sm text-slate-400">INDstocks live BANKNIFTY futures  /  {live.state}</p><p className="text-2xl font-semibold">{live.price?.toLocaleString("en-IN", { minimumFractionDigits: 2 }) ?? "-"}</p><p className="text-xs text-slate-400">Received: {time(live.received)}  /  Signals use completed INDstocks candles.</p></div>
    <OptionPaper />
    {query.isError && <p role="alert" className="text-red-300">{query.error.message} Displayed data may be outdated.</p>}
    {query.isPending && <p>Loading saved observations...</p>}
    {report && <><div className="grid gap-3 sm:grid-cols-4">{[["Monitor", query.isError ? "Unavailable" : report.stale ? "Stale / stopped" : report.status.state === "error" ? "Error" : report.status.state === "outside_session" ? "Outside session" : "Updating"], ["Detected zones", report.accepted], ["Rejected detections", report.rejected], ["Closed bars today", report.status.today_bars ?? "-"]].map(([label, value]) => <div key={label} className="rounded-lg bg-slate-800 p-4"><p className="text-sm text-slate-400">{label}</p><p className="mt-1 text-xl font-semibold">{value}</p></div>)}</div>
    <div className="text-sm text-slate-400">Contract: {report.status.contract ?? "-"}<br/>Last check: {time(report.status.checked_at)}<br/>Last closed candle (open time): {report.status.last_closed_bar ?? "-"}{report.status.error_type && <p className="text-red-300">Monitor error: {report.status.error_type}</p>}</div>
    <label className="block text-sm">Show <select className="ml-2 rounded bg-slate-800 p-2" value={filter} onChange={e => setFilter(e.target.value)}><option value="all">All evaluations</option><option value="accepted">Accepted</option><option value="rejected">Rejected</option></select></label>
    <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="text-slate-400"><tr>{["Signal time (IST)", "Side", "Result", "Entry / stop", "Reasons"].map(h => <th key={h} className="p-3">{h}</th>)}</tr></thead><tbody>{rows.map(s => <tr className="border-t border-slate-700" key={`${s.timestamp}-${s.side}`}><td className="p-3 whitespace-nowrap">{time(s.timestamp)}{s.historical_observation && <span className="block text-xs text-slate-500">Historical observation</span>}</td><td className="p-3 uppercase">{s.side}</td><td className={`p-3 ${s.accepted ? "text-emerald-300" : "text-slate-400"}`}>{s.accepted ? "Setup recorded" : "Rejected"}</td><td className="p-3 whitespace-nowrap">{s.entry_price?.toFixed(2) ?? "-"} / {s.stop_price?.toFixed(2) ?? "-"}</td><td className="p-3">{s.reason_codes.join(", ")}</td></tr>)}</tbody></table>{rows.length === 0 && <p className="p-6 text-center text-slate-400">No matching setups recorded today.</p>}</div></>}
  </section>;
}

