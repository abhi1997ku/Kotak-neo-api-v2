import { PaperWorkspaceLink } from "./PaperWorkspaceLink";
import { ManagedTradesPanel } from "./ManagedTradesPanel";
import { OrderAcknowledgementStatus } from "./OrderAcknowledgementStatus";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../contexts/AuthContext";
import { PositionsPanel } from "./PositionsPanel";
import { OrdersPanel } from "./OrdersPanel";
import { SearchSuggestions, type SearchInstrument } from "./SearchSuggestions";
import { HoldingsPanel } from "./HoldingsPanel";
import { OptionChainPanel } from "./OptionChainPanel";
import { WatchlistPanel } from "./WatchlistPanel";
import { OrderTicketModal, type OrderTicketRequest } from "./OrderTicketModal";
import { apiClient, type Holding, type Position, type WatchlistItem } from "../services/api";

type OrderInstrument = SearchInstrument | WatchlistItem | Position | Holding;

export function Dashboard() {
  const { logout, sessionHealth } = useAuth();
  const queryClient = useQueryClient();
  const [selectedSymbol, setSelectedSymbol] = useState<any | null>({ symbol: "NIFTY 50", name: "Nifty 50", option_chain_symbol: "NIFTY" });
  const [orderTicket, setOrderTicket] = useState<OrderTicketRequest | null>(null);
  const [showPortfolio, setShowPortfolio] = useState(false);

  const handleRefresh = () => {
    void queryClient.refetchQueries({ type: "active" });
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
    if (!optionChainSymbol && symbol.exchange !== "INDEX") handleOrderRequest(symbol as SearchInstrument, "BUY");
  };

  const handleWatchlistStockClick = (stock: WatchlistItem) => {
    setSelectedSymbol({
      ...stock,
      trading_symbol: stock.trading_symbol || `${stock.symbol}-EQ`,
      exchange: "NSE",
    });
  };

  const handleWatchlistIndexClick = (index: WatchlistItem) => {
    handleSelectSymbol({ ...index, name: index.name, exchange: "INDEX" });
  };

  const previewIndex = (index: WatchlistItem) => {
    const symbol = ({ "NIFTY 50": "NIFTY", "NIFTY BANK": "BANKNIFTY", "SENSEX": "SENSEX" } as Record<string, string>)[index.symbol];
    if (!symbol || symbol === selectedSymbol?.option_chain_symbol) return;
    void queryClient.prefetchQuery({
      queryKey: ["option_chain", symbol, ""],
      queryFn: () => apiClient.getOptionChain(symbol),
      staleTime: 5000,
      retry: false,
    });
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
    const quantity = Math.max(0, Math.floor(holding.qty));
    if (quantity < 1) return;
    handleOrderRequest({
      ...holding,
      trading_symbol: holding.trading_symbol || (holding.symbol.toUpperCase().endsWith("-EQ") ? holding.symbol : `${holding.symbol}-EQ`),
      exchange_segment: holding.exchange_segment || "nse_cm",
    }, "SELL", {
      quantity,
      maxQuantity: quantity,
      product: holding.product || "CNC",
      isHoldingSale: true,
    });
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <div className="flex min-h-screen flex-col items-stretch lg:flex-row">
        <aside className="w-full shrink-0 border-b border-slate-800 bg-slate-900/80 p-4 lg:w-80 lg:border-b-0 lg:border-r" aria-label="Search and portfolio">
          <h2 className="mb-3 text-sm font-semibold text-slate-300">Search instruments</h2>
          <SearchSuggestions onSelectSymbol={handleSelectSymbol} onOrderRequest={handleOrderRequest} />
          <button
            type="button"
            onClick={() => setShowPortfolio((open) => !open)}
            aria-expanded={showPortfolio}
            aria-controls="live-portfolio"
            className="mb-3 mt-6 flex w-full items-center justify-between rounded-md bg-slate-800 px-3 py-2 text-sm hover:bg-slate-700"
          >
            <span>Portfolio / Holdings</span><span aria-hidden="true">{showPortfolio ? "−" : "+"}</span>
          </button>
          <div id="live-portfolio">
            {showPortfolio && <HoldingsPanel compact onSellHolding={handleSellHolding} />}
          </div>
        </aside>
        <main className="min-w-0 flex-1 p-4">
          {sessionHealth === "degraded" && (
            <div role="status" className="mb-4 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
              Kotak did not confirm the session on the last check. Your current login is retained and the app will retry.
            </div>
          )}
          <div className="space-y-4">
            <header className="flex items-center justify-between"><div><h1 className="text-xl font-bold">Live trading</h1><p className="text-xs text-slate-400">Real orders · Kotak Neo</p></div><div className="flex gap-4 text-sm"><PaperWorkspaceLink /><button onClick={handleRefresh}>Refresh</button><button onClick={logout}>Logout</button></div></header>
            
            <WatchlistPanel
              view="indices"
              onSelectStock={handleWatchlistStockClick}
              onOrderRequest={handleOrderRequest}
              onSelectIndex={handleWatchlistIndexClick}
              onPreviewIndex={previewIndex}
              selectedSymbol={selectedSymbol?.symbol}
            />

            <OrderAcknowledgementStatus />
            <ManagedTradesPanel />
            <PositionsPanel onExitPosition={handleExitPosition} />
            <OrdersPanel />

            {selectedSymbol?.option_chain_symbol && (
              <OptionChainPanel key={selectedSymbol.option_chain_symbol} symbol={selectedSymbol.option_chain_symbol} displayName={selectedSymbol.name} />
            )}


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
