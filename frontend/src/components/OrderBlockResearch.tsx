import { useQuery } from "@tanstack/react-query";
import { API_BASE_URL } from "../services/api";

type Report = { state: string; summary: Record<string, { bars: number; zones: number; accepted_setups: number; rejected_setups: number }>;
  reports?: Record<string, { as_of: string; latest?: { support: number[]; resistance: number[]; score: number; reason_codes: string[] };
    outcomes?: { timestamp: string; side: string; entry: number; result: string; points: number | null }[];
    zones: { timestamp: string; side: string; low: number; high: number; event: string }[];
    setups: { timestamp: string; side: string; entry: number; stop: number; target: number | null; accepted: boolean; reason_codes: string[] }[] }> };

export default function OrderBlockResearch() {
  const query = useQuery<Report>({ queryKey: ["order-block-research"], queryFn: async () => {
    const response = await fetch(`${API_BASE_URL}/paper-data/indstocks/order-block-research`);
    if (!response.ok) throw new Error("Order-block report unavailable");
    return response.json();
  } });
  return <section className="rounded-lg border border-slate-600 p-4 space-y-3">
    <h2 className="text-xl font-semibold">Order-block experiment · Saved-data replay</h2>
    <p className="text-sm text-amber-200">Historical price/volume analysis. OI and benchmark strength unavailable in this replay. No option fills or P&amp;L. This is not a live top-stocks board.</p>
    {query.isPending && <p>Loading replay…</p>}
    {query.isError && <p role="alert">{query.error.message}</p>}
    {query.data?.state === "not_run" && <p>No replay saved yet.</p>}
    {Object.entries(query.data?.summary ?? {}).map(([symbol, summary]) => <div key={symbol}>
      <p className="font-semibold">{symbol} · {summary.bars} candles · {summary.zones} zones</p>
      <p className="text-sm text-slate-400">{summary.accepted_setups} accepted setups · {summary.rejected_setups} rejected retests · Replay cutoff: {query.data?.reports?.[symbol]?.as_of}</p>
      {query.data?.reports?.[symbol]?.outcomes?.map((t, i) => <p className="rounded bg-slate-800 p-2 text-sm" key={`outcome-${i}`}>
        Underlying diagnostic: {new Date(t.timestamp).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })} · {t.side} entry {t.entry.toFixed(2)} · {t.result} · {t.points === null ? "No completed trade P&L" : `${t.points.toFixed(2)} gross futures points (not option P&L)`}
      </p>)}
      <p className="text-sm">Last-bar support: {query.data?.reports?.[symbol]?.latest?.support.map(x => x.toFixed(2)).join(", ") || "Unavailable"} · Resistance: {query.data?.reports?.[symbol]?.latest?.resistance.map(x => x.toFixed(2)).join(", ") || "Unavailable"}</p>
      <p className="text-sm">Last-bar price/volume score: {query.data?.reports?.[symbol]?.latest?.score} / 50 · Filters: {query.data?.reports?.[symbol]?.latest?.reason_codes.join(", ") || "Passed"}</p>
      <details><summary className="cursor-pointer">Recent zone events (potential liquidity areas)</summary>
        {query.data?.reports?.[symbol]?.zones.map((z, i) => <p className="text-sm text-slate-400" key={i}>{new Date(z.timestamp).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })} · {z.side} · {z.low.toFixed(2)}–{z.high.toFixed(2)} · {z.event}</p>)}
      </details>
      <div className="overflow-x-auto"><table className="w-full text-sm text-left"><thead><tr>{["Time (IST)", "Side", "Entry / stop / target", "Decision"].map(x => <th className="p-2" key={x}>{x}</th>)}</tr></thead>
        <tbody>{query.data?.reports?.[symbol]?.setups.map((s, i) => <tr className="border-t border-slate-700" key={`${s.timestamp}-${i}`}>
          <td className="p-2">{new Date(s.timestamp).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })}</td><td className="p-2">{s.side}</td>
          <td className="p-2">{s.entry.toFixed(2)} / {s.stop.toFixed(2)} / {s.target?.toFixed(2) ?? "—"}</td>
          <td className="p-2">{s.accepted ? "Setup only · not filled" : s.reason_codes.join(", ")}</td></tr>)}</tbody></table></div>
    </div>)}
  </section>;
}
