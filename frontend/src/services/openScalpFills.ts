import type { Position, Trade } from "./api";

// FIFO matches exits to earlier entries. Never substitute the day's broker average.
export function openScalpFills(position: Position, trades: Trade[]) {
  const relevant = trades.filter(t => t.symbol === position.symbol);
  if (relevant.some(t => !t.product || !t.exchange_segment)) return null;
  const matching = relevant.filter(t => t.product === position.product && t.exchange_segment === position.exchange_segment);
  const timed = matching.map(t => ({ ...t, time: Date.parse(t.timestamp.replace(/^(\d{2})-([A-Za-z]{3})-(\d{4})/, "$2 $1, $3")) }));
  if (timed.some(t => !Number.isFinite(t.time) || !Number.isInteger(t.qty) || t.qty <= 0 || !Number.isFinite(t.price) || t.price <= 0 || !["BUY", "SELL"].includes(t.side))) return null;
  timed.sort((a, b) => a.time - b.time);
  // Opposite-side executions at identical timestamps cannot be reliably ordered.
  if (timed.some((t, i) => i > 0 && t.time === timed[i - 1].time && t.side !== timed[i - 1].side)) return null;
  const lots: { orderId: string; side: string; quantity: number; price: number }[] = [];
  for (const trade of timed) {
    let remaining = trade.qty;
    while (remaining > 0 && lots.length && lots[0].side !== trade.side) {
      const matched = Math.min(remaining, lots[0].quantity);
      remaining -= matched; lots[0].quantity -= matched;
      if (!lots[0].quantity) lots.shift();
    }
    if (remaining) lots.push({ orderId: trade.order_id || trade.trade_id, side: trade.side, quantity: remaining, price: trade.price });
  }
  const net = lots.reduce((sum, lot) => sum + (lot.side === "BUY" ? lot.quantity : -lot.quantity), 0);
  // Missing fills, overnight positions, and report lag must not show a guessed price.
  if (position.order_quantity == null || net !== position.order_quantity) return null;
  return lots;
}
