import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Search } from "lucide-react";
import { apiClient, type WatchlistData, type WatchlistItem } from "../services/api";

interface WatchlistPanelProps {
  onSelectStock: (stock: WatchlistItem) => void;
  onSelectIndex: (index: WatchlistItem) => void;
  selectedSymbol?: string;
  view?: "all" | "indices" | "stocks";
}

function formatPrice(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value) || value <= 0) return "—";
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

function formatChange(value: number | null | undefined, suffix = "") {
  if (value == null || !Number.isFinite(value)) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}${suffix}`;
}

function changeColor(value: number | null | undefined) {
  if (value == null || value === 0) return "text-slate-400";
  return value > 0 ? "text-emerald-300" : "text-red-300";
}

export function WatchlistPanel({ onSelectStock, onSelectIndex, selectedSymbol, view = "all" }: WatchlistPanelProps) {
  const [filter, setFilter] = useState("");
  const [feedState, setFeedState] = useState("connecting");
  const [marketStatus, setMarketStatus] = useState("");
  const [feedMessage, setFeedMessage] = useState("");
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["watchlist"],
    queryFn: () => apiClient.getWatchlist(),
    staleTime: Infinity,
  });

  useEffect(() => {
    if (view === "stocks") return;
    let disposed = false;
    let reconnectTimer: number | undefined;
    let socket: WebSocket | undefined;
    let streamFailed = false;

    const connect = () => {
      if (disposed) return;
      streamFailed = false;
      setFeedState("connecting");
      socket = apiClient.createWatchlistStream();
      socket.onopen = () => setFeedState("connecting");
      socket.onmessage = (message) => {
        let event: any;
        try {
          event = JSON.parse(message.data);
        } catch {
          return;
        }

        if (event.type === "status") {
          setFeedState(event.state ?? "connecting");
          if (event.message) setFeedMessage(event.message);
          if (event.state === "live") {
            setFeedMessage("");
            if (queryClient.getQueryData<WatchlistData>(["watchlist"])?.catalog_warning) {
              void queryClient.invalidateQueries({ queryKey: ["watchlist"] });
            }
          }
          if (event.state === "partial") {
            const current = queryClient.getQueryData<WatchlistData>(["watchlist"]);
            if (
              !current
              || typeof current.stock_token_count !== "number"
              || current.catalog_warning !== event.message
            ) {
              void queryClient.invalidateQueries({ queryKey: ["watchlist"] });
            }
          }
          if (event.state === "unauthorized") {
            window.dispatchEvent(new Event("kotak:unauthorized"));
          }
          if (event.state === "error" || event.state === "unauthorized") streamFailed = true;
          return;
        }

        if (event.type === "market_status") {
          setMarketStatus(event.status ?? "");
          return;
        }

        if (event.type === "quote" && typeof event.symbol === "string") {
          queryClient.setQueryData<WatchlistData>(["watchlist"], (current) => {
            if (!current) return current;
            const applyTick = (items: WatchlistItem[]) => items.map((item) =>
              item.symbol === event.symbol
                ? { ...item, ltp: event.ltp, change: event.change, change_pct: event.change_pct }
                : item
            );
            return {
              ...current,
              indices: applyTick(current.indices),
              stocks: applyTick(current.stocks),
              updated_at: event.updated_at ?? current.updated_at,
            };
          });
        }
      };
      socket.onerror = () => {
        streamFailed = true;
        setFeedState("error");
      };
      socket.onclose = () => {
        if (disposed) return;
        setFeedState(streamFailed ? "error" : "reconnecting");
        reconnectTimer = window.setTimeout(connect, streamFailed ? 15000 : 3000);
      };
    };

    connect();
    return () => {
      disposed = true;
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [queryClient, view]);

  const visibleStocks = useMemo(() => {
    const query = filter.trim().toLowerCase();
    return (data?.stocks ?? []).filter(
      (stock) => !query || stock.symbol.toLowerCase().includes(query) || stock.name.toLowerCase().includes(query)
    );
  }, [data?.stocks, filter]);

  const feedLabel = feedState === "live"
    ? marketStatus || "Live feed connected"
    : feedState === "partial"
      ? data?.stock_token_count
        ? `Live for ${data.stock_token_count}/${data.constituent_count} stocks`
        : "Index feed connected · waiting for stock tokens"
      : feedState === "connecting"
        ? "Connecting to Kotak live feed"
        : feedState === "error"
          ? "Live feed unavailable · reconnecting"
          : "Live feed reconnecting";

  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900 p-4">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm uppercase tracking-wide text-slate-400">
            {view === "indices" ? "Market indices" : view === "stocks" ? "Nifty 50 stocks" : "Market watchlist"}
          </h2>
          <p className="mt-1 text-xs text-slate-500">
            {view === "indices" ? "Nifty 50, Nifty Bank, and Sensex · select an index to open its option chain" : view === "stocks" ? "Live prices for all 50 Nifty 50 constituents" : "Nifty 50, Nifty Bank, Sensex and all 50 Nifty 50 constituents"}
          </p>
        </div>
        {view !== "stocks" && <div className="flex items-center gap-2 text-xs">
          <span className={`flex items-center gap-1 rounded-full px-2 py-1 ${feedState === "live" ? "bg-emerald-500/10 text-emerald-300" : feedState === "partial" ? "bg-amber-500/10 text-amber-300" : "bg-slate-800 text-slate-400"}`}>
            <Activity size={13} />
            {feedLabel}
          </span>
        </div>}
      </div>

      {error && (
        <div className="mb-4 rounded-md border border-red-500/20 bg-red-500/10 p-3 text-sm text-red-300">
          {error instanceof Error ? error.message : "Watchlist quotes could not be loaded."}
        </div>
      )}

      {view !== "stocks" && (data?.catalog_warning || feedMessage) && (
        <div className="mb-4 rounded-md border border-amber-500/20 bg-amber-500/10 p-3 text-sm text-amber-200">
          {data?.catalog_warning || feedMessage}
        </div>
      )}

      {view !== "stocks" && <div className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
        {(data?.indices ?? []).map((index) => (
          <button
            key={index.symbol}
            type="button"
            onClick={() => onSelectIndex(index)}
            aria-label={`Open ${index.name} option chain`}
            className={`rounded-lg border p-3 text-left transition-colors hover:border-sky-500/50 hover:bg-slate-900 ${selectedSymbol === index.symbol ? "border-sky-500/60 bg-sky-500/10" : "border-slate-800 bg-slate-950/70"}`}
          >
            <div className="text-xs uppercase tracking-wide text-slate-500">{index.name}</div>
            <div className="mt-2 flex items-baseline justify-between gap-2">
              <span className="text-lg font-semibold text-slate-100">{formatPrice(index.ltp)}</span>
              <span className={`text-xs font-medium ${changeColor(index.change_pct)}`}>
                {formatChange(index.change_pct, "%")}
              </span>
            </div>
            <div className={`mt-1 text-xs ${changeColor(index.change)}`}>
              {formatChange(index.change)} today
            </div>
            <div className="mt-2 text-[11px] font-medium text-sky-300">Open option chain</div>
          </button>
        ))}
        {!data && isLoading && [0, 1, 2].map((item) => (
          <div key={item} className="h-24 animate-pulse rounded-lg border border-slate-800 bg-slate-950/70" />
        ))}
      </div>}

      {view !== "indices" && <>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-200">{view === "stocks" ? "All 50 constituents" : "Nifty 50 stocks"}</h3>
          <p className="mt-1 text-xs text-slate-500">
            {data ? `${data.constituent_count} constituents · ${data.constituents_source}` : "Official index constituents"}
            {data?.updated_at ? ` · Updated ${new Date(data.updated_at).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", second: "2-digit" })}` : ""}
          </p>
        </div>
        <label className="flex min-w-52 items-center gap-2 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-400">
          <Search size={15} />
          <input
            aria-label="Filter Nifty 50 stocks"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="Find a stock"
            className="w-full bg-transparent text-sm text-slate-100 outline-none placeholder:text-slate-500"
          />
        </label>
      </div>

      <div className="max-h-[30rem] overflow-auto rounded-lg border border-slate-800">
        <table className="w-full min-w-[620px] text-sm">
          <thead className="sticky top-0 bg-slate-950 text-left text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-3 py-2.5">Symbol</th>
              <th className="px-3 py-2.5">Company</th>
              <th className="px-3 py-2.5 text-right">Live price</th>
              <th className="px-3 py-2.5 text-right">Change</th>
              <th className="px-3 py-2.5 text-right">%</th>
            </tr>
          </thead>
          <tbody>
            {visibleStocks.map((stock) => {
              const selected = selectedSymbol === stock.symbol;
              return (
                <tr
                  key={stock.symbol}
                  onClick={() => onSelectStock(stock)}
                  className={`cursor-pointer border-t border-slate-800/80 hover:bg-slate-800/70 ${selected ? "bg-sky-500/10" : ""}`}
                  title={`Select ${stock.symbol} for an order`}
                >
                  <td className="px-3 py-2.5 font-semibold text-slate-100">{stock.symbol}</td>
                  <td className="max-w-64 truncate px-3 py-2.5 text-slate-400">{stock.name}</td>
                  <td className="px-3 py-2.5 text-right font-medium tabular-nums text-slate-100">{formatPrice(stock.ltp)}</td>
                  <td className={`px-3 py-2.5 text-right tabular-nums ${changeColor(stock.change)}`}>{formatChange(stock.change)}</td>
                  <td className={`px-3 py-2.5 text-right tabular-nums ${changeColor(stock.change_pct)}`}>{formatChange(stock.change_pct, "%")}</td>
                </tr>
              );
            })}
            {!isLoading && !error && visibleStocks.length === 0 && (
              <tr><td colSpan={5} className="px-3 py-8 text-center text-sm text-slate-500">No matching Nifty 50 stocks.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-slate-500">Select a stock row to open its order panel. Prices update from Kotak's live feed during market hours.</p>
      </>}
    </section>
  );
}
