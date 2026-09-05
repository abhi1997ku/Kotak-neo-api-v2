import { useQuery } from "@tanstack/react-query";
import { apiClient, type MarginData } from "../services/api";

export function MarginPanel() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["margin"],
    queryFn: () => apiClient.getMargin(),
    refetchInterval: 5000, // Refetch every 5 seconds
  });

  if (isLoading) {
    return (
      <div className="rounded-lg border border-slate-700 bg-slate-800 p-3">
        <p className="text-xs text-slate-400">Loading...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-lg border border-red-500/50 bg-red-500/10 p-3">
        <p className="text-xs text-red-300">
          {error instanceof Error ? error.message : "Error loading margin"}
        </p>
      </div>
    );
  }

  const margin = data as MarginData | undefined;

  if (!margin) {
    return (
      <div className="rounded-lg border border-slate-700 bg-slate-800 p-3">
        <p className="text-xs text-slate-400">No margin data</p>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
      <div className="rounded-lg border border-slate-700 bg-slate-800 p-3">
        <p className="text-xs text-slate-400">Available</p>
        <p className="mt-1 text-sm font-semibold text-emerald-400">₹{margin.available?.toFixed(2) || "0.00"}</p>
      </div>
      <div className="rounded-lg border border-slate-700 bg-slate-800 p-3">
        <p className="text-xs text-slate-400">Utilised</p>
        <p className="mt-1 text-sm font-semibold text-red-400">₹{margin.utilised?.toFixed(2) || "0.00"}</p>
      </div>
      <div className="rounded-lg border border-slate-700 bg-slate-800 p-3">
        <p className="text-xs text-slate-400">Gross Margin</p>
        <p className="mt-1 text-sm font-semibold text-slate-200">₹{margin.gross?.toFixed(2) || "0.00"}</p>
      </div>
      <div className="rounded-lg border border-slate-700 bg-slate-800 p-3">
        <p className="text-xs text-slate-400">P&L</p>
        <p className={`mt-1 text-sm font-semibold ${(margin.pnl || 0) >= 0 ? "text-emerald-400" : "text-red-400"}`}>
          ₹{margin.pnl?.toFixed(2) || "0.00"}
        </p>
      </div>
    </div>
  );
}
