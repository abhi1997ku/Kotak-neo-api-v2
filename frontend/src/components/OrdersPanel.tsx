import { useQuery } from "@tanstack/react-query";
import { apiClient, type Order } from "../services/api";

export function OrdersPanel() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["orders"],
    queryFn: () => apiClient.getOrderBook(),
    refetchInterval: 3000, // Refetch every 3 seconds
  });

  if (isLoading) {
    return (
      <div className="rounded-xl border border-slate-800 bg-slate-900 p-4">
        <h3 className="mb-3 text-sm uppercase tracking-wide text-slate-400">Order Book</h3>
        <div className="text-center text-sm text-slate-500">Loading...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-xl border border-slate-800 bg-slate-900 p-4">
        <h3 className="mb-3 text-sm uppercase tracking-wide text-slate-400">Order Book</h3>
        <div className="rounded bg-red-500/10 p-2 text-xs text-red-300">
          {error instanceof Error ? error.message : "Error loading orders"}
        </div>
      </div>
    );
  }

  const orders = data?.orders || [];

  const statusColor = (status: string) => {
    switch (status?.toUpperCase()) {
      case "PENDING":
        return "bg-yellow-500/20 text-yellow-300";
      case "EXECUTED":
        return "bg-emerald-500/20 text-emerald-300";
      case "REJECTED":
        return "bg-red-500/20 text-red-300";
      case "CANCELLED":
        return "bg-slate-500/20 text-slate-300";
      default:
        return "bg-slate-500/20 text-slate-300";
    }
  };

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900 p-4">
      <h3 className="mb-3 text-sm uppercase tracking-wide text-slate-400">Order Book</h3>
      {orders.length === 0 ? (
        <div className="text-center text-sm text-slate-500">No orders</div>
      ) : (
        <div className="space-y-2 max-h-64 overflow-y-auto">
          {orders.map((order: Order) => (
            <div key={order.order_id} className="flex items-center justify-between rounded-lg border border-slate-700 bg-slate-800 p-3 text-xs">
              <div className="flex-1">
                <p className="font-medium text-slate-200">{order.symbol}</p>
                <p className="text-slate-400">
                  {order.side === "BUY" ? "Buy" : "Sell"} {order.qty} @ ₹{Number(order.price || 0).toFixed(2)}
                </p>
              </div>
              <div className={`rounded px-2 py-1 font-medium ${statusColor(order.status)}`}>{order.status}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
