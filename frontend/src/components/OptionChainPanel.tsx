import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, TrendingUp, TrendingDown } from "lucide-react";
import { apiClient } from "../services/api";

interface OptionChainPanelProps {
  symbol: string;
}

interface ChainData {
  strike_price: number;
  call_symbol?: string;
  call_lot_size?: number;
  put_symbol?: string;
  put_lot_size?: number;
  call_bid?: number;
  call_ask?: number;
  call_volume?: number;
  call_oi?: number;
  put_bid?: number;
  put_ask?: number;
  put_volume?: number;
  put_oi?: number;
}

export function OptionChainPanel({ symbol }: OptionChainPanelProps) {
  const queryClient = useQueryClient();
  const [expiry, setExpiry] = useState<string>("");
  const [showExpiries, setShowExpiries] = useState(false);
  const [selectedStrike, setSelectedStrike] = useState<number | null>(null);
  const [orderSide, setOrderSide] = useState<"BUY" | "SELL" | null>(null);
  const [optionType, setOptionType] = useState<"CE" | "PE">("CE");
  const [tradingSymbol, setTradingSymbol] = useState("");
  const [quantity, setQuantity] = useState(1);
  const [orderType, setOrderType] = useState("MKT");
  const [price, setPrice] = useState("");
  const [orderMessage, setOrderMessage] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [showOrderConfirmation, setShowOrderConfirmation] = useState(false);

  const openOrderTicket = (side: "BUY" | "SELL", type: "CE" | "PE", chain: ChainData) => {
    setSelectedStrike(chain.strike_price);
    setOrderSide(side);
    setOptionType(type);
    setTradingSymbol(type === "CE" ? chain.call_symbol || "" : chain.put_symbol || "");
    const lotSize = Number(type === "CE" ? chain.call_lot_size : chain.put_lot_size) || 1;
    setQuantity(lotSize);
    setOrderMessage("");
    setShowOrderConfirmation(false);
  };

  const submitOrder = async () => {
    setIsSubmitting(true);
    setOrderMessage("");
    try {
      const result = await apiClient.placeOrder({
        exchange_segment: "nse_fo",
        product: "NRML",
        order_type: orderType,
        transaction_type: orderSide || "BUY",
        quantity,
        price: orderType === "L" ? Number(price) : undefined,
        trading_symbol: tradingSymbol.trim(),
        validity: "DAY",
        tag: `terminal-${optionType}-${selectedStrike}`,
      });
      await queryClient.invalidateQueries({ queryKey: ["orders"] });
      setShowOrderConfirmation(false);
      setOrderMessage(`Order request sent${result.order_id ? `: ${result.order_id}` : "."}`);
    } catch (err) {
      setOrderMessage(err instanceof Error ? err.message : "Order submission failed.");
    } finally {
      setIsSubmitting(false);
    }
  };

  const { data, isLoading, error } = useQuery({
    queryKey: ["option_chain", symbol, expiry],
    queryFn: async () => {
      if (!symbol) return { chains: [], ltp: 0, symbol: "", expiry: "" };
      try {
        const response = await apiClient.getOptionChain(symbol, expiry || undefined);
        return response;
      } catch (err) {
        console.error("Option chain error:", err);
        return { chains: [], ltp: 0, symbol: "", expiry: "" };
      }
    },
    enabled: !!symbol,
    staleTime: 0,
    refetchOnMount: "always",
    refetchInterval: false,
  });

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
        <h3 className="mb-3 text-sm uppercase tracking-wide text-slate-400">Option Chain - {symbol}</h3>
        <div className="text-center text-sm text-slate-500">Loading...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-xl border border-slate-800 bg-slate-900 p-4">
        <h3 className="mb-3 text-sm uppercase tracking-wide text-slate-400">Option Chain - {symbol}</h3>
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

  // Determine moneyness for coloring
  const getMoneyness = (strike: number) => {
    const diff = strike - atmReference;
    if (Math.abs(diff) < 100) return "atm";
    if (diff > 0) return "otm";
    return "itm";
  };

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900 p-4">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h3 className="text-sm uppercase tracking-wide text-slate-400">
            Option Chain - {symbol}
          </h3>
          <p className="text-xs text-slate-500 mt-1">LTP: ₹{ltp.toFixed(2)}</p>
        </div>
        <div className="relative">
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

      {chains.length === 0 ? (
        <div className="text-center text-sm text-slate-500">{data?.message || "No option chain data available"}</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs border-collapse">
            <thead>
              <tr className="bg-slate-800/50 border-b border-slate-700">
                <th className="px-2 py-2 text-left text-slate-400 font-semibold">Call OI</th>
                <th className="px-2 py-2 text-right text-slate-400 font-semibold">Vol</th>
                <th className="px-2 py-2 text-right text-slate-400 font-semibold">Bid</th>
                <th className="px-2 py-2 text-right text-slate-400 font-semibold">Ask</th>
                <th className="px-3 py-2 text-center text-slate-300 font-bold bg-slate-800/70">Strike</th>
                <th className="px-2 py-2 text-left text-slate-400 font-semibold">Bid</th>
                <th className="px-2 py-2 text-left text-slate-400 font-semibold">Ask</th>
                <th className="px-2 py-2 text-left text-slate-400 font-semibold">Vol</th>
                <th className="px-2 py-2 text-left text-slate-400 font-semibold">Put OI</th>
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

                    {/* Strike Price - Center */}
                    <td className={`px-3 py-2 text-center font-bold ${strikeColor} ${isATM ? "bg-slate-800/70" : ""}`}>
                      {chain.strike_price}
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

      {selectedStrike && orderSide && (
        <form
          className="mt-4 space-y-3 rounded-lg border border-slate-700 bg-slate-950/70 p-4"
          onSubmit={async (event) => {
            event.preventDefault();
            if (!tradingSymbol.trim()) {
              setOrderMessage("Enter the exact broker trading symbol before submitting.");
              return;
            }
            if (orderType === "L" && (!price || Number(price) <= 0)) {
              setOrderMessage("Enter a valid limit price.");
              return;
            }
            setShowOrderConfirmation(true);
          }}
        >
          <div className="flex items-center justify-between">
            <p className="text-sm font-semibold text-slate-200">{orderSide} {optionType} · {selectedStrike}</p>
            <button type="button" onClick={() => setOrderSide(null)} className="text-xs text-slate-400 hover:text-slate-200">Cancel</button>
          </div>
          <input value={tradingSymbol} onChange={(event) => setTradingSymbol(event.target.value)} placeholder="Exact broker trading symbol" className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500" />
          <div className="grid grid-cols-2 gap-2">
            <label className="text-xs text-slate-400">Quantity
              <input type="number" min="1" value={quantity} onChange={(event) => setQuantity(Math.max(1, Number(event.target.value)))} className="mt-1 w-full rounded border border-slate-700 bg-slate-900 px-2 py-2 text-slate-100" />
            </label>
            <label className="text-xs text-slate-400">Order type
              <select value={orderType} onChange={(event) => setOrderType(event.target.value)} className="mt-1 w-full rounded border border-slate-700 bg-slate-900 px-2 py-2 text-slate-100">
                <option value="MKT">Market</option>
                <option value="L">Limit</option>
              </select>
            </label>
          </div>
          {orderType === "L" && <input type="number" min="0" step="0.05" value={price} onChange={(event) => setPrice(event.target.value)} placeholder="Limit price" className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500" />}
          <button type="submit" disabled={isSubmitting} className="w-full rounded bg-blue-600 px-3 py-2 text-xs font-semibold text-white hover:bg-blue-500 disabled:cursor-wait disabled:opacity-60">Review order</button>
          {orderMessage && <p className="text-xs text-slate-300">{orderMessage}</p>}
        </form>
      )}

      {showOrderConfirmation && orderSide && selectedStrike && (
        <div className="mt-3 rounded-lg border border-amber-700/60 bg-amber-950/30 p-4">
          <p className="text-sm font-semibold text-amber-200">Confirm live order</p>
          <p className="mt-2 text-xs text-slate-300">
            {orderSide} {quantity} × {tradingSymbol} · {orderType === "L" ? `₹${price}` : "Market"}
          </p>
          <div className="mt-3 grid grid-cols-2 gap-2">
            <button type="button" onClick={() => setShowOrderConfirmation(false)} className="rounded border border-slate-700 px-3 py-2 text-xs text-slate-300 hover:bg-slate-800">Back</button>
            <button type="button" onClick={submitOrder} disabled={isSubmitting} className="rounded bg-amber-600 px-3 py-2 text-xs font-semibold text-white hover:bg-amber-500 disabled:opacity-60">{isSubmitting ? "Sending..." : "Confirm and send"}</button>
          </div>
        </div>
      )}
    </div>
  );
}
