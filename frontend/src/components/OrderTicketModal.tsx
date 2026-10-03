import { useEffect, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { apiClient } from "../services/api";

export interface OrderTicketRequest {
  symbol: string;
  tradingSymbol: string;
  exchangeSegment: string;
  instrumentToken?: string;
  side: "BUY" | "SELL";
  quantity?: number;
  maxQuantity?: number;
  product?: string;
  ltp?: number;
  orderType?: "MKT" | "L";
  limitPrice?: number;
  isExit?: boolean;
  isHoldingSale?: boolean;
}

interface OrderTicketModalProps {
  ticket: OrderTicketRequest;
  onClose: () => void;
}

export function OrderTicketModal({ ticket, onClose }: OrderTicketModalProps) {
  const queryClient = useQueryClient();
  const [side, setSide] = useState<"BUY" | "SELL">(ticket.side);
  const [quantity, setQuantity] = useState(String(ticket.quantity || 1));
  const [product, setProduct] = useState(ticket.product || (ticket.exchangeSegment.endsWith("_fo") ? "NRML" : "CNC"));
  const [orderType, setOrderType] = useState<"MKT" | "L">(ticket.orderType || "MKT");
  const [price, setPrice] = useState(ticket.limitPrice ? String(ticket.limitPrice) : "");
  const [message, setMessage] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const { data: quoteData } = useQuery({
    queryKey: ["order-ticket-quote", ticket.exchangeSegment, ticket.instrumentToken],
    queryFn: () => apiClient.getQuotes([ticket.instrumentToken!], ticket.exchangeSegment),
    enabled: Boolean(ticket.instrumentToken),
    refetchInterval: ticket.instrumentToken ? 5000 : false,
  });
  const quoteLtp = Number(quoteData?.quotes?.[0]?.ltp || 0);
  const ltp = quoteLtp > 0 ? quoteLtp : Number(ticket.ltp || 0);

  useEffect(() => {
    if (orderType === "L" && !price && ltp > 0) setPrice(ltp.toFixed(2));
  }, [ltp, orderType, price]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !isSubmitting) onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isSubmitting, onClose]);

  const submitOrder = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const quantityValue = Number(quantity);
    const priceValue = Number(price);
    if (!Number.isInteger(quantityValue) || quantityValue < 1) {
      setMessage("Quantity must be a whole number greater than zero.");
      return;
    }
    if (!ticket.tradingSymbol.trim()) {
      setMessage("Kotak did not provide a trading symbol for this instrument, so the order cannot be submitted.");
      return;
    }
    if (ticket.maxQuantity && quantityValue > ticket.maxQuantity) {
      setMessage(`Quantity cannot exceed the available ${ticket.isHoldingSale ? "holding" : "position"} quantity of ${ticket.maxQuantity}.`);
      return;
    }
    if (orderType === "L" && (!Number.isFinite(priceValue) || priceValue <= 0)) {
      setMessage("Enter a valid limit price.");
      return;
    }

    setIsSubmitting(true);
    setMessage("");
    try {
      await apiClient.placeOrder({
        exchange_segment: ticket.exchangeSegment,
        product,
        order_type: orderType,
        transaction_type: side,
        quantity: quantityValue,
        price: orderType === "L" ? priceValue : undefined,
        amo: "NO",
        trading_symbol: ticket.tradingSymbol,
        scrip_token: ticket.instrumentToken || undefined,
        validity: "DAY",
        tag: ticket.isHoldingSale ? "holding-sell" : ticket.isExit ? `exit-${side.toLowerCase()}` : `terminal-${side.toLowerCase()}`,
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["orders"] }),
        queryClient.invalidateQueries({ queryKey: ["positions"] }),
        queryClient.invalidateQueries({ queryKey: ["holdings"] }),
        queryClient.invalidateQueries({ queryKey: ["trade-book"] }),
      ]);
      onClose();
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : "Order submission failed.";
      if (orderType === "MKT" && /LTP\)?\s+not available|Last Traded Price.*not available/i.test(errorMessage)) {
        setOrderType("L");
        if (!price && ltp > 0) setPrice(ltp.toFixed(2));
        setMessage("Kotak has no LTP for this instrument. No order was placed. Review the limit price and submit again.");
      } else {
        setMessage(errorMessage);
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-950/80 p-4 backdrop-blur-sm"
      onMouseDown={(event) => { if (event.target === event.currentTarget && !isSubmitting) onClose(); }}
      role="presentation"
    >
      <form
        role="dialog"
        aria-modal="true"
        aria-labelledby="order-ticket-title"
        onSubmit={submitOrder}
        className="w-full max-w-md overflow-hidden rounded-xl border border-slate-700 bg-slate-900 shadow-2xl"
      >
        <div className="flex items-start justify-between border-b border-slate-800 px-5 py-4">
          <div>
            <h2 id="order-ticket-title" className="text-base font-semibold text-slate-100">
              {ticket.isHoldingSale ? "Sell holding" : ticket.isExit ? "Exit position" : "Place order"}
            </h2>
            <p className="mt-1 text-sm text-slate-400">{ticket.symbol}</p>
          </div>
          <button type="button" onClick={onClose} disabled={isSubmitting} aria-label="Close order ticket" className="rounded p-1 text-slate-400 hover:bg-slate-800 hover:text-white disabled:opacity-50">
            <X size={18} />
          </button>
        </div>

        <div className="space-y-4 px-5 py-4">
          <div className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-950 px-3 py-2">
            <span className="text-xs uppercase tracking-wide text-slate-500">LTP</span>
            <span className="font-semibold tabular-nums text-slate-100">{ltp > 0 ? <> &#8377;{ltp.toFixed(2)}</> : "Unavailable"}</span>
          </div>

          {!ticket.isExit && !ticket.isHoldingSale && (
            <div className="grid grid-cols-2 gap-2">
              <button type="button" onClick={() => setSide("BUY")} className={`rounded-lg py-2.5 text-sm font-semibold ${side === "BUY" ? "bg-emerald-600 text-white" : "bg-slate-800 text-slate-400"}`}>Buy</button>
              <button type="button" onClick={() => setSide("SELL")} className={`rounded-lg py-2.5 text-sm font-semibold ${side === "SELL" ? "bg-red-600 text-white" : "bg-slate-800 text-slate-400"}`}>Sell</button>
            </div>
          )}
          {(ticket.isExit || ticket.isHoldingSale) && <p className="rounded-lg bg-amber-500/10 px-3 py-2 text-xs text-amber-200">{side} up to {ticket.maxQuantity} {ticket.isHoldingSale ? "held" : "open position"} units.</p>}

          <div className="grid grid-cols-2 gap-3">
            <label className="text-xs text-slate-400">
              Quantity
              <input type="number" min="1" max={ticket.maxQuantity} step="1" value={quantity} onChange={(event) => setQuantity(event.target.value)} className="mt-1 w-full rounded border border-slate-700 bg-slate-800 px-3 py-2.5 text-sm text-slate-100" />
            </label>
            <label className="text-xs text-slate-400">
              Product
              <select value={product} onChange={(event) => setProduct(event.target.value)} disabled={ticket.isHoldingSale} className="mt-1 w-full rounded border border-slate-700 bg-slate-800 px-3 py-2.5 text-sm text-slate-100 disabled:cursor-not-allowed disabled:opacity-60">
                <option value="CNC">CNC</option>
                <option value="MIS">MIS</option>
                <option value="NRML">NRML</option>
              </select>
            </label>
            <label className="text-xs text-slate-400">
              Order type
              <select value={orderType} onChange={(event) => setOrderType(event.target.value as "MKT" | "L")} className="mt-1 w-full rounded border border-slate-700 bg-slate-800 px-3 py-2.5 text-sm text-slate-100">
                <option value="MKT">Market</option>
                <option value="L">Limit</option>
              </select>
            </label>
            {orderType === "L" && (
              <label className="text-xs text-slate-400">
                Limit price
                <input type="number" min="0.01" step="0.01" value={price} onChange={(event) => setPrice(event.target.value)} className="mt-1 w-full rounded border border-slate-700 bg-slate-800 px-3 py-2.5 text-sm text-slate-100" />
              </label>
            )}
          </div>

          {(ticket.isExit || ticket.isHoldingSale) && ticket.maxQuantity && quantity && Number(quantity) < ticket.maxQuantity && (
            <p className="text-xs text-slate-500">Partial sale · {ticket.maxQuantity - Number(quantity)} units will remain.</p>
          )}
          {orderType === "MKT" && ltp > 0 && <p className="text-xs text-slate-500">Estimated value: &#8377;{(ltp * Number(quantity || 0)).toFixed(2)}</p>}
          <p className="text-xs text-slate-500">After-market orders are currently unavailable through this terminal.</p>
          {message && <p role="alert" className="rounded bg-red-500/10 px-3 py-2 text-xs text-red-300">{message}</p>}
        </div>

        <div className="flex gap-2 border-t border-slate-800 px-5 py-4">
          <button type="button" onClick={onClose} disabled={isSubmitting} className="flex-1 rounded-lg border border-slate-700 px-4 py-2.5 text-sm text-slate-300 hover:bg-slate-800 disabled:opacity-50">Cancel</button>
          <button type="submit" disabled={isSubmitting} className={`flex-1 rounded-lg px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50 ${side === "BUY" ? "bg-emerald-600 hover:bg-emerald-500" : "bg-red-600 hover:bg-red-500"}`}>
            {isSubmitting ? "Sending…" : `${ticket.isHoldingSale ? "Sell" : ticket.isExit ? "Exit" : side} ${ticket.symbol}`}
          </button>
        </div>
      </form>
    </div>
  );
}
