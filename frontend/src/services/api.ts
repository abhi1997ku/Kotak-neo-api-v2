/**
 * API Client for Kotak Neo Backend
 */

import { isResearchWorkspace } from "./workspace";
export const API_BASE_URL = `http://localhost:${isResearchWorkspace ? 8003 : 8001}/api`;

export interface OrderAcknowledgement {
  order_id: string;
  status?: string;
  timing?: { queue_ms: number; broker_ms: number };
}

// Types
export interface Position {
  symbol: string;
  /** Net quantity in lots for some F&O rows; see order_quantity for contract units. */
  qty: number;
  /** Kotak order quantity in contract/share units, used for realized-size calculations. */
  order_quantity?: number;
  avg_price: number;
  current_price: number;
  pnl: number;
  pnl_pct: number;
  exchange_segment?: string;
  instrument_token?: string;
  product?: string;
}

export interface PositionToken {
  symbol: string;
  exchange_segment: string;
  instrument_token: string;
}

export interface Holding {
  symbol: string;
  qty: number;
  avg_price: number;
  current_price: number;
  value: number;
  cost_value?: number;
  pnl?: number;
  pnl_pct?: number;
  trading_symbol?: string;
  instrument_token?: string;
  exchange_segment?: string;
  product?: string;
}

export interface Order {
  order_id: string;
  symbol: string;
  side: "BUY" | "SELL";
  qty: number;
  filled_qty?: number | null;
  unfilled_qty?: number | null;
  price: number;
  average_fill_price?: number | null;
  order_type: string;
  validity: string;
  amo: "YES" | "NO";
  status: string;
  timestamp: string;
}

export interface Trade {
  trade_id: string;
  order_id?: string;
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

  async getAuthStatus(): Promise<{
    authenticated: boolean;
    session_health?: "active" | "degraded" | "expired" | "not_authenticated";
    last_session_check_at?: string | null;
    message?: string;
  }> {
    return this.request(`/auth/status`, { method: "GET" });
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
  }): Promise<OrderAcknowledgement> {
    return this.request<OrderAcknowledgement>(`/orders/place`, {
      method: "POST",
      body: JSON.stringify(orderData),
    });
  }

  // Market endpoints
  async getQuotes(symbols: string[], exchangeSegment = "nse_cm"): Promise<{ quotes: QuoteData[] }> {
    return this.request(`/market/quotes?symbols=${symbols.join(",")}&exchange_segment=${encodeURIComponent(exchangeSegment)}`, {
      method: "GET",
    });
  }

  async getWatchlist(indicesOnly = false): Promise<WatchlistData> {
    return this.request(`/watchlist/?indices_only=${indicesOnly}`, { method: "GET" });
  }

  createWatchlistStream(indicesOnly = false): WebSocket {
    const websocketBase = API_BASE_URL.replace(/^http/, "ws");
    return new WebSocket(`${websocketBase}/watchlist/stream?indices_only=${indicesOnly}`);
  }

  createOptionChainStream(tokens: OptionToken[]): WebSocket {
    const websocketBase = API_BASE_URL.replace(/^http/, "ws");
    const encodedTokens = tokens
      .map(({ exchange_segment, instrument_token }) => `${exchange_segment}|${instrument_token}`)
      .join(",");
    return new WebSocket(`${websocketBase}/market/option-chain/stream?tokens=${encodeURIComponent(encodedTokens)}`);
  }

  createPositionStream(tokens: PositionToken[]): WebSocket {
    const websocketBase = API_BASE_URL.replace(/^http/, "ws");
    return new WebSocket(`${websocketBase}/positions/stream?tokens=${encodeURIComponent(JSON.stringify(tokens))}`);
  }

  createPaperFutureStream(): WebSocket {
    const websocketBase = API_BASE_URL.replace(/^http/, "ws");
    return new WebSocket(`${websocketBase}/paper-trading/future-stream`);
  }

  async getPaperAgentStatus(): Promise<any> { return this.request(`/paper-trading/status`, { method: "GET" }); }
  async startPaperAgent(): Promise<any> { return this.request(`/paper-trading/start`, { method: "POST" }); }
  async stopPaperAgent(): Promise<any> { return this.request(`/paper-trading/stop`, { method: "POST" }); }
  async getPaperReport(): Promise<any> { return this.request(`/paper-trading/report`, { method: "GET" }); }
  async getPaperDayAudit(): Promise<any> { return this.request(`/paper-trading/day-audit`, { method: "GET" }); }
  getPaperReportCsvUrl(): string { return `${API_BASE_URL}/paper-trading/report.csv`; }

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


}

export const apiClient = new KotakNeoAPI();
