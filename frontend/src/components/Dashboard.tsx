import { LogOut, RefreshCw } from "lucide-react";
import { useAuth } from "../contexts/AuthContext";
import { useQueryClient } from "@tanstack/react-query";
import { PositionsPanel } from "./PositionsPanel";
import { HoldingsPanel } from "./HoldingsPanel";
import { OrdersPanel } from "./OrdersPanel";
import { MarginPanel } from "./MarginPanel";
import { SearchSuggestions } from "./SearchSuggestions";
import { SearchResultsPanel } from "./SearchResultsPanel";
import { OptionChainPanel } from "./OptionChainPanel";
import { EquityOrderPanel } from "./EquityOrderPanel";
import { ScreenerPanel } from "./ScreenerPanel";
import { WatchlistPanel } from "./WatchlistPanel";
import { useState } from "react";
import { type WatchlistItem } from "../services/api";

export function Dashboard() {
  const { logout } = useAuth();
  const queryClient = useQueryClient();
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<any[]>([]);
  const [selectedSymbol, setSelectedSymbol] = useState<any | null>(null);

  const handleRefresh = () => {
    queryClient.refetchQueries();
  };

  const handleLogout = () => {
    logout();
  };

  const handleSelectSymbol = async (symbol: any) => {
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
  };

  const handleWatchlistStockClick = (stock: WatchlistItem) => {
    setSearchQuery(stock.symbol);
    setSelectedSymbol({
      symbol: stock.symbol,
      trading_symbol: stock.trading_symbol || `${stock.symbol}-EQ`,
      instrument_token: stock.instrument_token,
      name: stock.name,
      exchange: "NSE",
    });
  };

  const handleWatchlistIndexClick = (index: WatchlistItem) => {
    handleSelectSymbol({
      ...index,
      name: index.name,
      exchange: "INDEX",
    });
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <div className="flex min-h-screen">
        <aside className="h-screen w-72 shrink-0 self-start overflow-y-auto border-r border-slate-800 bg-slate-900/80 p-4">
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
            <SearchSuggestions onSelectSymbol={handleSelectSymbol} />
          </div>

          {searchQuery && searchResults.length > 0 && (
            <SearchResultsPanel
              results={searchResults}
              query={searchQuery}
              onSelectResult={handleSelectSymbol}
            />
          )}

          <div className="space-y-2 border-t border-slate-800 pt-4">
            <button
              onClick={handleRefresh}
              className="flex w-full items-center gap-2 rounded-md bg-slate-800 px-3 py-2 text-sm hover:bg-slate-700"
            >
              <RefreshCw size={16} />
              Refresh
            </button>
            <button
              onClick={handleLogout}
              className="flex w-full items-center gap-2 rounded-md bg-red-900/30 px-3 py-2 text-sm text-red-300 hover:bg-red-900/50"
            >
              <LogOut size={16} />
              Logout
            </button>
          </div>

          <div className="mt-6 border-t border-slate-800 pt-4">
            <ScreenerPanel />
          </div>
        </aside>

        <main className="flex-1 overflow-auto p-4">
          <div className="space-y-4">
            <WatchlistPanel
              view="indices"
              onSelectStock={handleWatchlistStockClick}
              onSelectIndex={handleWatchlistIndexClick}
              selectedSymbol={selectedSymbol?.symbol}
            />

            <PositionsPanel />
            <OrdersPanel />

            {selectedSymbol?.option_chain_symbol && (
              <OptionChainPanel symbol={selectedSymbol.option_chain_symbol} displayName={selectedSymbol.name} />
            )}

            <HoldingsPanel />

            {selectedSymbol?.exchange === "NSE" && !selectedSymbol.option_chain_symbol && (
              <EquityOrderPanel
                symbol={selectedSymbol.symbol}
                tradingSymbol={selectedSymbol.trading_symbol}
                name={selectedSymbol.name}
                instrumentToken={selectedSymbol.instrument_token}
              />
            )}

            <WatchlistPanel
              view="stocks"
              onSelectStock={handleWatchlistStockClick}
              onSelectIndex={handleWatchlistIndexClick}
              selectedSymbol={selectedSymbol?.symbol}
            />
          </div>
        </main>
      </div>
    </div>
  );
}
