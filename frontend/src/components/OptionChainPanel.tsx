import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, TrendingUp, TrendingDown } from "lucide-react";
import { apiClient, type OptionToken } from "../services/api";
import { OrderTicketModal, type OrderTicketRequest } from "./OrderTicketModal";

interface OptionChainPanelProps {
  symbol: string;
  displayName?: string;
}

interface ChainData {
  strike_price: number;
  call_symbol?: string;
  call_token?: string;
  call_lot_size?: number;
  call_ltp?: number;
  put_symbol?: string;
  put_token?: string;
  put_lot_size?: number;
  put_ltp?: number;
  call_bid?: number;
  call_ask?: number;
  call_volume?: number;
  call_oi?: number;
  put_bid?: number;
  put_ask?: number;
  put_volume?: number;
  put_oi?: number;
}

export function OptionChainPanel({ symbol, displayName }: OptionChainPanelProps) {
  const queryClient = useQueryClient();
  const [expiry, setExpiry] = useState<string>("");
  const [showExpiries, setShowExpiries] = useState(false);
  const [selectedStrike, setSelectedStrike] = useState<number | null>(null);
  const [orderTicket, setOrderTicket] = useState<OrderTicketRequest | null>(null);
  const [feedState, setFeedState] = useState("connecting");

  const openOrderTicket = (side: "BUY" | "SELL", type: "CE" | "PE", chain: ChainData) => {
    setSelectedStrike(chain.strike_price);
    const tradingSymbol = type === "CE" ? chain.call_symbol || "" : chain.put_symbol || "";
    const instrumentToken = type === "CE" ? chain.call_token || "" : chain.put_token || "";
    const lotSize = Number(type === "CE" ? chain.call_lot_size : chain.put_lot_size) || 1;
    const ltp = Number(type === "CE" ? chain.call_ltp : chain.put_ltp);
    setOrderTicket({
      symbol: `${displayName || symbol} ${chain.strike_price} ${type}`,
      tradingSymbol,
      exchangeSegment: symbol === "SENSEX" ? "bse_fo" : "nse_fo",
      instrumentToken: instrumentToken || undefined,
      side,
      quantity: lotSize,
      product: "NRML",
      ltp: ltp > 0 ? ltp : undefined,
      orderType: "L",
      limitPrice: ltp > 0 ? ltp : undefined,
    });
  };

  const { data, isLoading, error } = useQuery({
    queryKey: ["option_chain", symbol, expiry],
    queryFn: async () => {
      if (!symbol) return { chains: [], ltp: 0, symbol: "", expiry: "" };
      return apiClient.getOptionChain(symbol, expiry || undefined);
    },
    enabled: !!symbol,
    staleTime: 0,
    refetchOnMount: "always",
    refetchInterval: false,
  });

  useEffect(() => {
    setSelectedStrike(null);
    setOrderTicket(null);
  }, [expiry, symbol]);

  const tokenSignature = useMemo(() => {
    const exchangeSegment = symbol === "SENSEX" ? "bse_fo" : "nse_fo";
    const tokens = ((data?.chains || []) as ChainData[]).flatMap((chain) => [
      chain.call_token ? `${exchangeSegment}|${chain.call_token}` : "",
      chain.put_token ? `${exchangeSegment}|${chain.put_token}` : "",
    ]).filter(Boolean);
    return [...new Set(tokens)].join(",");
  }, [data?.chains, symbol]);

  useEffect(() => {
    if (!tokenSignature) {
      setFeedState("unavailable");
      return;
    }

    let stopped = false;
    let reconnectTimer: number | undefined;
    let socket: WebSocket | undefined;
    const tokens: OptionToken[] = tokenSignature.split(",").map((entry) => {
      const [exchange_segment, instrument_token] = entry.split("|", 2);
      return { exchange_segment, instrument_token };
    });

    const connect = () => {
      if (stopped) return;
      setFeedState("connecting");
      socket = apiClient.createOptionChainStream(tokens);
      socket.onopen = () => setFeedState("connected");
      socket.onmessage = (message) => {
        let event: any;
        try {
          event = JSON.parse(message.data);
        } catch {
          return;
        }
        if (event.type === "status") {
          if (event.state === "unauthorized") window.dispatchEvent(new Event("kotak:unauthorized"));
          setFeedState(event.state === "connected" ? "connected" : event.state || "unavailable");
          return;
        }
        if (event.type !== "quote" || !event.instrument_token) return;

        setFeedState("live");
        queryClient.setQueryData<any>(["option_chain", symbol, expiry], (current: any) => {
          if (!current?.chains) return current;
          return {
            ...current,
            chains: current.chains.map((chain: ChainData) => {
              if (chain.call_token === event.instrument_token) return { ...chain, call_ltp: event.ltp };
              if (chain.put_token === event.instrument_token) return { ...chain, put_ltp: event.ltp };
              return chain;
            }),
            updated_at: event.updated_at || current.updated_at,
          };
        });
      };
      socket.onerror = () => setFeedState("reconnecting");
      socket.onclose = () => {
        if (stopped) return;
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
  }, [expiry, queryClient, symbol, tokenSignature]);

  if (!symbol) {
    return (
      <div className="rounded-xl border border-slate-800 bg-slate-900 p-4">
        <h3 className="mb-3 text-sm uppercase tracking-wide text-slate-400">Option Chain</h3>
        <div className="text-center text-sm text-slate-500">Select an index/stock from watchlist</div>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="rounded-xl border border-slate-800 bg-slate-900 p-4">
        <h3 className="mb-3 text-sm uppercase tracking-wide text-slate-400">Option Chain - {displayName || symbol}</h3>
        <div className="text-center text-sm text-slate-500">Loading...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-xl border border-slate-800 bg-slate-900 p-4">
        <h3 className="mb-3 text-sm uppercase tracking-wide text-slate-400">Option Chain - {displayName || symbol}</h3>
        <div className="rounded bg-red-500/10 p-2 text-xs text-red-300">
          Failed to load option chain
        </div>
      </div>
    );
  }

  const chains = ((data?.chains || []) as ChainData[]).slice(0, 11);
  const ltp = data?.ltp || 0;
  const atmReference = data?.atm_strike || ltp;

  // Calculate ATM (At The Money) strike
  const atmStrike = chains.reduce<ChainData | null>((closest, chain) => {
    if (!closest) return chain;
    const closestDiff = Math.abs(closest.strike_price - atmReference);
    const chainDiff = Math.abs(chain.strike_price - atmReference);
    return chainDiff < closestDiff ? chain : closest;
  }, null);

  const getMoneyness = (strike: number) => {
    if (strike === atmStrike?.strike_price) return "atm";
    return strike < (atmStrike?.strike_price || atmReference) ? "itm" : "otm";
  };

  const formatOptionPrice = (value: number | undefined) =>
    value != null && Number.isFinite(value) && value > 0 ? value.toFixed(2) : "—";

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900 p-4">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h3 className="text-sm uppercase tracking-wide text-slate-400">
            Option Chain - {displayName || symbol}
          </h3>
          <p className="text-xs text-slate-500 mt-1">LTP: ₹{ltp.toFixed(2)}</p>
        </div>
        <div className="relative flex items-center gap-3">
          <span className={`text-xs ${feedState === "live" ? "text-emerald-300" : "text-slate-500"}`}>
            {feedState === "live" ? "Live option prices" : feedState === "connected" ? "Feed connected · waiting for ticks" : feedState === "reconnecting" ? "Reconnecting live feed" : feedState === "connecting" ? "Connecting live feed" : "Live feed unavailable"}
          </span>
          <button
            onClick={() => setShowExpiries(!showExpiries)}
            className="flex items-center gap-2 rounded-md border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-700"
          >
            {expiry === "next_week" ? "Next Week" : expiry === "next_month" ? "Next Month" : "Current Expiry"}
            <ChevronDown className="h-3 w-3" />
          </button>
          {showExpiries && (
            <div className="absolute right-0 top-full mt-1 w-40 rounded-md border border-slate-700 bg-slate-800 p-1 z-50">
              <button
                onClick={() => { setExpiry(""); setShowExpiries(false); }}
                className="block w-full text-left px-2 py-1 text-xs hover:bg-slate-700 rounded"
              >
                Current Expiry
              </button>
              <button
                onClick={() => { setExpiry("next_week"); setShowExpiries(false); }}
                className="block w-full text-left px-2 py-1 text-xs hover:bg-slate-700 rounded"
              >
                Next Week
              </button>
              <button
                onClick={() => { setExpiry("next_month"); setShowExpiries(false); }}
                className="block w-full text-left px-2 py-1 text-xs hover:bg-slate-700 rounded"
              >
                Next Month
              </button>
            </div>
          )}
        </div>
      </div>

      {data?.message && chains.length > 0 && (
        <p className="mb-3 rounded border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
          {data.message}
        </p>
      )}

      {chains.length === 0 ? (
        <div className="text-center text-sm text-slate-500">{data?.message || "No option chain data available"}</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs border-collapse">
            <thead>
              <tr className="bg-slate-800/50 text-center">
                <th colSpan={5} className="px-2 py-2 font-semibold text-emerald-300">CALL / CE</th>
                <th rowSpan={2} className="bg-slate-800/70 px-3 py-2 font-bold text-slate-200">Strike</th>
                <th colSpan={5} className="px-2 py-2 font-semibold text-red-300">PUT / PE</th>
                <th rowSpan={2} className="px-2 py-2 text-slate-400 font-semibold">Trade</th>
              </tr>
              <tr className="border-b border-slate-700 bg-slate-800/50">
                <th className="px-2 py-2 text-left text-slate-400 font-semibold">OI</th>
                <th className="px-2 py-2 text-right text-slate-400 font-semibold">Vol</th>
                <th className="px-2 py-2 text-right text-slate-400 font-semibold">Bid</th>
                <th className="px-2 py-2 text-right text-slate-400 font-semibold">Ask</th>
                <th className="px-2 py-2 text-right text-slate-400 font-semibold">LTP</th>
                <th className="px-2 py-2 text-left text-slate-400 font-semibold">LTP</th>
                <th className="px-2 py-2 text-left text-slate-400 font-semibold">Bid</th>
                <th className="px-2 py-2 text-left text-slate-400 font-semibold">Ask</th>
                <th className="px-2 py-2 text-left text-slate-400 font-semibold">Vol</th>
                <th className="px-2 py-2 text-left text-slate-400 font-semibold">OI</th>
              </tr>
            </thead>
            <tbody>
              {chains.map((chain) => {
                const moneyness = getMoneyness(chain.strike_price);
                const isATM = chain.strike_price === atmStrike?.strike_price;
                const rowBg = isATM ? "bg-slate-800/40" : moneyness === "itm" ? "bg-slate-900/20" : "";
                const strikeColor = isATM ? "bg-blue-900/40 text-blue-200" : "text-slate-300";

                return (
                  <tr
                    key={chain.strike_price}
                    className={`border-b border-slate-800/50 hover:bg-slate-800/30 cursor-pointer ${rowBg}`}
                    onClick={() => setSelectedStrike(chain.strike_price)}
                  >
                    {/* Call Side - OI */}
                    <td className="px-2 py-2 text-slate-500">{(chain.call_oi || 0) / 1000}k</td>

                    {/* Call Volume */}
                    <td className="px-2 py-2 text-right text-slate-500">{(chain.call_volume || 0) / 100}</td>

                    {/* Call Bid */}
                    <td className="px-2 py-2 text-right font-semibold text-emerald-400">
                      {chain.call_bid?.toFixed(2) || "-"}
                    </td>

                    {/* Call Ask */}
                    <td className="px-2 py-2 text-right font-semibold text-emerald-400">
                      {chain.call_ask?.toFixed(2) || "-"}
                    </td>

                    {/* Call LTP */}
                    <td className="px-2 py-2 text-right font-semibold text-emerald-200">
                      {formatOptionPrice(chain.call_ltp)}
                    </td>

                    {/* Strike Price - Center */}
                    <td className={`px-3 py-2 text-center font-bold ${strikeColor} ${isATM ? "bg-slate-800/70" : ""}`}>
                      <div>{chain.strike_price}</div>
                      <div className="mt-0.5 text-[9px] font-medium text-slate-500">
                        {isATM ? "ATM" : `CE ${chain.strike_price < (atmStrike?.strike_price || 0) ? "ITM" : "OTM"} · PE ${chain.strike_price > (atmStrike?.strike_price || 0) ? "ITM" : "OTM"}`}
                      </div>
                    </td>

                    {/* Put LTP */}
                    <td className="px-2 py-2 text-left font-semibold text-red-200">
                      {formatOptionPrice(chain.put_ltp)}
                    </td>

                    {/* Put Bid */}
                    <td className="px-2 py-2 text-left font-semibold text-red-400">
                      {chain.put_bid?.toFixed(2) || "-"}
                    </td>

                    {/* Put Ask */}
                    <td className="px-2 py-2 text-left font-semibold text-red-400">
                      {chain.put_ask?.toFixed(2) || "-"}
                    </td>

                    {/* Put Volume */}
                    <td className="px-2 py-2 text-left text-slate-500">{(chain.put_volume || 0) / 100}</td>

                    {/* Put OI */}
                    <td className="px-2 py-2 text-left text-slate-500">{(chain.put_oi || 0) / 1000}k</td>

                    <td className="px-2 py-2">
                      <div className="flex gap-1">
                        <button
                          type="button"
                          title="Buy call"
                          onClick={(event) => { event.stopPropagation(); openOrderTicket("BUY", "CE", chain); }}
                          className="rounded bg-emerald-900/40 px-1.5 py-1 text-[10px] font-semibold text-emerald-300 hover:bg-emerald-900/70"
                        >
                          CE Buy
                        </button>
                        <button
                          type="button"
                          title="Sell call"
                          onClick={(event) => { event.stopPropagation(); openOrderTicket("SELL", "CE", chain); }}
                          className="rounded bg-red-900/40 px-1.5 py-1 text-[10px] font-semibold text-red-300 hover:bg-red-900/70"
                        >
                          CE Sell
                        </button>
                        <button
                          type="button"
                          title="Buy put"
                          onClick={(event) => { event.stopPropagation(); openOrderTicket("BUY", "PE", chain); }}
                          className="rounded bg-emerald-900/40 px-1.5 py-1 text-[10px] font-semibold text-emerald-300 hover:bg-emerald-900/70"
                        >
                          PE Buy
                        </button>
                        <button
                          type="button"
                          title="Sell put"
                          onClick={(event) => { event.stopPropagation(); openOrderTicket("SELL", "PE", chain); }}
                          className="rounded bg-red-900/40 px-1.5 py-1 text-[10px] font-semibold text-red-300 hover:bg-red-900/70"
                        >
                          PE Sell
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {selectedStrike && (
        <div className="mt-4 border-t border-slate-700 pt-4">
          <div className="mb-3 flex items-center justify-between">
            <p className="text-sm font-semibold text-slate-300">
              Strike {selectedStrike} Selected
            </p>
            <button
              onClick={() => setSelectedStrike(null)}
              className="text-xs text-slate-400 hover:text-slate-200"
            >
              Clear
            </button>
          </div>
          <div className="grid grid-cols-4 gap-2">
            <button onClick={() => { const chain = chains.find((item) => item.strike_price === selectedStrike); if (chain) openOrderTicket("BUY", "CE", chain); }} className="rounded-md bg-emerald-900/30 px-2 py-1.5 text-xs font-semibold text-emerald-300 hover:bg-emerald-900/50 flex items-center justify-center gap-1">
              <TrendingUp className="h-3 w-3" />
              Call Buy
            </button>
            <button onClick={() => { const chain = chains.find((item) => item.strike_price === selectedStrike); if (chain) openOrderTicket("SELL", "CE", chain); }} className="rounded-md bg-red-900/30 px-2 py-1.5 text-xs font-semibold text-red-300 hover:bg-red-900/50 flex items-center justify-center gap-1">
              <TrendingDown className="h-3 w-3" />
              Call Sell
            </button>
            <button onClick={() => { const chain = chains.find((item) => item.strike_price === selectedStrike); if (chain) openOrderTicket("BUY", "PE", chain); }} className="rounded-md bg-emerald-900/30 px-2 py-1.5 text-xs font-semibold text-emerald-300 hover:bg-emerald-900/50 flex items-center justify-center gap-1">
              <TrendingUp className="h-3 w-3" />
              Put Buy
            </button>
            <button onClick={() => { const chain = chains.find((item) => item.strike_price === selectedStrike); if (chain) openOrderTicket("SELL", "PE", chain); }} className="rounded-md bg-red-900/30 px-2 py-1.5 text-xs font-semibold text-red-300 hover:bg-red-900/50 flex items-center justify-center gap-1">
              <TrendingDown className="h-3 w-3" />
              Put Sell
            </button>
          </div>
        </div>
      )}

      {orderTicket && <OrderTicketModal key={`${orderTicket.tradingSymbol}-${orderTicket.side}`} ticket={orderTicket} onClose={() => setOrderTicket(null)} />}
    </div>
  );
}
