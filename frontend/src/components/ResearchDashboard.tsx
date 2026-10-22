import { PaperTradingPanel } from "./PaperTradingPanel";
import { IndexSwingPanel } from "./IndexSwingPanel";
import { ScreenerPanel } from "./ScreenerPanel";
import { WatchlistPanel } from "./WatchlistPanel";
import { useAuth } from "../contexts/AuthContext";

export default function ResearchDashboard() {
  const { logout } = useAuth();
  return <main className="min-h-screen space-y-4 bg-slate-950 p-4 text-slate-100">
    <header className="flex items-center justify-between">
      <h1 className="text-xl font-bold">Paper trading &amp; research</h1>
      <div className="flex gap-4 text-sm"><a href="http://localhost:5173/">Live trading</a><button onClick={logout}>Logout</button></div>
    </header>
    <a className="inline-block text-cyan-300 underline" href="#ema-paper">Open EMA9 + VWAP monitor</a>
    <PaperTradingPanel />
    <IndexSwingPanel />
    <WatchlistPanel view="stocks" readOnly snapshotOnly onSelectStock={() => {}} onSelectIndex={() => {}} onOrderRequest={() => {}} />
    <ScreenerPanel />
  </main>;
}
