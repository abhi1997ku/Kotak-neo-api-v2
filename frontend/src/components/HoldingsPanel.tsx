import { useQuery } from "@tanstack/react-query";
import { apiClient, type Holding } from "../services/api";

interface HoldingsPanelProps {
  compact?: boolean;
  onSellHolding: (holding: Holding) => void;
}

export function HoldingsPanel({ compact = false, onSellHolding }: HoldingsPanelProps) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["holdings"],
    queryFn: () => apiClient.getHoldings(),
    refetchInterval: 10000,
  });

  const containerClass = compact
    ? "rounded-lg border border-slate-800 bg-slate-950/70 p-3"
    : "rounded-xl border border-slate-800 bg-slate-900 p-4";

  if (isLoading) {
    return <div className={`${containerClass} text-center text-sm text-slate-500`}>Loading holdings...</div>;
  }

  if (error) {
    return (
      <div className={`${containerClass} text-xs text-red-300`}>
        {error instanceof Error ? error.message : "Error loading holdings"}
      </div>
    );
  }

  const holdings = data?.holdings || [];
  const totalValue = holdings.reduce((sum, holding) => sum + (holding.value || 0), 0);

  return (
    <section className={containerClass} aria-label="Holdings">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="text-sm font-medium uppercase tracking-wide text-slate-400">Your holdings</h3>
        {totalValue > 0 && <span className="text-xs font-semibold tabular-nums text-slate-300">&#8377;{totalValue.toFixed(2)}</span>}
      </div>
      {holdings.length === 0 ? (
        <div className="text-center text-sm text-slate-500">No holdings</div>
      ) : (
        <div className={compact ? "max-h-[42vh] space-y-2 overflow-y-auto pr-1" : "space-y-2"}>
          {holdings.map((holding: Holding) => (
            <div key={holding.symbol} className="rounded-lg border border-slate-700 bg-slate-800 p-2.5">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold text-slate-100">{holding.symbol}</p>
                  <p className="mt-0.5 text-xs text-slate-400">
                    {holding.qty} shares @ &#8377;{holding.avg_price.toFixed(2)}
                  </p>
                </div>
                <div className="shrink-0 text-right">
                  <p className="text-sm font-semibold tabular-nums text-slate-100">&#8377;{holding.value?.toFixed(2) || "0.00"}</p>
                  <p className="text-xs tabular-nums text-slate-400">LTP &#8377;{holding.current_price.toFixed(2)}</p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => onSellHolding(holding)}
                disabled={holding.qty < 1}
                className="mt-2 w-full rounded-md bg-red-900/40 px-3 py-1.5 text-xs font-semibold text-red-200 hover:bg-red-800/60 disabled:cursor-not-allowed disabled:opacity-50"
              >
                Sell holding
              </button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
