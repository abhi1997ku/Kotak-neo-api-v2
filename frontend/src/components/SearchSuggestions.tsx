import { useState, useEffect } from "react";
import { Search } from "lucide-react";
import { apiClient } from "../services/api";

export interface SearchInstrument {
  symbol: string;
  name: string;
  exchange: string;
  trading_symbol?: string;
  exchange_segment?: string;
  instrument_token?: string;
}

interface SearchSuggestionsProps {
  onSelectSymbol: (symbol: SearchInstrument) => void;
  onOrderRequest: (symbol: SearchInstrument, side: "BUY" | "SELL") => void;
}

export function SearchSuggestions({ onSelectSymbol, onOrderRequest }: SearchSuggestionsProps) {
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState<SearchInstrument[]>([]);
  const [isOpen, setIsOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    if (!query.trim()) {
      setSuggestions([]);
      setIsOpen(false);
      return;
    }

    const fetchSuggestions = async () => {
      setIsLoading(true);
      try {
        const response = await apiClient.searchSymbol(query);
        setSuggestions(response.results ?? []);
        setIsOpen(true);
      } catch (err) {
        console.error("Search error:", err);
        setSuggestions([]);
      } finally {
        setIsLoading(false);
      }
    };

    const timer = setTimeout(fetchSuggestions, 300); // Debounce
    return () => clearTimeout(timer);
  }, [query]);

  const handleSelectSymbol = (symbol: SearchInstrument) => {
    onSelectSymbol(symbol);
    setQuery("");
    setSuggestions([]);
    setIsOpen(false);
  };

  const handleOrderRequest = (symbol: SearchInstrument, side: "BUY" | "SELL") => {
    onOrderRequest(symbol, side);
    setQuery("");
    setSuggestions([]);
    setIsOpen(false);
  };

  return (
    <div className="relative w-full">
      <div className="relative">
        <Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onFocus={() => suggestions.length > 0 && setIsOpen(true)}
          placeholder="Search symbol, equity, index..."
          className="w-full rounded-md border border-slate-700 bg-slate-800 pl-10 pr-3 py-2 text-sm text-slate-100 placeholder-slate-500 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20"
        />
      </div>

      {/* Suggestions Dropdown */}
      {isOpen && (
        <div className="absolute top-full left-0 right-0 mt-1 max-h-64 overflow-y-auto rounded-md border border-slate-700 bg-slate-800 shadow-lg z-50">
          {isLoading ? (
            <div className="px-3 py-2 text-sm text-slate-400">Loading...</div>
          ) : suggestions.length === 0 ? (
            <div className="px-3 py-2 text-sm text-slate-400">No results found</div>
          ) : (
            <div className="divide-y divide-slate-700">
              {suggestions.map((symbol) => (
                <div key={`${symbol.exchange}-${symbol.symbol}`} className="flex items-center gap-2 px-2 py-2 hover:bg-slate-700/70">
                  <button onClick={() => handleSelectSymbol(symbol)} className="min-w-0 flex-1 text-left">
                    <div className="truncate font-semibold text-slate-100">{symbol.symbol}</div>
                    <div className="truncate text-xs text-slate-400">{symbol.name}</div>
                  </button>
                  <button type="button" onClick={() => handleOrderRequest(symbol, "BUY")} className="rounded bg-emerald-700/80 px-2 py-1 text-[10px] font-semibold text-white hover:bg-emerald-600">Buy</button>
                  <button type="button" onClick={() => handleOrderRequest(symbol, "SELL")} className="rounded bg-red-700/80 px-2 py-1 text-[10px] font-semibold text-white hover:bg-red-600">Sell</button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
