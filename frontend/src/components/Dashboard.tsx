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
import { ScreenerPanel } from "./ScreenerPanel";
import { useState } from "react";
import { apiClient } from "../services/api";

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
    setSelectedSymbol(symbol);
    setSearchQuery(symbol.symbol);
  };

  const handleWatchlistClick = (value: string) => {
    setSearchQuery(value);
    setSelectedSymbol({ symbol: value });
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <div className="flex min-h-screen">
        <aside className="w-72 border-r border-slate-800 bg-slate-900/80 p-4">
          <div className="mb-8">
            <h1 className="text-xl font-bold">Kotak Neo Terminal</h1>
            <p className="text-sm text-slate-400">Personal trading dashboard</p>
          </div>

          <div className="mb-6">
            <h2 className="mb-3 text-xs uppercase tracking-wide text-slate-400">Margin</h2>
            <MarginPanel />
          </div>

          <div className="mb-6">
            <h2 className="mb-2 text-xs uppercase tracking-wide text-slate-400">Watchlist</h2>
            <ul className="space-y-2 text-sm">
              <li onClick={() => handleWatchlistClick("NIFTY 50")} className="cursor-pointer rounded-md bg-slate-800 px-3 py-2 hover:bg-slate-700">NIFTY 50</li>
              <li onClick={() => handleWatchlistClick("BANKNIFTY")} className="cursor-pointer rounded-md bg-slate-800 px-3 py-2 hover:bg-slate-700">BANKNIFTY</li>
              <li onClick={() => handleWatchlistClick("RELIANCE")} className="cursor-pointer rounded-md bg-slate-800 px-3 py-2 hover:bg-slate-700">RELIANCE</li>
            </ul>
          </div>

          <div className="mb-6">
            <h2 className="mb-2 text-xs uppercase tracking-wide text-slate-400">Search</h2>
            <SearchSuggestions onSelectSymbol={handleSelectSymbol} />
          </div>

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
        </aside>

        <main className="flex-1 overflow-auto p-4">
          <div className="space-y-4">
            <ScreenerPanel />

            {/* Option Chain Panel */}
            {selectedSymbol && (
              <OptionChainPanel symbol={selectedSymbol.symbol} />
            )}

            {/* Search Results */}
            {searchQuery && searchResults.length > 0 && (
              <SearchResultsPanel 
                results={searchResults} 
                query={searchQuery} 
                onSelectResult={handleSelectSymbol}
              />
            )}

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
              <div className="lg:col-span-2">
                <OrdersPanel />
              </div>
              <div>
                <PositionsPanel />
              </div>
            </div>

            <div>
              <HoldingsPanel />
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
