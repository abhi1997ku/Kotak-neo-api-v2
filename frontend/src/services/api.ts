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
  order_type: string;
  validity: string;
  amo: "YES" | "NO";
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
  message?: string;
}

export interface QuoteData {
  symbol: string;
  instrument_token?: string;
  exchange_segment?: string;
  ltp: number;
  bid: number;
  ask: number;
  volume: number;
  change: number;
  change_pct: number;
}

export interface WatchlistItem {
  symbol: string;
  name: string;
  exchange_segment: string;
  instrument_token: string;
  ltp: number | null;
  change: number | null;
  change_pct: number | null;
  trading_symbol?: string;
}

export interface WatchlistData {
  indices: WatchlistItem[];
  stocks: WatchlistItem[];
  updated_at: string;
  constituents_source: string;
  constituent_count: number;
  stock_token_count?: number;
  stocks_without_token?: string[];
  catalog_warning?: string | null;
}

export interface OptionToken {
  exchange_segment: string;
  instrument_token: string;
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
      if (response.status === 401) {
        window.dispatchEvent(new Event("kotak:unauthorized"));
      }
      throw new Error(error.detail || `HTTP ${response.status}`);
    }

    return response.json() as Promise<T>;
  }

  // Auth endpoints
  async login(totp: string, mpin: string): Promise<LoginResponse> {
    return this.request<LoginResponse>(`/auth/login`, {
      method: "POST",
      body: JSON.stringify({ totp, mpin }),
    });
  }

  async getAuthStatus(): Promise<{ authenticated: boolean }> {
    return this.request<{ authenticated: boolean }>(`/auth/status`, { method: "GET" });
  }

  async logout(): Promise<void> {
    await this.request(`/auth/logout`, { method: "POST" });
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
  async getOrderBook(): Promise<{ orders: Order[]; message?: string }> {
    return this.request(`/orders/book`, { method: "GET" });
  }

  async getTradeBook(): Promise<{ trades: Trade[] }> {
    return this.request(`/orders/trade-book`, { method: "GET" });
  }

  async modifyOrder(orderData: {
    order_id: string;
    order_type: string;
    quantity: number;
    price: number;
    validity: string;
    amo: "YES" | "NO";
  }): Promise<{ order_id: string; result: unknown }> {
    return this.request(`/orders/modify`, {
      method: "POST",
      body: JSON.stringify(orderData),
    });
  }

  async cancelOrder(order_id: string, amo: "YES" | "NO"): Promise<{ order_id: string; result: unknown }> {
    return this.request(`/orders/cancel`, {
      method: "POST",
      body: JSON.stringify({ order_id, amo }),
    });
  }

  async placeOrder(orderData: {
    exchange_segment: string;
    product: string;
    order_type: string;
    transaction_type: string;
    quantity: number;
    price?: number;
    amo?: "YES" | "NO";
    trading_symbol?: string;
    validity?: string;
    trigger_price?: number;
    tag?: string;
    scrip_token?: string;
  }): Promise<{ order_id: string; status?: string }> {
    return this.request<{ order_id: string; status?: string }>(`/orders/place`, {
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

  async getWatchlist(): Promise<WatchlistData> {
    return this.request(`/watchlist/`, { method: "GET" });
  }

  createWatchlistStream(): WebSocket {
    const websocketBase = API_BASE_URL.replace(/^http/, "ws");
    return new WebSocket(`${websocketBase}/watchlist/stream`);
  }

  createOptionChainStream(tokens: OptionToken[]): WebSocket {
    const websocketBase = API_BASE_URL.replace(/^http/, "ws");
    const encodedTokens = tokens
      .map(({ exchange_segment, instrument_token }) => `${exchange_segment}|${instrument_token}`)
      .join(",");
    return new WebSocket(`${websocketBase}/market/option-chain/stream?tokens=${encodeURIComponent(encodedTokens)}`);
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
        ? `/market/option-chain?symbol=${encodeURIComponent(symbol)}&expiry=${encodeURIComponent(expiry)}`
        : `/market/option-chain?symbol=${encodeURIComponent(symbol)}`;
    return this.request(url, { method: "GET" });
  }

  async runScreener(mode: "real" | "synthetic" = "synthetic", minRr = 2): Promise<{ mode: string; candidate_count: number; candidates: ScreenerCandidate[] }> {
    return this.request(`/screener/scan?mode=${mode}&min_rr=${minRr}`, { method: "GET" });
  }
}

export const apiClient = new KotakNeoAPI();
