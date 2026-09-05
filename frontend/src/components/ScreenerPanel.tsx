import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Play, RefreshCw } from "lucide-react";
import { apiClient, type ScreenerCandidate } from "../services/api";

export function ScreenerPanel() {
  const [mode, setMode] = useState<"synthetic" | "real">("synthetic");
  const [runMode, setRunMode] = useState<"synthetic" | "real" | null>(null);
  const [minRr, setMinRr] = useState(2);
  const { data, isFetching, error, refetch } = useQuery({
    queryKey: ["screener", runMode, minRr],
    queryFn: () => apiClient.runScreener(runMode || "synthetic", minRr),
    enabled: runMode !== null,
    staleTime: 0,
  });

  const runScan = () => {
    setRunMode(mode);
    void refetch();
  };

  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900 p-4">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm uppercase tracking-wide text-slate-400">Swing trade scanner</h2>
          <p className="mt-1 text-xs text-slate-500">Ranked setups only. No orders are placed automatically.</p>
        </div>
        <div className="flex items-center gap-2">
          <select value={mode} onChange={(event) => setMode(event.target.value as "synthetic" | "real")} className="rounded border border-slate-700 bg-slate-800 px-2 py-2 text-xs text-slate-200">
            <option value="synthetic">Synthetic test</option>
            <option value="real">Real market data</option>
          </select>
          <label className="text-xs text-slate-400">Min R:R
            <input type="number" min="0.5" step="0.5" value={minRr} onChange={(event) => setMinRr(Math.max(0.5, Number(event.target.value)))} className="ml-2 w-14 rounded border border-slate-700 bg-slate-800 px-2 py-2 text-slate-100" />
          </label>
          <button onClick={runScan} disabled={isFetching} className="flex items-center gap-1 rounded bg-blue-600 px-3 py-2 text-xs font-semibold text-white hover:bg-blue-500 disabled:opacity-60">
            {isFetching ? <RefreshCw className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />}
            Scan
          </button>
        </div>
      </div>

      {error && <div className="rounded bg-red-500/10 p-2 text-xs text-red-300">{error instanceof Error ? error.message : "Scanner failed"}</div>}
      {data && !data.candidates.length && <div className="text-center text-sm text-slate-500">No setups passed the selected filters.</div>}
      {data && data.candidates.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead><tr className="border-b border-slate-700 text-left text-slate-400"><th className="px-2 py-2">Symbol</th><th className="px-2 py-2">Setup</th><th className="px-2 py-2">Entry</th><th className="px-2 py-2">Stop</th><th className="px-2 py-2">Target</th><th className="px-2 py-2">R:R</th><th className="px-2 py-2">Qty</th></tr></thead>
            <tbody>{data.candidates.map((candidate: ScreenerCandidate) => <tr key={`${candidate.symbol}-${candidate.entry}`} className="border-b border-slate-800"><td className="px-2 py-2 font-semibold text-slate-200">{candidate.symbol}</td><td className="px-2 py-2 text-emerald-300">{candidate.setup_type}</td><td className="px-2 py-2">₹{candidate.entry.toFixed(2)}</td><td className="px-2 py-2 text-red-300">₹{candidate.stop.toFixed(2)}</td><td className="px-2 py-2 text-emerald-300">₹{candidate.target.toFixed(2)}</td><td className="px-2 py-2 font-semibold">{candidate.rr.toFixed(2)}</td><td className="px-2 py-2">{candidate.suggested_qty}</td></tr>)}</tbody>
          </table>
          <p className="mt-3 text-xs text-slate-500">{data.candidate_count} candidate(s) from {data.mode} data. Review the rationale in the scanner output before trading.</p>
        </div>
      )}
    </section>
  );
}
