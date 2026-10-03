import { useEffect, useState } from "react";
import { apiClient } from "../services/api";

export function PaperTradingPanel() {
  const [state, setState] = useState("Connecting live feed…");
  const [future, setFuture] = useState("");
  const [ltp, setLtp] = useState<number | null>(null);
  const [volume, setVolume] = useState<number | null>(null);

  useEffect(() => {
    const stream = apiClient.createPaperFutureStream();
    stream.onmessage = ({ data }) => {
      const event = JSON.parse(data);
      if (event.type === "status") {
        setState(event.state === "connected" ? "Live feed connected" : event.message || event.state);
        setFuture(event.symbol || "");
      }
      if (event.type === "quote") {
        setState("Live feed connected");
        setLtp(event.ltp);
        setVolume(event.volume);
      }
    };
    stream.onerror = () => setState("Live feed unavailable — log in and check Kotak connectivity.");
    return () => stream.close();
  }, []);

  return (
    <section className="rounded-lg border border-amber-700/50 bg-amber-950/20 p-4">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h2 className="font-semibold text-amber-200">BANKNIFTY paper agent</h2>
          <p className="mt-1 text-sm text-slate-300">Simulation only — broker orders are disabled.</p>
        </div>
        <span className="rounded-full bg-slate-700 px-2 py-1 text-xs text-slate-200">{state}</span>
      </div>
      <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-3">
        <div><dt className="text-slate-400">Signal</dt><dd>1m nearest future</dd></div>
        <div><dt className="text-slate-400">Position</dt><dd>3 lots, 2 ITM PE</dd></div>
        <div><dt className="text-slate-400">Daily limit</dt><dd>2 trades</dd></div>
        <div><dt className="text-slate-400">Loss limit</dt><dd>30 points</dd></div>
        <div><dt className="text-slate-400">Target</dt><dd>90 points</dd></div>
        <div><dt className="text-slate-400">Trailing</dt><dd>30→10, 60→30, 80→50</dd></div>
      </dl>
      {future && <p className="mt-3 text-sm text-slate-300">Feed: {future} · LTP: {ltp ?? "—"} · Volume: {volume ?? "—"}</p>}
      <p className="mt-4 text-xs text-amber-100/80">The live feed runner is being connected next; this panel will then expose Start, Stop, open virtual trade, and results.</p>
    </section>
  );
}
