import { useEffect, useState, useRef, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { apiClient } from "../services/api";
import { managedTrades } from "../services/managedTrades";
import { managedDefaults } from "../services/managedDefaults";
import { isResearchWorkspace } from "../services/workspace";

export interface OrderTicketRequest {
  symbol: string;
  tradingSymbol: string;
  exchangeSegment: string;
  instrumentToken?: string;
  side: "BUY" | "SELL";
  quantity?: number;
  lotSize?: number;
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
  const submitting = useRef(false);
  const [side, setSide] = useState<"BUY" | "SELL">(ticket.side);
  const [quantity, setQuantity] = useState(String(ticket.quantity || 1));
  const quantityStep = ticket.exchangeSegment.endsWith("_fo") && Number.isInteger(ticket.lotSize) && ticket.lotSize! > 0 ? ticket.lotSize! : 1;
  const [product, setProduct] = useState(ticket.product || (ticket.exchangeSegment.endsWith("_fo") ? "NRML" : "CNC"));
  const [orderType, setOrderType] = useState<"MKT" | "L" | "SL">(ticket.orderType || "MKT");
  const [triggerPrice, setTriggerPrice] = useState("");
  const [price, setPrice] = useState(ticket.limitPrice ? String(ticket.limitPrice) : "");
  const [message, setMessage] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [managed, setManaged] = useState(false);
  const [targetTrigger, setTargetTrigger] = useState("");
  const [targetLimit, setTargetLimit] = useState("");
  const [stopTrigger, setStopTrigger] = useState("");
  const [stopLimit, setStopLimit] = useState("");
  const manualPrices = useRef(new Set<string>());
  const managedRequest = useRef({ signature: "", id: "" });
  const canManage = !isResearchWorkspace && !ticket.isExit && !ticket.isHoldingSale && Boolean(ticket.instrumentToken)
    && ["nse_fo", "bse_fo"].includes(ticket.exchangeSegment);

  const { data: quoteData } = useQuery({
    queryKey: ["order-ticket-quote", ticket.exchangeSegment, ticket.instrumentToken],
    queryFn: () => apiClient.getQuotes([ticket.instrumentToken!], ticket.exchangeSegment),
    enabled: Boolean(ticket.instrumentToken) && !isSubmitting,
    refetchInterval: ticket.instrumentToken ? 5000 : false,
  });
  const quoteLtp = Number(quoteData?.quotes?.[0]?.ltp || 0);
  const ltp = quoteLtp > 0 ? quoteLtp : Number(ticket.ltp || 0);
  const defaultBase = orderType === "L" ? Number(price) : ltp;
  const defaults = managedDefaults(ticket.tradingSymbol || ticket.symbol, ticket.exchangeSegment, defaultBase);

  useEffect(() => {
    if (!managed || side !== "BUY" || isSubmitting || !defaults) return;
    if (!manualPrices.current.has("Profit trigger")) setTargetTrigger(defaults.targetPrice);
    if (!manualPrices.current.has("Profit limit")) setTargetLimit(defaults.targetPrice);
    if (!manualPrices.current.has("Stop trigger")) setStopTrigger(defaults.stopPrice);
    if (!manualPrices.current.has("Stop limit")) setStopLimit(defaults.stopPrice);
  }, [managed, side, isSubmitting, defaults?.targetPrice, defaults?.stopPrice]);

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
    if (submitting.current) return;
    const quantityValue = Number(quantity);
    const priceValue = Number(price);
    if (!Number.isInteger(quantityValue) || quantityValue < 1) {
      setMessage("Quantity must be a whole number greater than zero.");
      return;
    }
    if (quantityValue % quantityStep !== 0) {
      setMessage(`Quantity must be a multiple of the contract lot size (${quantityStep} units).`);
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
    if (orderType !== "MKT" && (!Number.isFinite(priceValue) || priceValue <= 0)) {
      setMessage("Enter a valid limit price.");
      return;
    }
    const triggerValue = Number(triggerPrice);
    if (orderType === "SL") {
      if (!Number.isFinite(triggerValue) || triggerValue <= 0) {
        setMessage("Enter a valid stop trigger price.");
        return;
      }
      if ((side === "SELL" && priceValue > triggerValue) || (side === "BUY" && priceValue < triggerValue)) {
        setMessage(side === "SELL" ? "Sell SL limit must be at or below the trigger." : "Buy SL limit must be at or above the trigger.");
        return;
      }
      if (ltp > 0 && ((side === "SELL" && triggerValue >= ltp) || (side === "BUY" && triggerValue <= ltp))) {
        setMessage(side === "SELL" ? "Sell stop trigger must be below the displayed LTP." : "Buy stop trigger must be above the displayed LTP.");
        return;
      }
    }

    submitting.current = true;
    setIsSubmitting(true);
    setMessage("");
    const submittedAt = performance.now();
    try {
      if (managed) {
        if (orderType === "SL") throw new Error("Managed entries require Market or Limit.");
        if (!canManage || side !== "BUY" || !["MIS", "NRML"].includes(product)) throw new Error("Managed entries require a BUY option and MIS or NRML product.");
        const payload = {
          symbol: ticket.tradingSymbol, instrument_token: ticket.instrumentToken!,
          exchange_segment: ticket.exchangeSegment as "nse_fo" | "bse_fo", product: product as "MIS" | "NRML",
          side, quantity: quantityValue, entry_type: orderType, entry_limit: orderType === "L" ? priceValue : undefined,
          target_trigger: Number(targetTrigger), target_limit: Number(targetLimit),
          stop_trigger: Number(stopTrigger), stop_limit: Number(stopLimit),
        };
        if (![payload.target_trigger, payload.target_limit, payload.stop_trigger, payload.stop_limit].every(value => Number.isFinite(value) && value > 0)) throw new Error("Enter all four trigger and limit prices.");
        if (payload.target_limit > payload.target_trigger) throw new Error(`Profit limit (${payload.target_limit.toFixed(2)}) must be at or below Profit trigger (${payload.target_trigger.toFixed(2)}) for a BUY trade. Lower Profit limit or raise Profit trigger.`);
        if (payload.stop_limit > payload.stop_trigger) throw new Error(`Stop limit (${payload.stop_limit.toFixed(2)}) must be at or below Stop trigger (${payload.stop_trigger.toFixed(2)}) for a BUY trade. Lower Stop limit or raise Stop trigger.`);
        if (payload.stop_trigger >= payload.target_trigger) throw new Error("Stop trigger must be below Profit trigger for a BUY trade.");
        if (orderType === "L" && !(payload.stop_trigger < priceValue && priceValue < payload.target_trigger)) throw new Error(`Entry limit (${priceValue.toFixed(2)}) must be above Stop trigger (${payload.stop_trigger.toFixed(2)}) and below Profit trigger (${payload.target_trigger.toFixed(2)}) for a BUY trade.`);
        const signature = JSON.stringify(payload);
        if (managedRequest.current.signature !== signature) managedRequest.current = { signature, id: crypto.randomUUID() };
        const result = await managedTrades.create({ ...payload, request_id: managedRequest.current.id });
        void queryClient.invalidateQueries({ queryKey: ["managed-trades"] });
        void queryClient.invalidateQueries({ queryKey: ["orders"] });
        void queryClient.invalidateQueries({ queryKey: ["positions"] });
        if (result.state === "ENTRY_UNKNOWN") { setMessage(result.note); return; }
        onClose();
        return;
      }
      const acknowledgement = await apiClient.placeOrder({
        exchange_segment: ticket.exchangeSegment,
        product,
        order_type: orderType,
        transaction_type: side,
        quantity: quantityValue,
        price: orderType !== "MKT" ? priceValue : undefined,
        trigger_price: orderType === "SL" ? triggerValue : undefined,
        amo: "NO",
        trading_symbol: ticket.tradingSymbol,
        scrip_token: ticket.instrumentToken || undefined,
        validity: "DAY",
        tag: ticket.isHoldingSale ? "holding-sell" : ticket.isExit ? `exit-${side.toLowerCase()}` : `terminal-${side.toLowerCase()}`,
      });
      queryClient.setQueryData(["last-order-ack"], {
        ...acknowledgement,
        total_ms: Math.round(performance.now() - submittedAt),
      });
      void Promise.all([
        queryClient.invalidateQueries({ queryKey: ["orders"] }),
        queryClient.invalidateQueries({ queryKey: ["positions"] }),
        queryClient.invalidateQueries({ queryKey: ["holdings"] }),
        queryClient.invalidateQueries({ queryKey: ["trade-book"] }),
      ]).catch(() => {});
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
      submitting.current = false;
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
        className="max-h-[90vh] w-full max-w-md overflow-y-auto rounded-xl border border-slate-700 bg-slate-900 shadow-2xl"
      >
        <div className="flex items-start justify-between border-b border-slate-800 px-5 py-4">
          <div>
            <h2 id="order-ticket-title" className="text-base font-semibold text-slate-100">
              {managed ? "Live managed trade - real orders" : ticket.isHoldingSale ? "Sell holding" : ticket.isExit ? "Exit position" : "Place order"}
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
              <input type="number" min={quantityStep} max={ticket.maxQuantity} step={quantityStep} value={quantity} onChange={(event) => setQuantity(event.target.value)} className="mt-1 w-full rounded border border-slate-700 bg-slate-800 px-3 py-2.5 text-sm text-slate-100" />
              {quantityStep > 1 && <span className="mt-1 block text-xs text-slate-500">1 lot = {quantityStep} units</span>}
            </label>
            <label className="text-xs text-slate-400">
              Product
              <select value={product} onChange={(event) => setProduct(event.target.value)} disabled={ticket.isHoldingSale || ticket.isExit} className="mt-1 w-full rounded border border-slate-700 bg-slate-800 px-3 py-2.5 text-sm text-slate-100 disabled:cursor-not-allowed disabled:opacity-60">
                <option value="CNC">CNC</option>
                <option value="MIS">MIS</option>
                <option value="NRML">NRML</option>
              </select>
            </label>
            <label className="text-xs text-slate-400">
              Order type
              <select value={orderType} onChange={(event) => setOrderType(event.target.value as "MKT" | "L" | "SL")} className="mt-1 w-full rounded border border-slate-700 bg-slate-800 px-3 py-2.5 text-sm text-slate-100">
                <option value="MKT">Market</option>
                <option value="L">Limit</option>
                {ticket.isExit && <option value="SL">SL Limit</option>}
              </select>
            </label>
            {orderType !== "MKT" && (
              <label className="text-xs text-slate-400">
                Limit price
                <input type="number" min="0.01" step="0.01" value={price} onChange={(event) => setPrice(event.target.value)} className="mt-1 w-full rounded border border-slate-700 bg-slate-800 px-3 py-2.5 text-sm text-slate-100" />
              </label>
            )}
            {orderType === "SL" && <label className="text-xs text-slate-400">
              Stop trigger price
              <input required type="number" min="0.01" step="0.01" value={triggerPrice} onChange={event => setTriggerPrice(event.target.value)} className="mt-1 w-full rounded border border-slate-700 bg-slate-800 px-3 py-2.5 text-sm text-slate-100" />
            </label>}
          </div>
          {orderType === "SL" && <p className="text-xs text-amber-200">Places a broker SL-limit order; the position stays open until it fills. {side === "SELL" ? "Set trigger below LTP and limit at or below trigger." : "Set trigger above LTP and limit at or above trigger."} A price gap can leave the order unfilled. Cancel any other exit for this quantity before placing another.</p>}

          {canManage && <fieldset disabled={isSubmitting} className="space-y-3 rounded-lg border border-amber-500/30 p-3">
            <label className="flex items-center gap-2 text-sm text-amber-200"><input type="checkbox" checked={managed} onChange={event => setManaged(event.target.checked)} />Live target & stop - real orders</label>
            {managed && <>
              {defaults && <div className="space-y-1 text-xs text-slate-400">
                <p>Auto-fill: SL {defaults.stop} points / target {defaults.target} points from {orderType === "L" ? "entry limit" : "LTP"}{defaultBase > 0 ? ` (${defaultBase.toFixed(2)})` : ""}. Trigger and limit start equal. Manually edited fields stay fixed.</p>
                {!defaults.stopPrice && defaultBase > 0 && <p className="text-amber-200">Premium is too low for this SL distance. Enter positive stop prices manually.</p>}
                <button type="button" className="text-sky-300" onClick={() => {
                  manualPrices.current.clear();
                  setTargetTrigger(defaults.targetPrice); setTargetLimit(defaults.targetPrice);
                  setStopTrigger(defaults.stopPrice); setStopLimit(defaults.stopPrice);
                }}>Reset all to defaults</button>
              </div>}
              <p className="text-xs text-slate-400">Buy one or more whole lots of a Nifty/Bank Nifty/Sensex option, subject to broker order limits. Entry goes to Kotak; local premium triggers submit one real limit exit for the actual filled quantity. Quantity is in contract units. Keep the backend and connection running.</p>
              <div className="grid grid-cols-2 gap-2">
                {([
                  ["Profit trigger", targetTrigger, setTargetTrigger], ["Profit limit", targetLimit, setTargetLimit],
                  ["Stop trigger", stopTrigger, setStopTrigger], ["Stop limit", stopLimit, setStopLimit],
                ] as const).map(([label, value, setter]) => <label key={label} className="text-xs text-slate-400">{label}<input required type="number" min="0.05" step="0.05" value={value} onChange={event => { manualPrices.current.add(label); setter(event.target.value); }} className="mt-1 w-full rounded border border-slate-700 bg-slate-800 p-2 text-slate-100" /></label>)}
              </div>
              <p className="text-xs text-slate-400">For a BUY trade: Profit limit must be at or below Profit trigger; Stop limit must be at or below Stop trigger. For a limit entry, set Stop trigger below Entry limit and Profit trigger above it.</p>
              <p className="text-xs text-amber-200">An unfilled exit keeps your limit; the other trigger stays disabled. Backend or feed outages stop local trigger detection.</p>
            </>}
          </fieldset>}
          {(ticket.isExit || ticket.isHoldingSale) && ticket.maxQuantity && quantity && Number(quantity) < ticket.maxQuantity && (
            <p className="text-xs text-slate-500">Partial sale - {ticket.maxQuantity - Number(quantity)} units will remain.</p>
          )}
          {orderType === "MKT" && ltp > 0 && <p className="text-xs text-slate-500">Estimated value: &#8377;{(ltp * Number(quantity || 0)).toFixed(2)}</p>}
          <p className="text-xs text-slate-500">After-market orders are currently unavailable through this terminal.</p>
          {message && <p role="alert" className="rounded bg-red-500/10 px-3 py-2 text-xs text-red-300">{message}</p>}
        </div>

        <div className="flex gap-2 border-t border-slate-800 px-5 py-4">
          <button type="button" onClick={onClose} disabled={isSubmitting} className="flex-1 rounded-lg border border-slate-700 px-4 py-2.5 text-sm text-slate-300 hover:bg-slate-800 disabled:opacity-50">Cancel</button>
          <button type="submit" disabled={isSubmitting} className={`flex-1 rounded-lg px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50 ${side === "BUY" ? "bg-emerald-600 hover:bg-emerald-500" : "bg-red-600 hover:bg-red-500"}`}>
            {managed ? (isSubmitting ? "Submitting managed entry..." : "Buy with live target & stop") : <>
            {isSubmitting ? "Sending..." : orderType === "SL" ? "Place SL Limit" : `${ticket.isHoldingSale ? "Sell" : ticket.isExit ? "Exit" : side} ${ticket.symbol}`}
            </>}
          </button>
        </div>
      </form>
    </div>
  );
}
