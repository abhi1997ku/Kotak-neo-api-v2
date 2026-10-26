export function managedDefaults(symbol: string, segment: string, base: number) {
  const name = symbol.toUpperCase().replace(/[^A-Z0-9]/g, "");
  const offsets = segment === "bse_fo" && name.startsWith("SENSEX") ? { stop: 20, target: 60 }
    : segment === "nse_fo" && (name.startsWith("BANKNIFTY") || name.startsWith("NIFTYBANK")) ? { stop: 30, target: 50 }
    : segment === "nse_fo" && name.startsWith("NIFTY") ? { stop: 10, target: 20 } : null;
  if (!offsets) return null;
  const format = (value: number) => Number.isFinite(value) && value > 0 ? (Math.round(value * 20) / 20).toFixed(2) : "";
  const validBase = Number.isFinite(base) && base > 0;
  return { ...offsets, targetPrice: validBase ? format(base + offsets.target) : "",
    stopPrice: validBase ? format(base - offsets.stop) : "" };
}
