import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, type Order } from "../services/api";

export function OrdersPanel() {
  const queryClient = useQueryClient();
  const [editingOrderId, setEditingOrderId] = useState<string | null>(null);
  const [editQuantity, setEditQuantity] = useState("1");
  const [editPrice, setEditPrice] = useState("");
  const [busyOrderId, setBusyOrderId] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState("");
  const [actionFailed, setActionFailed] = useState(false);
  const { data, isLoading, error } = useQuery({
    queryKey: ["orders"],
    queryFn: () => apiClient.getOrderBook(),
    refetchInterval: 3000,
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
      case "OPEN":
      case "PENDING":
      case "TRIGGER PENDING":
        return "bg-yellow-500/20 text-yellow-300";
      case "COMPLETE":
      case "COMPLETED":
      case "EXECUTED":
      case "TRADED":
        return "bg-emerald-500/20 text-emerald-300";
      case "REJECTED":
        return "bg-red-500/20 text-red-300";
      case "CANCELED":
      case "CANCELLED":
        return "bg-slate-500/20 text-slate-300";
      default:
        return "bg-slate-500/20 text-slate-300";
    }
  };

  const isTerminal = (status: string) => {
    const normalized = status?.trim().toUpperCase().replace(/[_-]+/g, " ") || "";
    const partiallyFilled = /^(PARTIAL|PART ).*(TRADE|FILL)/.test(normalized);
    const terminalMarkers = ["CANCEL", "REJECT", "COMPLETE", "EXECUT", "TRADE", "FILL", "EXPIRE", "CLOSED"];
    if (partiallyFilled) {
      return ["CANCEL", "REJECT", "EXPIRE", "CLOSED"].some((marker) => normalized.includes(marker));
    }
    return terminalMarkers.some((marker) => normalized.includes(marker));
  };

  const refreshOrders = async () => {
    await queryClient.invalidateQueries({ queryKey: ["orders"] });
    await queryClient.refetchQueries({ queryKey: ["orders"] });
  };

  const beginModify = (order: Order) => {
    setEditingOrderId(order.order_id);
    setEditQuantity(String(order.qty || 1));
    setEditPrice(order.price > 0 ? String(order.price) : "");
    setActionMessage("");
    setActionFailed(false);
  };

  const saveModification = async (order: Order) => {
    const quantity = Number(editQuantity);
    const price = order.order_type === "MKT" ? Number(order.price || 0) : Number(editPrice);
    if (!Number.isInteger(quantity) || quantity < 1) {
      setActionFailed(true);
      setActionMessage("Quantity must be a whole number greater than zero.");
      return;
    }
    if (order.order_type !== "MKT" && (!Number.isFinite(price) || price <= 0)) {
      setActionFailed(true);
      setActionMessage("Enter a valid order price.");
      return;
    }

    setBusyOrderId(order.order_id);
    setActionMessage("");
    setActionFailed(false);
    try {
      await apiClient.modifyOrder({
        order_id: order.order_id,
        order_type: order.order_type || "L",
        quantity,
        price,
        validity: order.validity || "DAY",
        amo: order.amo || "NO",
      });
      setEditingOrderId(null);
      setActionMessage("Modification request sent for order " + order.order_id + ".");
      await refreshOrders();
    } catch (modifyError) {
      setActionFailed(true);
      setActionMessage(modifyError instanceof Error ? modifyError.message : "Could not modify this order.");
    } finally {
      setBusyOrderId(null);
    }
  };

  const cancel = async (order: Order) => {
    const approved = window.confirm(
      "Cancel " + order.side + " " + order.qty + " " + order.symbol + " order " + order.order_id + "?"
    );
    if (!approved) return;

    setBusyOrderId(order.order_id);
    setActionMessage("");
    setActionFailed(false);
    try {
      await apiClient.cancelOrder(order.order_id, order.amo || "NO");
      if (editingOrderId === order.order_id) setEditingOrderId(null);
      setActionMessage("Cancellation request sent for order " + order.order_id + ".");
      await refreshOrders();
    } catch (cancelError) {
      setActionFailed(true);
      setActionMessage(cancelError instanceof Error ? cancelError.message : "Could not cancel this order.");
    } finally {
      setBusyOrderId(null);
    }
  };

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900 p-4">
      <h3 className="mb-3 text-sm uppercase tracking-wide text-slate-400">Order Book</h3>
      {actionMessage && (
        <p className={"mb-3 rounded px-3 py-2 text-xs " + (actionFailed ? "bg-red-500/10 text-red-300" : "bg-sky-500/10 text-sky-200")}>
          {actionMessage}
        </p>
      )}
      {orders.length === 0 ? (
        <div className="text-center text-sm text-slate-500">
          {data?.message || "Kotak returned no orders for the current order book."}
        </div>
      ) : (
        <div className="max-h-64 space-y-2 overflow-y-auto">
          {orders.map((order: Order) => {
            const knownStatus = Boolean(order.status) && !["UNKNOWN", "NONE"].includes(order.status.trim().toUpperCase());
            const canManage = Boolean(order.order_id) && knownStatus && !isTerminal(order.status);
            const isEditing = editingOrderId === order.order_id;
            const isBusy = busyOrderId === order.order_id;
            return (
              <div key={order.order_id} className="rounded-lg border border-slate-700 bg-slate-800 p-3 text-xs">
                <div className="flex items-center gap-3">
                  <div className="min-w-0 flex-1">
                    <p className="font-medium text-slate-200">{order.symbol || "Unknown instrument"}</p>
                    <p className="text-slate-400">
                      {order.side === "BUY" ? "Buy" : "Sell"} {order.qty} @ ₹{Number(order.price || 0).toFixed(2)}
                      {order.order_type ? " · " + order.order_type : ""}
                    </p>
                    <p className="mt-1 text-[10px] text-slate-500">Order ID: {order.order_id}</p>
                  </div>
                  <div className={"rounded px-2 py-1 font-medium " + statusColor(order.status)}>{order.status}</div>
                  {canManage && (
                    <div className="flex shrink-0 gap-1">
                      <button
                        type="button"
                        disabled={isBusy}
                        onClick={() => isEditing ? setEditingOrderId(null) : beginModify(order)}
                        className="rounded border border-sky-700 px-2 py-1 text-sky-200 hover:bg-sky-900/40 disabled:opacity-50"
                      >
                        {isEditing ? "Close" : "Modify"}
                      </button>
                      <button
                        type="button"
                        disabled={isBusy}
                        onClick={() => void cancel(order)}
                        className="rounded border border-red-800 px-2 py-1 text-red-300 hover:bg-red-900/30 disabled:opacity-50"
                      >
                        {isBusy ? "Working..." : "Cancel"}
                      </button>
                    </div>
                  )}
                </div>
                {isEditing && (
                  <div className="mt-3 grid grid-cols-1 gap-2 rounded border border-slate-700 bg-slate-900 p-3 sm:grid-cols-3">
                    <label className="text-slate-400">
                      Quantity
                      <input
                        type="number"
                        min="1"
                        step="1"
                        value={editQuantity}
                        onChange={(event) => setEditQuantity(event.target.value)}
                        className="mt-1 w-full rounded border border-slate-700 bg-slate-800 px-2 py-2 text-slate-100"
                      />
                    </label>
                    {order.order_type !== "MKT" && (
                      <label className="text-slate-400">
                        Order price
                        <input
                          type="number"
                          min="0.01"
                          step="0.01"
                          value={editPrice}
                          onChange={(event) => setEditPrice(event.target.value)}
                          className="mt-1 w-full rounded border border-slate-700 bg-slate-800 px-2 py-2 text-slate-100"
                        />
                      </label>
                    )}
                    <button
                      type="button"
                      disabled={isBusy}
                      onClick={() => void saveModification(order)}
                      className="self-end rounded bg-sky-700 px-3 py-2 font-semibold text-white hover:bg-sky-600 disabled:opacity-50"
                    >
                      {isBusy ? "Saving..." : "Update order"}
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
