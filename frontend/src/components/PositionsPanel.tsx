import { useQuery } from "@tanstack/react-query";
import { apiClient, type Position } from "../services/api";
import { TrendingUp, TrendingDown } from "lucide-react";

export function PositionsPanel() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["positions"],
    queryFn: () => apiClient.getPositions(),
    refetchInterval: 5000, // Refetch every 5 seconds
  });

  if (isLoading) {
    return (
      <div className="rounded-xl border border-slate-800 bg-slate-900 p-4">
        <h3 className="mb-3 text-sm uppercase tracking-wide text-slate-400">Positions</h3>
        <div className="text-center text-sm text-slate-500">Loading...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-xl border border-slate-800 bg-slate-900 p-4">
        <h3 className="mb-3 text-sm uppercase tracking-wide text-slate-400">Positions</h3>
        <div className="rounded bg-red-500/10 p-2 text-xs text-red-300">
          {error instanceof Error ? error.message : "Error loading positions"}
        </div>
      </div>
    );
  }

  const positions = data?.positions || [];

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900 p-4">
      <h3 className="mb-3 text-sm uppercase tracking-wide text-slate-400">Positions</h3>
      {positions.length === 0 ? (
        <div className="text-center text-sm text-slate-500">No open positions</div>
      ) : (
        <div className="space-y-2">
          {positions.map((pos: Position) => (
            <div key={pos.symbol} className="flex items-center justify-between rounded-lg border border-slate-700 bg-slate-800 p-3 text-sm">
              <div>
                <p className="font-medium">{pos.symbol}</p>
                <p className="text-xs text-slate-400">{pos.qty} units @ ₹{pos.avg_price.toFixed(2)}</p>
              </div>
              <div className="text-right">
                <div className={`flex items-center gap-1 font-semibold ${pos.pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                  {pos.pnl >= 0 ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
                  ₹{Math.abs(pos.pnl).toFixed(2)}
                </div>
                <p className={`text-xs ${pos.pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>{pos.pnl_pct > 0 ? "+" : ""}
                  {pos.pnl_pct.toFixed(2)}%
                </p>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
