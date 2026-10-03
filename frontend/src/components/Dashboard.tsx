import { useState } from "react";
import { BriefcaseBusiness, ChevronDown, LogOut, RefreshCw } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../contexts/AuthContext";
import { PositionsPanel } from "./PositionsPanel";
import { HoldingsPanel } from "./HoldingsPanel";
import { OrdersPanel } from "./OrdersPanel";
import { MarginPanel } from "./MarginPanel";
import { SearchSuggestions, type SearchInstrument } from "./SearchSuggestions";
import { SearchResultsPanel } from "./SearchResultsPanel";
import { OptionChainPanel } from "./OptionChainPanel";
import { ScreenerPanel } from "./ScreenerPanel";
import { WatchlistPanel } from "./WatchlistPanel";
import { PaperTradingPanel } from "./PaperTradingPanel";
import { OrderTicketModal, type OrderTicketRequest } from "./OrderTicketModal";
import { type Holding, type Position, type WatchlistItem } from "../services/api";

type OrderInstrument = SearchInstrument | WatchlistItem | Position | Holding;

export function Dashboard() {
  const { logout } = useAuth();
  const queryClient = useQueryClient();
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults] = useState<any[]>([]);
  const [selectedSymbol, setSelectedSymbol] = useState<any | null>(null);
  const [orderTicket, setOrderTicket] = useState<OrderTicketRequest | null>(null);
  const [showHoldings, setShowHoldings] = useState(false);

  const handleRefresh = () => {
    queryClient.refetchQueries();
  };

  const handleOrderRequest = (
    instrument: OrderInstrument,
    side: "BUY" | "SELL",
    options: Partial<Pick<OrderTicketRequest, "quantity" | "maxQuantity" | "product" | "isExit" | "isHoldingSale" | "orderType" | "limitPrice">> = {},
  ) => {
    const instrumentData = instrument as SearchInstrument & WatchlistItem & Position;
    const exchangeSegment = (instrumentData.exchange_segment
      || (instrumentData.exchange === "BSE" ? "bse_cm" : instrumentData.exchange === "BFO" ? "bse_fo" : instrumentData.exchange === "NFO" ? "nse_fo" : "nse_cm"));
    const normalizedSegment = exchangeSegment.toLowerCase();
    setOrderTicket({
      symbol: instrumentData.symbol,
      tradingSymbol: instrumentData.trading_symbol || instrumentData.symbol,
      exchangeSegment: normalizedSegment,
      instrumentToken: instrumentData.instrument_token?.split("|").pop() || undefined,
      side,
      quantity: options.quantity || 1,
      maxQuantity: options.maxQuantity,
      product: options.product || instrumentData.product || (normalizedSegment.endsWith("_fo") ? "NRML" : "CNC"),
      ltp: Number(instrumentData.ltp ?? instrumentData.current_price) || undefined,
      orderType: options.orderType,
      limitPrice: options.limitPrice,
      isExit: options.isExit,
      isHoldingSale: options.isHoldingSale,
    });
  };

  const handleSelectSymbol = (symbol: any) => {
    const normalized = String(symbol.symbol || "").toUpperCase().replace(/[ _-]/g, "");
    const optionChainSymbol = ["NIFTY", "NIFTY50"].includes(normalized)
      ? "NIFTY"
      : ["BANKNIFTY", "NIFTYBANK"].includes(normalized)
        ? "BANKNIFTY"
        : normalized === "SENSEX"
          ? "SENSEX"
          : undefined;
    setSelectedSymbol(optionChainSymbol ? { ...symbol, option_chain_symbol: optionChainSymbol, exchange: "INDEX" } : symbol);
    setSearchQuery(symbol.symbol);
    if (!optionChainSymbol && symbol.exchange !== "INDEX") handleOrderRequest(symbol as SearchInstrument, "BUY");
  };

  const handleWatchlistStockClick = (stock: WatchlistItem) => {
    setSearchQuery(stock.symbol);
    setSelectedSymbol({
      ...stock,
      trading_symbol: stock.trading_symbol || `${stock.symbol}-EQ`,
      exchange: "NSE",
    });
  };

  const handleWatchlistIndexClick = (index: WatchlistItem) => {
    handleSelectSymbol({ ...index, name: index.name, exchange: "INDEX" });
  };

  const handleExitPosition = (position: Position) => {
    const quantity = Math.abs(position.order_quantity || position.qty);
    handleOrderRequest(position, position.qty > 0 ? "SELL" : "BUY", {
      quantity,
      maxQuantity: quantity,
      product: position.product,
      isExit: true,
    });
  };

  const handleSellHolding = (holding: Holding) => {
    const tradingSymbol = holding.trading_symbol
      || (holding.symbol.toUpperCase().endsWith("-EQ") ? holding.symbol : `${holding.symbol}-EQ`);
    const quantity = Math.max(0, Math.floor(holding.qty));
    if (quantity < 1) return;
    handleOrderRequest({
      ...holding,
      name: holding.symbol,
      exchange: "NSE",
      trading_symbol: tradingSymbol,
      exchange_segment: holding.exchange_segment || "nse_cm",
      product: holding.product || "CNC",
    } as SearchInstrument & Holding, "SELL", {
      quantity,
      maxQuantity: quantity,
      product: holding.product || "CNC",
      isHoldingSale: true,
    });
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <div className="flex min-h-screen">
        <aside className="h-screen w-80 shrink-0 self-start overflow-y-auto border-r border-slate-800 bg-slate-900/80 p-4">
          <div className="mb-8">
            <h1 className="text-xl font-bold">Kotak Neo Terminal</h1>
            <p className="text-sm text-slate-400">Personal trading dashboard</p>
          </div>

          <div className="mb-6">
            <h2 className="mb-3 text-xs uppercase tracking-wide text-slate-400">Margin</h2>
            <MarginPanel />
          </div>

          <div className="mb-6">
            <h2 className="mb-2 text-xs uppercase tracking-wide text-slate-400">Search</h2>
            <SearchSuggestions onSelectSymbol={handleSelectSymbol} onOrderRequest={handleOrderRequest} />
          </div>

          {searchQuery && searchResults.length > 0 && (
            <SearchResultsPanel results={searchResults} query={searchQuery} onSelectResult={handleSelectSymbol} />
          )}

          <div className="space-y-2 border-t border-slate-800 pt-4">
            <button onClick={handleRefresh} className="flex w-full items-center gap-2 rounded-md bg-slate-800 px-3 py-2 text-sm hover:bg-slate-700">
              <RefreshCw size={16} />
              Refresh
            </button>
            <button onClick={logout} className="flex w-full items-center gap-2 rounded-md bg-red-900/30 px-3 py-2 text-sm text-red-300 hover:bg-red-900/50">
              <LogOut size={16} />
              Logout
            </button>
            <button
              type="button"
              onClick={() => setShowHoldings((open) => !open)}
              aria-expanded={showHoldings}
              className="flex w-full items-center justify-between rounded-md bg-slate-800 px-3 py-2 text-sm hover:bg-slate-700"
            >
              <span className="flex items-center gap-2"><BriefcaseBusiness size={16} />Holdings</span>
              <ChevronDown size={16} className={`transition-transform ${showHoldings ? "rotate-180" : ""}`} />
            </button>
            {showHoldings && <HoldingsPanel compact onSellHolding={handleSellHolding} />}
          </div>

          <div className="mt-6 border-t border-slate-800 pt-4">
            <ScreenerPanel />
          </div>
        </aside>

        <main className="min-w-0 flex-1 overflow-auto p-4">
          <div className="space-y-4">
            <PaperTradingPanel />
            <WatchlistPanel
              view="indices"
              onSelectStock={handleWatchlistStockClick}
              onOrderRequest={handleOrderRequest}
              onSelectIndex={handleWatchlistIndexClick}
              selectedSymbol={selectedSymbol?.symbol}
            />

            <PositionsPanel onExitPosition={handleExitPosition} />
            <OrdersPanel />

            {selectedSymbol?.option_chain_symbol && (
              <OptionChainPanel symbol={selectedSymbol.option_chain_symbol} displayName={selectedSymbol.name} />
            )}

            <WatchlistPanel
              view="stocks"
              onSelectStock={handleWatchlistStockClick}
              onOrderRequest={handleOrderRequest}
              onSelectIndex={handleWatchlistIndexClick}
              selectedSymbol={selectedSymbol?.symbol}
            />
          </div>
        </main>
      </div>
      {orderTicket && (
        <OrderTicketModal
          key={`${orderTicket.tradingSymbol}-${orderTicket.side}-${orderTicket.isExit ? "exit" : orderTicket.isHoldingSale ? "holding-sale" : "order"}`}
          ticket={orderTicket}
          onClose={() => setOrderTicket(null)}
        />
      )}
    </div>
  );
}
