/**
 * API Client for Kotak Neo Backend
 */

const API_BASE_URL = "http://localhost:8001/api";

// Types
export interface Position {
  symbol: string;
  qty: number;
  avg_price: number;
  current_price: number;
  pnl: number;
  pnl_pct: number;
}

export interface Holding {
  symbol: string;
  qty: number;
  avg_price: number;
  current_price: number;
  value: number;
}

export interface Order {
  order_id: string;
  symbol: string;
  side: "BUY" | "SELL";
  qty: number;
  price: number;
  status: string;
  timestamp: string;
}

export interface Trade {
  trade_id: string;
  symbol: string;
  side: "BUY" | "SELL";
  qty: number;
  price: number;
  timestamp: string;
}

export interface MarginData {
  available: number;
  utilised: number;
  gross: number;
  pnl: number;
}

export interface LoginResponse {
  view_token?: string;
  message?: string;
}

export interface QuoteData {
  symbol: string;
  ltp: number;
  bid: number;
  ask: number;
  volume: number;
  change: number;
  change_pct: number;
}

export interface ScreenerCandidate {
  symbol: string;
  setup_type: string;
  entry: number;
  stop: number;
  target: number;
  rr: number;
  suggested_qty: number;
  rationale: string;
}

// API Client Class
class KotakNeoAPI {
  private token?: string;

  constructor() {
    // Try to load token from localStorage
    this.token = localStorage.getItem("kotak_token") || undefined;
  }

  setToken(token: string) {
    this.token = token;
    localStorage.setItem("kotak_token", token);
  }

  clearToken() {
    this.token = undefined;
    localStorage.removeItem("kotak_token");
  }

  private async request<T>(
    endpoint: string,
    options: RequestInit = {}
  ): Promise<T> {
    const url = `${API_BASE_URL}${endpoint}`;
    const headers: HeadersInit = {
      "Content-Type": "application/json",
      ...options.headers,
    };

    const response = await fetch(url, {
      ...options,
      headers,
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }

    return response.json() as Promise<T>;
  }

  // Auth endpoints
  async login(totp: string, mpin: string): Promise<LoginResponse> {
    const params = new URLSearchParams({
      totp,
      mpin,
    });
    return this.request<LoginResponse>(`/auth/login?${params.toString()}`, {
      method: "POST",
    });
  }

  async getAuthStatus() {
    return this.request(`/auth/status`, { method: "GET" });
  }

  // Positions endpoints
  async getPositions(kind: string = "net"): Promise<{ positions: Position[] }> {
    return this.request(`/positions/?kind=${kind}`, { method: "GET" });
  }

  async getHoldings(): Promise<{ holdings: Holding[] }> {
    return this.request(`/positions/holdings`, { method: "GET" });
  }

  async getMargin(): Promise<MarginData> {
    return this.request(`/positions/margin`, { method: "GET" });
  }

  // Order endpoints
  async getOrderBook(): Promise<{ orders: Order[] }> {
    return this.request(`/orders/book`, { method: "GET" });
  }

  async getTradeBook(): Promise<{ trades: Trade[] }> {
    return this.request(`/orders/trade-book`, { method: "GET" });
  }

  async placeOrder(orderData: {
    exchange_segment: string;
    product: string;
    order_type: string;
    transaction_type: string;
    quantity: number;
    price?: number;
    trading_symbol?: string;
    validity?: string;
    trigger_price?: number;
    tag?: string;
  }): Promise<{ order_id: string }> {
    return this.request(`/orders/place`, {
      method: "POST",
      body: JSON.stringify(orderData),
    });
  }

  // Market endpoints
  async getQuotes(symbols: string[]): Promise<{ quotes: QuoteData[] }> {
    return this.request(`/market/quotes?symbols=${symbols.join(",")}`, {
      method: "GET",
    });
  }

  async searchSymbol(query: string): Promise<{ results: any[]; message?: string }> {
    const response = await this.request<any>(`/market/search?query=${encodeURIComponent(query)}`, {
      method: "GET",
    });

    if (Array.isArray(response)) {
      return { results: response };
    }

    if (Array.isArray(response?.results)) {
      return response;
    }

    if (Array.isArray(response?.data)) {
      return { results: response.data, message: response.message };
    }

    return {
      results: [],
      message: response?.message || response?.detail || "No matching symbol found.",
    };
  }

  async getOptionChain(symbol: string, expiry?: string): Promise<any> {
    const url =
      expiry !== undefined
        ? `/market/option-chain?symbol=${symbol}&expiry=${expiry}`
        : `/market/option-chain?symbol=${symbol}`;
    return this.request(url, { method: "GET" });
  }

  async runScreener(mode: "real" | "synthetic" = "synthetic", minRr = 2): Promise<{ mode: string; candidate_count: number; candidates: ScreenerCandidate[] }> {
    return this.request(`/screener/scan?mode=${mode}&min_rr=${minRr}`, { method: "GET" });
  }
}

export const apiClient = new KotakNeoAPI();
