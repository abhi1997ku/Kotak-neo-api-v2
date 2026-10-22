import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient, type Position, type Trade } from "../services/api";
import { TrendingDown, TrendingUp } from "lucide-react";

type LivePositionQuote = { ltp: number; source: "snapshot" | "stream" };
interface PositionsPanelProps {
  onExitPosition: (position: Position) => void;
}

const normalizeSymbol = (symbol: string) => symbol.toUpperCase().replace(/-EQ$/, "");

interface ClosedTradeSummary {
  symbol: string;
  direction: "LONG" | "SHORT";
  quantity: number;
  average_entry: number;
  average_exit: number;
  realized_pnl: number;
  closed_at: string;
}

interface OpenFill {
  remaining: number;
  price: number;
}

function tradeTimestamp(value: string): number {
  const normalized = value.replace(/^(\d{2})-([A-Za-z]{3})-(\d{4})/, "$2 $1, $3");
  const timestamp = Date.parse(normalized);
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

function summarizeClosedTrades(trades: Trade[]): ClosedTradeSummary[] {
  const sorted = trades
    .map((trade, index) => ({ trade, index }))
    .filter(({ trade }) => trade.qty > 0 && trade.price > 0 && ["BUY", "SELL"].includes(trade.side))
    .sort((left, right) => {
      const timeDiff = tradeTimestamp(left.trade.timestamp) - tradeTimestamp(right.trade.timestamp);
      return timeDiff || left.index - right.index;
    })
    .map(({ trade }) => trade);

  const inventory = new Map<string, { long: OpenFill[]; short: OpenFill[] }>();
  const realized = new Map<string, ClosedTradeSummary & { entry_value: number; exit_value: number }>();

  const recordMatch = (
    symbol: string,
    direction: "LONG" | "SHORT",
    opening: OpenFill,
    closing: Trade,
    quantity: number,
  ) => {
    const key = `${symbol}|${direction}`;
    const summary = realized.get(key) || {
      symbol,
      direction,
      quantity: 0,
      average_entry: 0,
      average_exit: 0,
      realized_pnl: 0,
      closed_at: closing.timestamp,
      entry_value: 0,
      exit_value: 0,
    };
    summary.quantity += quantity;
    summary.entry_value += opening.price * quantity;
    summary.exit_value += closing.price * quantity;
    summary.realized_pnl += (direction === "LONG" ? closing.price - opening.price : opening.price - closing.price) * quantity;
    if (tradeTimestamp(closing.timestamp) >= tradeTimestamp(summary.closed_at)) summary.closed_at = closing.timestamp;
    summary.average_entry = summary.entry_value / summary.quantity;
    summary.average_exit = summary.exit_value / summary.quantity;
    realized.set(key, summary);
  };

  for (const trade of sorted) {
    const symbol = normalizeSymbol(trade.symbol);
    const state = inventory.get(symbol) || { long: [], short: [] };
    inventory.set(symbol, state);
    let remaining = Math.abs(trade.qty);
    const oppositeLots = trade.side === "BUY" ? state.short : state.long;
    const direction = trade.side === "BUY" ? "SHORT" : "LONG";

    while (remaining > 0 && oppositeLots.length > 0) {
      const opening = oppositeLots[0];
      const matched = Math.min(remaining, opening.remaining);
      recordMatch(symbol, direction, opening, trade, matched);
      opening.remaining -= matched;
      remaining -= matched;
      if (opening.remaining === 0) oppositeLots.shift();
    }

    if (remaining > 0) {
      const ownLots = trade.side === "BUY" ? state.long : state.short;
      ownLots.push({ remaining, price: trade.price });
    }
  }

  return [...realized.values()]
    .map(({ entry_value, exit_value, ...summary }) => summary)
    .sort((left, right) => tradeTimestamp(right.closed_at) - tradeTimestamp(left.closed_at));
}

export function PositionsPanel({ onExitPosition }: PositionsPanelProps) {
  const tradeListRef = useRef<HTMLDivElement>(null);
  const [tradeListMaxHeight, setTradeListMaxHeight] = useState<number | null>(null);
  const { data, isLoading, error } = useQuery({
    queryKey: ["positions"],
    queryFn: () => apiClient.getPositions(),
    refetchInterval: 5000,
  });
  const rawPositions = data?.positions || [];
  const { data: tradeData } = useQuery({
    queryKey: ["trade-book"],
    queryFn: () => apiClient.getTradeBook(),
    refetchInterval: 10000,
  });
  const closedTrades = useMemo(() => summarizeClosedTrades(tradeData?.trades || []), [tradeData?.trades]);

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

  useLayoutEffect(() => {
    const list = tradeListRef.current;
    if (!list) return;

    const measureVisibleRows = () => {
      const cards = list.querySelectorAll<HTMLElement>("[data-position-card]");
      if (cards.length <= 4) {
        setTradeListMaxHeight((current) => current === null ? current : null);
        return;
      }
      const listTop = list.getBoundingClientRect().top;
      const fourthCardBottom = cards[3].getBoundingClientRect().bottom;
      const nextHeight = Math.ceil(fourthCardBottom - listTop);
      setTradeListMaxHeight((current) => current === nextHeight ? current : nextHeight);
    };

    measureVisibleRows();
    const observer = new ResizeObserver(measureVisibleRows);
    observer.observe(list);
    list.querySelectorAll<HTMLElement>("[data-position-card]").forEach((card) => observer.observe(card));
    window.addEventListener("resize", measureVisibleRows);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measureVisibleRows);
    };
  }, [rawPositions, closedTrades]);

  if (isLoading) {
    return (
      <div className="flex min-h-36 flex-col rounded-xl border border-slate-800 bg-slate-900 p-4">
        <h3 className="mb-3 text-sm uppercase tracking-wide text-slate-400">Positions</h3>
        <div className="flex flex-1 items-center justify-center text-center text-sm text-slate-500">Loading...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex min-h-36 flex-col rounded-xl border border-slate-800 bg-slate-900 p-4">
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
  const openPnlResult = positions.reduce((result, position) => {
    const ltp = liveQuotes[normalizeSymbol(position.symbol)]?.ltp || position.current_price || 0;
    const averagePrice = position.avg_price > 0 ? position.avg_price : 0;
    const quantity = Math.sign(position.qty) * Math.abs(position.order_quantity || position.qty);
    if (ltp > 0 && averagePrice > 0) {
      result.value += (ltp - averagePrice) * quantity;
    } else if (position.pnl !== 0 && Number.isFinite(position.pnl)) {
      result.value += position.pnl;
    } else {
      result.complete = false;
    }
    return result;
  }, { value: 0, complete: true });
  const closedPnl = tradeData
    ? closedTrades.reduce((total, trade) => total + trade.realized_pnl, 0)
    : null;
  const dayPnl = closedPnl !== null && openPnlResult.complete
    ? closedPnl + openPnlResult.value
    : null;
  const pnlLabel = (value: number | null) => value === null
    ? "—"
    : `${value > 0 ? "+" : value < 0 ? "-" : ""}₹${Math.abs(value).toFixed(2)}`;
  const pnlColor = (value: number | null) => value === null
    ? "text-slate-400"
    : value >= 0 ? "text-emerald-400" : "text-red-400";
  const displayedTradeCount = positions.length + closedTrades.length;
  const shouldScrollTrades = displayedTradeCount > 4;

  return (
    <div className="flex flex-col overflow-hidden rounded-xl border border-slate-800 bg-slate-900 p-4">
      <div className="mb-3 flex shrink-0 items-center justify-between">
        <h3 className="text-sm uppercase tracking-wide text-slate-400">Positions</h3>
        {positions.length > 0 && (
          <span className={`text-[10px] font-semibold uppercase tracking-wide ${feedState === "live" ? "text-emerald-400" : "text-slate-500"}`}>
            {feedState === "live" ? "Live LTP" : feedState === "connecting" || feedState === "reconnecting" ? "Connecting LTP" : "LTP unavailable"}
          </span>
        )}
      </div>
      <div className="mb-3 grid shrink-0 grid-cols-3 gap-2 rounded-lg border border-slate-800 bg-slate-950/70 px-3 py-2 text-xs">
        <div>
          <p className="text-[10px] uppercase tracking-wide text-slate-500">Open P&amp;L</p>
          <p className={`mt-0.5 font-semibold tabular-nums ${openPnlResult.complete ? pnlColor(openPnlResult.value) : "text-slate-400"}`}>
            {pnlLabel(openPnlResult.complete ? openPnlResult.value : null)}
          </p>
        </div>
        <div>
          <p className="text-[10px] uppercase tracking-wide text-slate-500">Closed P&amp;L</p>
          <p className={`mt-0.5 font-semibold tabular-nums ${pnlColor(closedPnl)}`}>{pnlLabel(closedPnl)}</p>
        </div>
        <div>
          <p className="text-[10px] uppercase tracking-wide text-slate-500">Day P&amp;L</p>
          <p className={`mt-0.5 font-semibold tabular-nums ${pnlColor(dayPnl)}`}>{pnlLabel(dayPnl)}</p>
        </div>
        <p className="col-span-3 text-[9px] leading-3 text-slate-500">Open + closed gross P&amp;L; charges excluded. Open P&amp;L uses average entry price.</p>
      </div>
      {positions.length === 0 && closedTrades.length === 0 ? (
        <div className="py-8 text-center text-sm text-slate-500">No open positions</div>
      ) : (
        <div
          ref={tradeListRef}
          style={shouldScrollTrades && tradeListMaxHeight !== null ? { maxHeight: `${tradeListMaxHeight}px` } : undefined}
          className={`min-h-0 space-y-2 pr-1 ${shouldScrollTrades ? "overflow-y-auto" : ""}`}
        >
          {positions.length > 0 && <section aria-label="Open positions" className="space-y-2">
            {positions.map((pos: Position) => {
            const averagePrice = pos.avg_price > 0 ? pos.avg_price : 0;
            const quote = liveQuotes[normalizeSymbol(pos.symbol)];
            const ltp = quote?.ltp || pos.current_price || 0;
            // Kotak's FO normalization keeps qty in lots for display while
            // order_quantity retains the actual contract units (e.g. 2 lots = 130).
            const pnlQuantity = Math.sign(pos.qty) * Math.abs(pos.order_quantity || pos.qty);
            const hasCalculatedPnl = ltp > 0 && averagePrice > 0;
            const pnl = hasCalculatedPnl ? (ltp - averagePrice) * pnlQuantity : pos.pnl;
            const pnlPct = hasCalculatedPnl && averagePrice > 0 && pos.qty !== 0
              ? pnl / (averagePrice * Math.abs(pnlQuantity)) * 100
              : pos.pnl_pct;
            const isProfit = pnl >= 0;
            const isDerivative = Boolean(pos.exchange_segment?.endsWith("_fo"));
            const quantityLabel = isDerivative && Math.abs(pos.order_quantity || 0) !== Math.abs(pos.qty)
              ? `${Math.abs(pos.qty)} lots (${Math.abs(pos.order_quantity || 0)} units)`
              : `${pos.qty} units`;

            return (
              <div data-position-card key={pos.symbol} className="flex items-center justify-between rounded-lg border border-slate-700 bg-slate-800 p-3 text-sm">
                <div>
                  <p className="font-medium">{pos.symbol}</p>
                  <p className="text-xs text-slate-400">
                    {quantityLabel} @ {averagePrice > 0 ? <> &#8377;{averagePrice.toFixed(2)}</> : "price pending"}
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
          </section>}
          {closedTrades.length > 0 && <section aria-label="Closed trades" className="space-y-2">
            <h4 className="pt-2 text-[10px] font-semibold uppercase tracking-wide text-slate-400">Closed trades</h4>
            <p className="text-[10px] text-slate-500">Realized P&amp;L from matched fills; charges are excluded.</p>
            {closedTrades.map((trade) => {
              const isProfit = trade.realized_pnl >= 0;
              const pnlPct = trade.average_entry > 0 ? trade.realized_pnl / (trade.average_entry * trade.quantity) * 100 : 0;
              return (
                <div data-position-card key={`${trade.symbol}-${trade.direction}`} className="flex items-center justify-between gap-2 rounded-lg border border-slate-700 bg-slate-800/70 p-3 text-sm">
                  <div className="min-w-0">
                    <p className="truncate font-medium">{trade.symbol} <span className="text-[10px] text-slate-400">{trade.direction} · CLOSED</span></p>
                    <p className="mt-1 text-xs text-slate-400">{trade.quantity} units · Avg {trade.average_entry.toFixed(2)} → {trade.average_exit.toFixed(2)}</p>
                    <p className="mt-1 text-[10px] text-slate-500">{trade.closed_at}</p>
                  </div>
                  <div className={`shrink-0 text-right font-semibold tabular-nums ${isProfit ? "text-emerald-400" : "text-red-400"}`}>
                    <p>{trade.realized_pnl > 0 ? "+" : trade.realized_pnl < 0 ? "-" : ""}&#8377;{Math.abs(trade.realized_pnl).toFixed(2)}</p>
                    <p className="text-xs">{pnlPct > 0 ? "+" : ""}{pnlPct.toFixed(2)}%</p>
                  </div>
                </div>
              );
            })}
          </section>}
        </div>
      )}
    </div>
  );
}
