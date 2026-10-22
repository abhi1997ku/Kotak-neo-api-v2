// The paper workspace already serves these routes and owns the daily worker.
const BASE = "http://localhost:8003/api/screener/paper";

export type StockIdea = {
  symbol: string; company: string; sector: string; status: string; reason: string;
  score: number; close: number | null; as_of: string | null; reference?: number;
  stop?: number; target?: number; max_entry?: number;
  strategy?: string; entry_trigger?: number; planned_entry?: number; initial_stop?: number;
  trailing_stop?: number | null; risk_per_share?: number; quantity?: number; capital_required?: number;
  reward_risk?: number | null; reward_risk_note?: string; relative_strength?: number; volume_ratio?: number;
  invalidation?: string; entry_price?: number; holding_sessions?: number; unrealized_pnl?: number | null;
  execution_status?: string; score_components?: Record<string, number>; event_risk?: string;
};
export type StockOrder = StockIdea & {
  id: string; created_date: string; outcome_date?: string; outcome_reason?: string;
};
export type StockTrade = {
  id: string; symbol: string; sector: string; status: string; entry_date: string;
  entry: number; quantity: number; stop: number; target: number | null; mark: number;
  strategy?: string; initial_stop?: number; sessions?: number; exit_reason?: string;
  pending_exit?: { signal_date: string; reason: string } | null;
  mark_date: string; cost: number; dividends: number; planned_risk: number;
  exit: number | null; exit_date: string | null; net_pnl: number | null;
};
export type StockScan = {
  id?: number; created_at: string; bar_date: string; source: string;
  requested: number; available: number; stocks: StockIdea[];
  regime?: string; benchmark?: string; status?: string; event_data?: string;
};
export type StockPortfolio = {
  cash: number; equity: number; initial_capital: number; realized_pnl: number;
  unrealized_pnl: number; weekly_entries: number; max_weekly: number; max_open: number;
  month: string; month_pnl: number; month_return_pct: number; monthly_target: number | null;
  policy?: { id: string; hold_sessions?: number; validation_status: string; entries_enabled: boolean; message: string };
  open_portfolio_risk?: number; config?: SwingConfig;
  last_scan: string | null; last_session: string | null; enabled: boolean;
  running: boolean; error: string | null; warnings: string[];
  trades: StockTrade[]; orders: StockOrder[]; latest_scan: StockScan | null;
  events: { date: string; symbol: string; kind: string; detail: string }[];
  equity_history: { date: string; equity: number; cash: number }[];
};

export type SwingConfig = { strategy: string; exit_style: string; [key: string]: string | number };
export type ResearchMetric = { return_pct: number; annualized_pct: number; max_drawdown_pct: number;
  expectancy_inr: number | null; expectancy_r: number | null; profit_factor: number | null;
  win_rate_pct: number | null; closed_trades: number; sharpe_zero_rf: number | null;
  average_holding_sessions: number | null; transaction_costs: number; start: string; end: string };
export type SwingResearch = { status: string; message?: string; universe_source?: string;
  protocol?: { limitations: string[] }; strategies: Record<string, { config: SwingConfig;
  out_of_sample: ResearchMetric; recommendation: string; cost_stress: ResearchMetric }> };

async function request<T>(path = "", method = "GET"): Promise<T> {
  const response = await fetch(`${BASE}${path}`, { method });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Paper portfolio request failed (${response.status})`);
  }
  return response.json();
}

export const stockPaper = {
  portfolio: () => request<StockPortfolio>(),
  scan: () => request<{ running: boolean }>("/scan", "POST"),
  scans: () => request<{ id: number; created_at: string }[]>("/scans"),
  history: (id: number) => request<StockScan>(`/scans/${id}`),
  exportUrl: `${BASE}/export`,
  research: () => request<SwingResearch>("/research"),
  config: () => request<SwingConfig>("/config"),
  saveConfig: async (config: SwingConfig) => {
    const response = await fetch(`${BASE}/config`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(config) });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "Could not save settings");
    return body as SwingConfig;
  },
};
