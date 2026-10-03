import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient, type Position } from "../services/api";
import { TrendingDown, TrendingUp } from "lucide-react";

type LivePositionQuote = { ltp: number; source: "snapshot" | "stream" };
interface PositionsPanelProps {
  onExitPosition: (position: Position) => void;
}

const normalizeSymbol = (symbol: string) => symbol.toUpperCase().replace(/-EQ$/, "");

export function PositionsPanel({ onExitPosition }: PositionsPanelProps) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["positions"],
    queryFn: () => apiClient.getPositions(),
    refetchInterval: 5000,
  });
  const rawPositions = data?.positions || [];
  const needsTradePrice = rawPositions.some((position) => position.qty !== 0 && !(position.avg_price > 0));
  const { data: tradeData } = useQuery({
    queryKey: ["trade-book", "position-price-fallback"],
    queryFn: () => apiClient.getTradeBook(),
    enabled: needsTradePrice,
    refetchInterval: needsTradePrice ? 10000 : false,
  });

  const positionTokenKey = JSON.stringify(rawPositions
    .filter((position) => position.instrument_token && position.exchange_segment)
    .map((position) => ({
      symbol: position.symbol,
      exchange_segment: position.exchange_segment!,
      instrument_token: position.instrument_token!,
    })));
  const [liveQuotes, setLiveQuotes] = useState<Record<string, LivePositionQuote>>({});
  const [feedState, setFeedState] = useState("waiting");

  useEffect(() => {
    const tokens = JSON.parse(positionTokenKey) as Array<{
      symbol: string;
      exchange_segment: string;
      instrument_token: string;
    }>;
    setLiveQuotes({});
    if (tokens.length === 0) {
      setFeedState("unavailable");
      return;
    }

    let stopped = false;
    let unauthorized = false;
    let reconnectTimer: number | undefined;
    let socket: WebSocket | undefined;

    const connect = () => {
      if (stopped) return;
      setFeedState("connecting");
      socket = apiClient.createPositionStream(tokens);
      socket.onopen = () => setFeedState("connecting");
      socket.onmessage = (message) => {
        let event: any;
        try {
          event = JSON.parse(message.data);
        } catch {
          return;
        }
        if (event.type === "status") {
          if (event.state === "unauthorized") {
            unauthorized = true;
            window.dispatchEvent(new Event("kotak:unauthorized"));
          }
          if (event.state === "error") setFeedState("unavailable");
          else if (event.state === "connected") setFeedState("connected");
          return;
        }
        if (event.type !== "quote" || typeof event.symbol !== "string" || !(Number(event.ltp) > 0)) return;

        setLiveQuotes((current) => ({
          ...current,
          [normalizeSymbol(event.symbol)]: {
            ltp: Number(event.ltp),
            source: event.source === "stream" ? "stream" : "snapshot",
          },
        }));
        if (event.source === "stream") setFeedState("live");
      };
      socket.onerror = () => setFeedState("reconnecting");
      socket.onclose = () => {
        if (stopped || unauthorized) return;
        setFeedState("reconnecting");
        reconnectTimer = window.setTimeout(connect, 3000);
      };
    };

    connect();
    return () => {
      stopped = true;
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [positionTokenKey]);

  if (isLoading) {
    return (
      <div className="flex h-64 flex-col rounded-xl border border-slate-800 bg-slate-900 p-4">
        <h3 className="mb-3 text-sm uppercase tracking-wide text-slate-400">Positions</h3>
        <div className="flex flex-1 items-center justify-center text-center text-sm text-slate-500">Loading...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-64 flex-col rounded-xl border border-slate-800 bg-slate-900 p-4">
        <h3 className="mb-3 text-sm uppercase tracking-wide text-slate-400">Positions</h3>
        <div className="flex flex-1 items-center rounded bg-red-500/10 p-2 text-xs text-red-300">
          {error instanceof Error ? error.message : "Error loading positions"}
        </div>
      </div>
    );
  }

  const positions = rawPositions.map((position) => {
    if (position.avg_price > 0) return position;
    const expectedSide = position.qty > 0 ? "BUY" : "SELL";
    const symbol = normalizeSymbol(position.symbol);
    const fills = (tradeData?.trades || []).filter((trade) =>
      trade.side === expectedSide &&
      normalizeSymbol(trade.symbol) === symbol &&
      trade.qty > 0 &&
      trade.price > 0
    );
    const fillQuantity = fills.reduce((total, trade) => total + trade.qty, 0);
    if (!fillQuantity) return position;
    const averageFillPrice = fills.reduce((total, trade) => total + trade.qty * trade.price, 0) / fillQuantity;
    return { ...position, avg_price: averageFillPrice };
  });

  return (
    <div className="flex h-64 flex-col overflow-hidden rounded-xl border border-slate-800 bg-slate-900 p-4">
      <div className="mb-3 flex shrink-0 items-center justify-between">
        <h3 className="text-sm uppercase tracking-wide text-slate-400">Positions</h3>
        {positions.length > 0 && (
          <span className={`text-[10px] font-semibold uppercase tracking-wide ${feedState === "live" ? "text-emerald-400" : "text-slate-500"}`}>
            {feedState === "live" ? "Live LTP" : feedState === "connecting" || feedState === "reconnecting" ? "Connecting LTP" : "LTP unavailable"}
          </span>
        )}
      </div>
      {positions.length === 0 ? (
        <div className="flex min-h-0 flex-1 items-center justify-center text-center text-sm text-slate-500">No open positions</div>
      ) : (
        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
          {positions.map((pos: Position) => {
            const averagePrice = pos.avg_price > 0 ? pos.avg_price : 0;
            const quote = liveQuotes[normalizeSymbol(pos.symbol)];
            const ltp = quote?.ltp || pos.current_price || 0;
            const hasCalculatedPnl = ltp > 0 && averagePrice > 0;
            const pnl = hasCalculatedPnl ? (ltp - averagePrice) * pos.qty : pos.pnl;
            const pnlPct = hasCalculatedPnl && averagePrice > 0 && pos.qty !== 0
              ? pnl / (averagePrice * Math.abs(pos.qty)) * 100
              : pos.pnl_pct;
            const isProfit = pnl >= 0;

            return (
              <div key={pos.symbol} className="flex items-center justify-between rounded-lg border border-slate-700 bg-slate-800 p-3 text-sm">
                <div>
                  <p className="font-medium">{pos.symbol}</p>
                  <p className="text-xs text-slate-400">
                    {pos.qty} units @ {averagePrice > 0 ? <> &#8377;{averagePrice.toFixed(2)}</> : "price pending"}
                  </p>
                  <p className="mt-1 text-xs text-slate-300">
                    LTP {ltp > 0 ? <> &#8377;{ltp.toFixed(2)}</> : "pending"}
                    {quote?.source === "stream" && <span className="ml-1 text-emerald-400">LIVE</span>}
                    {quote?.source === "snapshot" && <span className="ml-1 text-slate-500">QUOTE</span>}
                  </p>
                </div>
                <div className="text-right">
                  <div className={`flex items-center justify-end gap-1 font-semibold ${isProfit ? "text-emerald-400" : "text-red-400"}`}>
                    {isProfit ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
                    &#8377;{Math.abs(pnl).toFixed(2)}
                  </div>
                  <p className={`text-xs ${isProfit ? "text-emerald-400" : "text-red-400"}`}>
                    {pnlPct > 0 ? "+" : ""}{pnlPct.toFixed(2)}%
                  </p>
                  <button
                    type="button"
                    onClick={() => onExitPosition(pos)}
                    className="mt-1 rounded border border-red-800/80 px-2 py-1 text-[10px] font-semibold text-red-300 hover:bg-red-900/30"
                  >
                    Exit
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
