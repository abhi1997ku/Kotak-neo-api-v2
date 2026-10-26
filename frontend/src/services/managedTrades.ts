import { API_BASE_URL } from "./api";

export interface ManagedTradeRequest {
  request_id: string;
  symbol: string;
  instrument_token: string;
  exchange_segment: "nse_fo" | "bse_fo";
  product: "MIS" | "NRML";
  side: "BUY" | "SELL";
  quantity: number;
  entry_type: "MKT" | "L";
  entry_limit?: number;
  target_trigger: number;
  target_limit: number;
  stop_trigger: number;
  stop_limit: number;
}

export interface ManagedTrade extends ManagedTradeRequest {
  edit_revision?: number;
  state: string;
  note: string;
  selected_leg: "target" | "stop" | "manual" | null;
  entry_id: string | null;
  exit_id: string | null;
  entry_filled: number;
  exit_filled: number;
  trigger_to_ack_ms?: number | null;
  entry_price: number | null;
  exit_price: number | null;
  ltp?: number;
  tick_age_seconds: number | null;
}

async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE_URL}/managed-trades${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok) {
    const detail = result.detail;
    throw new Error(typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((item: { msg: string }) => item.msg).join("; ") : "Managed order request failed. Check Managed Trades before retrying.");
  }
  return result as T;
}

export const managedTrades = {
  edit: (id: string, payload: { revision: number; target_trigger: number; target_limit: number; stop_trigger: number; stop_limit: number }) => request<ManagedTrade>(`/${encodeURIComponent(id)}/edit`, payload),
  list: () => request<{ trades: ManagedTrade[]; feed_state: string; order_feed_state: string; error: string; live_execution_enabled: true }>(""),
  create: (payload: ManagedTradeRequest) => request<ManagedTrade>("/live", payload),
  action: (id: string, action: "reconcile" | "exit" | "release") => request<ManagedTrade>(`/${encodeURIComponent(id)}/${action}`, {}),
};
