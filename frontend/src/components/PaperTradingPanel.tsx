import { useEffect, useState } from "react";
import { apiClient } from "../services/api";

type AgentState = { running: boolean; message: string; trades_today: number; losses_today?: number; max_trades: number; open_trade?: any; history?: any[]; future?: string; pattern_data?: { samples: number; labeled_samples: number; trading_days: number }; signal_diagnostics?: { completed_candles: number; required_candles: number; phase: string; market_regime?: string; latest_patterns?: string[]; readiness_mode?: string; analysis_candles?: number } };

export function PaperTradingPanel() {
  const [agent, setAgent] = useState<(AgentState & { oi_shadow?: { state: string; reason: string; samples?: number; oi_change?: number; price_change?: number }; warmup_history?: string; strategy_version?: string; realized_pnl_today?: number; risk_controls?: { max_losses: number; daily_loss_limit: number; cooldown_minutes: number; stop_points: number; trail: string; target: string } }) | null>(null);
  const [report, setReport] = useState<any | null>(null);
  const [audit, setAudit] = useState<any | null>(null);
  const [error, setError] = useState("");
  const refresh = async () => {
    try { const [status, journal, dayAudit] = await Promise.all([apiClient.getPaperAgentStatus(), apiClient.getPaperReport(), apiClient.getPaperDayAudit()]); setAgent(status); setReport(journal); setAudit(dayAudit); setError(""); }
    catch (err) { setError(err instanceof Error ? err.message : "Could not load paper agent."); }
  };
  useEffect(() => { refresh(); const timer = window.setInterval(refresh, 2000); return () => window.clearInterval(timer); }, []);
  const change = async (action: "start" | "stop") => {
    try { setAgent(action === "start" ? await apiClient.startPaperAgent() : await apiClient.stopPaperAgent()); setError(""); }
    catch (err) { setError(err instanceof Error ? err.message : "Paper agent action failed."); }
  };
  const trade = agent?.open_trade;
  const signal = agent?.signal_diagnostics;
  const signalText = !signal ? "Connecting"
    : signal.phase === "late_start_warming_up" ? `Late start: ${signal.analysis_candles ?? 0}/3 consecutive completed candles after the first observed minute`
    : signal.phase === "missing_opening_history" ? "Opening candles are missing; waiting for verified 09:15 history before allowing entries"
    : signal.phase === "waiting_session_confirmation" ? "History loaded; waiting for 3 completed candles from today to confirm the opening trend"
    : signal.phase === "warming_up" ? `Warming up: ${signal.completed_candles}/${signal.required_candles} closed 1m candles`
      : signal.phase === "market_bullish" ? "BANKNIFTY regime bullish; waiting for a fresh market confirmation before the next signal"
        : signal.phase === "market_bearish" ? "BANKNIFTY regime bearish; waiting for a fresh market confirmation before the next signal"
          : signal.phase === "establishing_market_baseline" ? "Waiting for a fresh closed candle; a confirmed bullish or bearish trend can trigger the first paper entry"
            : "BANKNIFTY regime mixed/neutral; no directional trade signal";
  return <section className="rounded-lg border border-amber-700/50 bg-amber-950/20 p-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><h2 className="font-semibold text-amber-200">BANKNIFTY paper agent</h2><p className="mt-1 text-sm text-slate-300">Simulation only — broker orders are disabled.</p></div>
      <div className="flex gap-2"><button disabled className="rounded bg-slate-700 px-3 py-1.5 text-sm opacity-50">Strategy retired</button><button onClick={() => change("stop")} disabled={!agent?.running} className="rounded bg-slate-700 px-3 py-1.5 text-sm disabled:opacity-50">Stop</button></div>
    </div>
    <p className={`mt-3 text-sm ${error ? "text-red-300" : "text-slate-300"}`}>{error || agent?.message || "Loading…"}</p>
    <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-3">
      <div><dt className="text-slate-400">Active strategy</dt><dd>{agent?.strategy_version ?? "Older backend — restart required"}</dd></div>
      <div><dt className="text-slate-400">Readiness</dt><dd>3 completed session candles; earliest 09:18 IST</dd></div>
      <div><dt className="text-slate-400">Position</dt><dd>3 lots, 2 ITM; {agent?.strategy_version === "opening-trend-v3.2" ? "₹2,700 planned risk + ₹270 tolerance" : "planned risk capped at ₹2,700"}</dd></div>
      <div><dt className="text-slate-400">Daily entries</dt><dd>{agent?.trades_today ?? 0}/{agent?.max_trades ?? "—"}</dd></div>
      <div><dt className="text-slate-400">Initial stop</dt><dd>{agent?.risk_controls?.stop_points ?? "—"} premium points</dd></div>
      <div><dt className="text-slate-400">Exit</dt><dd>{agent?.risk_controls?.target ?? "Check active backend"}</dd></div>
      <div><dt className="text-slate-400">Trailing</dt><dd>{agent?.risk_controls?.trail ?? "Check active backend"}</dd></div>
      <div><dt className="text-slate-400">Loss lockout</dt><dd>{agent?.risk_controls?.max_losses ?? "—"} losses or ₹{agent?.risk_controls?.daily_loss_limit ?? "—"}</dd></div>
      <div><dt className="text-slate-400">Today</dt><dd>{agent?.losses_today ?? 0} losses · ₹{agent?.realized_pnl_today ?? 0} realized</dd></div>
      <div><dt className="text-slate-400">After exit</dt><dd>{agent?.risk_controls?.cooldown_minutes ?? "—"} minute cooldown</dd></div>
    </dl>
    <p className="mt-3 rounded bg-slate-900/60 px-3 py-2 text-xs text-sky-200">{signalText}</p>
    {agent?.warmup_history && <p className="mt-2 text-xs text-slate-400">{agent.warmup_history}</p>}
    {agent?.strategy_version === "opening-trend-v3.2" && <p className="mt-2 text-xs text-amber-200">Risk allowance: up to ₹2,970 planned per trade and ₹5,670 projected daily loss. New entries still stop after 2 losses or ₹5,400 realized loss. Actual fills can exceed planned loss.</p>}
    {signal?.readiness_mode === "observed_session" && <p className="mt-2 text-xs text-amber-200">Late-start mode: opening history unavailable. VWAP and trend anchor use observed candles; the first potentially partial minute is excluded.</p>}
    <p className="mt-2 rounded bg-slate-900/60 px-3 py-2 text-xs text-sky-200">OI shadow: {agent?.oi_shadow?.state ?? "Awaiting updated backend"}. {agent?.oi_shadow?.reason} {agent?.oi_shadow?.oi_change != null && <>OI change: {agent.oi_shadow.oi_change}; futures price change: {agent.oi_shadow.price_change}. </>}Research only; entries and stops are unchanged. Collection runs while the paper agent is active.</p>
    <p className="mt-2 rounded bg-slate-900/60 px-3 py-2 text-xs text-slate-300">Latest closed candle patterns: {signal?.latest_patterns?.length ? signal.latest_patterns.join(", ") : "no named pattern detected"}.</p>
    <p className="mt-2 rounded bg-slate-900/60 px-3 py-2 text-xs text-slate-300">Candle model data: {agent?.pattern_data?.labeled_samples ?? 0} labeled 5-minute outcomes from {agent?.pattern_data?.trading_days ?? 0} trading days ({agent?.pattern_data?.samples ?? 0} candles). Pattern model is collecting real samples; paper entries still use the current BANKNIFTY regime until a model is trained and time-ordered validation is reviewed.</p>
    {trade && <div className="mt-4 rounded border border-slate-700 bg-slate-900/60 p-3 text-sm"><div className="flex justify-between gap-3"><b>{trade.symbol}</b><span className={(trade.unrealized_pnl ?? 0) >= 0 ? "text-emerald-300" : "text-red-300"}>P&L ₹{trade.unrealized_pnl ?? "—"}</span></div><p className="mt-1 text-slate-300">Entry {trade.entry_price} · Mark {trade.mark_price ?? "—"} · Stop {trade.stop_price} · Target {trade.target_price} · Qty {trade.quantity}</p></div>}
    {!!agent?.history?.length && <p className="mt-3 text-xs text-slate-400">Latest closed paper trade: {agent.history[0].symbol} · {agent.history[0].exit_reason} · ₹{agent.history[0].unrealized_pnl}</p>}
    <div className="mt-4 rounded border border-slate-700 bg-slate-900/40 p-3 text-xs"><div className="flex justify-between"><b>Today’s audit log</b><span className="text-slate-400">{audit?.candles?.length ?? 0} closed candles · {audit?.events?.length ?? 0} events</span></div>{audit?.events?.slice(-3).reverse().map((item: any, index: number) => <p key={index} className="mt-1 text-slate-300">{item.event_type}{item.direction ? ` (${item.direction})` : ""}: {item.details}</p>)}</div>
    <div className="mt-5 border-t border-slate-700 pt-3"><div className="flex items-center justify-between"><h3 className="text-sm font-semibold">Paper-test results</h3><a href={apiClient.getPaperReportCsvUrl()} className="text-xs text-sky-300 hover:underline">Download CSV</a></div><p className="mt-1 text-xs text-slate-400">Closed trades: {report?.trade_count ?? 0} · Wins: {report?.wins ?? 0} · Win rate: {report?.win_rate ?? 0}% · Total P&amp;L: ₹{report?.total_pnl ?? 0}</p>{report?.trades?.slice(0, 5).map((item: any, index: number) => <p key={index} className="mt-1 text-xs text-slate-300">{item.symbol} · {item.exit_reason} · {item.points_pnl} pts · ₹{item.pnl}</p>)}</div>
  </section>;
}
