import { useQuery } from "@tanstack/react-query";
import { apiClient, type Holding } from "../services/api";

export function HoldingsPanel() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["holdings"],
    queryFn: () => apiClient.getHoldings(),
    refetchInterval: 10000, // Refetch every 10 seconds
  });

  if (isLoading) {
    return (
      <div className="rounded-xl border border-slate-800 bg-slate-900 p-4">
        <h3 className="mb-3 text-sm uppercase tracking-wide text-slate-400">Holdings</h3>
        <div className="text-center text-sm text-slate-500">Loading...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-xl border border-slate-800 bg-slate-900 p-4">
        <h3 className="mb-3 text-sm uppercase tracking-wide text-slate-400">Holdings</h3>
        <div className="rounded bg-red-500/10 p-2 text-xs text-red-300">
          {error instanceof Error ? error.message : "Error loading holdings"}
        </div>
      </div>
    );
  }

  const holdings = data?.holdings || [];
  const totalValue = holdings.reduce((sum, h) => sum + (h.value || 0), 0);

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900 p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm uppercase tracking-wide text-slate-400">Holdings</h3>
        {totalValue > 0 && <span className="text-sm font-semibold text-slate-300">₹{totalValue.toFixed(2)}</span>}
      </div>
      {holdings.length === 0 ? (
        <div className="text-center text-sm text-slate-500">No holdings</div>
      ) : (
        <div className="space-y-2">
          {holdings.map((holding: Holding) => (
            <div key={holding.symbol} className="flex items-center justify-between rounded-lg border border-slate-700 bg-slate-800 p-3 text-sm">
              <div>
                <p className="font-medium">{holding.symbol}</p>
                <p className="text-xs text-slate-400">
                  {holding.qty} × ₹{holding.avg_price.toFixed(2)}
                </p>
              </div>
              <div className="text-right">
                <p className="font-semibold">₹{holding.value?.toFixed(2) || "0.00"}</p>
                <p className="text-xs text-slate-400">₹{holding.current_price.toFixed(2)}</p>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
