interface SearchResult {
  symbol: string;
  name: string;
  exchange: string;
}

interface SearchResultsPanelProps {
  results: SearchResult[];
  query: string;
  onSelectResult: (symbol: SearchResult) => void;
}

export function SearchResultsPanel({ results, query, onSelectResult }: SearchResultsPanelProps) {
  if (!query || results.length === 0) {
    return null;
  }

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900 p-4">
      <h3 className="mb-3 text-sm uppercase tracking-wide text-slate-400">
        Search Results for "{query}"
      </h3>
      <div className="space-y-2">
        {results.map((result) => (
          <button
            key={`${result.exchange}-${result.symbol}`}
            onClick={() => onSelectResult(result)}
            className="w-full text-left rounded-lg border border-slate-700 bg-slate-800 p-3 hover:bg-slate-700/80 transition-colors"
          >
            <div className="flex items-center justify-between">
              <div>
                <p className="font-semibold text-slate-100">{result.symbol}</p>
                <p className="text-xs text-slate-400">{result.name}</p>
              </div>
              <span className="text-xs text-slate-500">{result.exchange}</span>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
