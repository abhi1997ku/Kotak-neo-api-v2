import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../services/api";

interface EquityOrderPanelProps {
  symbol: string;
  tradingSymbol?: string;
  name?: string;
  instrumentToken?: string;
}

export function EquityOrderPanel({ symbol, tradingSymbol, name, instrumentToken }: EquityOrderPanelProps) {
  const queryClient = useQueryClient();
  const [side, setSide] = useState<"BUY" | "SELL">("BUY");
  const [quantity, setQuantity] = useState(1);
  const [orderType, setOrderType] = useState<"MKT" | "L">("MKT");
  const [afterMarket, setAfterMarket] = useState(false);
  const [price, setPrice] = useState("");
  const [message, setMessage] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const { data: quoteData, error: quoteError } = useQuery({
    queryKey: ["equity_quote", instrumentToken],
    queryFn: async () => {
      let token = instrumentToken;
      if (!token) {
        const search = await apiClient.searchSymbol(symbol);
        token = search.results.find((item: { symbol?: string; instrument_token?: string }) =>
          item.symbol === symbol || item.symbol?.startsWith(`${symbol}-`)
        )?.instrument_token;
      }
      if (!token) {
        throw new Error(`Instrument token unavailable for ${symbol}. Search the symbol again.`);
      }
      return apiClient.getQuotes([token]);
    },
    enabled: Boolean(symbol),
    refetchInterval: 5000,
  });
  const quote = quoteData?.quotes?.[0];

  const submitOrder = async () => {
    if (quantity < 1) {
      setMessage("Quantity must be at least 1.");
      return;
    }
    if (orderType === "L" && (!price || Number(price) <= 0)) {
      setMessage("Enter a valid limit price.");
      return;
    }

    setIsSubmitting(true);
    setMessage("");
    try {
      const result = await apiClient.placeOrder({
        exchange_segment: "nse_cm",
        product: "CNC",
        order_type: orderType,
        transaction_type: side,
        quantity,
        price: orderType === "L" ? Number(price) : undefined,
        amo: afterMarket ? "YES" : "NO",
        trading_symbol: tradingSymbol || symbol,
        validity: "DAY",
        tag: `equity-${side.toLowerCase()}`,
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["orders"] }),
        queryClient.invalidateQueries({ queryKey: ["positions"] }),
        queryClient.invalidateQueries({ queryKey: ["holdings"] }),
        queryClient.invalidateQueries({ queryKey: ["trade-book"] }),
      ]);
      setMessage(`Order ${result.status || "submitted"}${result.order_id ? `: ${result.order_id}` : ". Check Order Book."}`);
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : "Order submission failed.";
      if (orderType === "MKT" && /LTP\)?\s+not available|Last Traded Price.*not available/i.test(errorMessage)) {
        const latestPrice = Number(quote?.ltp);
        setOrderType("L");
        if (!price && Number.isFinite(latestPrice) && latestPrice > 0) {
          setPrice(String(latestPrice));
        }
        setMessage(
          "Kotak rejected the market order because no LTP is available. No order was placed. Review the limit price and submit during market hours, or place an AMO through the Kotak Neo app/web."
        );
      } else {
        setMessage(errorMessage);
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900 p-4">
      <div className="mb-3">
        <h3 className="text-sm uppercase tracking-wide text-slate-400">Equity Order</h3>
        <p className="mt-1 text-sm font-semibold text-slate-200">{symbol}</p>
        {name && <p className="text-xs text-slate-500">{name}</p>}
        <p className="mt-2 text-lg font-semibold text-emerald-300">
          LTP: {quote ? `₹${Number(quote.ltp).toFixed(2)}` : "Unavailable"}
        </p>
        {quoteError && <p className="mt-1 text-xs text-red-300">{quoteError instanceof Error ? quoteError.message : "Quote request failed."}</p>}
      </div>

      <div className="grid grid-cols-2 gap-2">
        <button
          type="button"
          onClick={() => setSide("BUY")}
          className={`rounded-md px-3 py-2 text-sm font-semibold ${side === "BUY" ? "bg-emerald-600 text-white" : "bg-slate-800 text-slate-400"}`}
        >
          Buy
        </button>
        <button
          type="button"
          onClick={() => setSide("SELL")}
          className={`rounded-md px-3 py-2 text-sm font-semibold ${side === "SELL" ? "bg-red-600 text-white" : "bg-slate-800 text-slate-400"}`}
        >
          Sell
        </button>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2">
        <label className="text-xs text-slate-400">
          Quantity
          <input
            type="number"
            min="1"
            value={quantity}
            onChange={(event) => setQuantity(Math.max(1, Number(event.target.value)))}
            className="mt-1 w-full rounded border border-slate-700 bg-slate-800 px-2 py-2 text-slate-100"
          />
        </label>
        <label className="text-xs text-slate-400">
          Order type
          <select
            value={orderType}
            onChange={(event) => {
              const nextType = event.target.value as "MKT" | "L";
              setOrderType(nextType);
              const latestPrice = Number(quote?.ltp);
              if (nextType === "L" && !price && Number.isFinite(latestPrice) && latestPrice > 0) {
                setPrice(String(latestPrice));
              }
            }}
            className="mt-1 w-full rounded border border-slate-700 bg-slate-800 px-2 py-2 text-slate-100"
          >
            <option value="MKT" disabled={afterMarket}>Market</option>
            <option value="L">Limit</option>
          </select>
        </label>
      </div>

      <label className="mt-3 flex items-center gap-2 text-xs text-slate-300">
        <input
          type="checkbox"
          checked={afterMarket}
          disabled
          onChange={(event) => {
            const enabled = event.target.checked;
            setAfterMarket(enabled);
            if (enabled) {
              setOrderType("L");
              const latestPrice = Number(quote?.ltp);
              if (!price && Number.isFinite(latestPrice) && latestPrice > 0) {
                setPrice(String(latestPrice));
              }
            }
          }}
          className="h-4 w-4 accent-sky-500"
        />
        After-market order (temporarily unavailable via Trade API)
      </label>
      <p className="mt-1 text-xs text-amber-300">
        Kotak has temporarily paused AMO placement through Trade APIs. You can place an AMO in the{" "}
        <a
          href="https://www.kotakneo.com/bulletins/amo-trade-api-temporarily-disabled/"
          target="_blank"
          rel="noreferrer"
          className="underline"
        >
          Kotak Neo app or web platform
        </a>.
      </p>

      {orderType === "L" && (
        <input
          type="number"
          min="0.01"
          step="0.01"
          value={price}
          onChange={(event) => setPrice(event.target.value)}
          placeholder="Limit price"
          className="mt-3 w-full rounded border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
        />
      )}

      <button
        type="button"
        onClick={submitOrder}
        disabled={isSubmitting}
        className={`mt-3 w-full rounded-md px-3 py-2 text-sm font-semibold text-white disabled:opacity-50 ${side === "BUY" ? "bg-emerald-600 hover:bg-emerald-500" : "bg-red-600 hover:bg-red-500"}`}
      >
        {isSubmitting ? "Sending..." : `${side} ${symbol}`}
      </button>

      {message && <p className="mt-3 text-xs text-slate-300">{message}</p>}
    </div>
  );
}
