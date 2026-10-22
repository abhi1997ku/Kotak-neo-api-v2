import { API_BASE_URL } from "../services/api";
import { useState } from "react";

type Metrics = {
  closed_trades: number; win_rate: number | null; total_r: number; mean_r: number | null;
  profit_factor: number | null; double_cost_net_r: number; max_drawdown_r: number;
  longest_losing_streak: number; win_rate_interval: [number, number] | null;
  timeouts: number; profitable_rate: number | null; brier_score_all_patterns: number;
  average_holding_bars: number | null; signal_coverage_pct: number;
};
type Experiment = {
  symbol: string; configurations: number; walk_forward_simulations: number;
  observed_80_percent_goal: boolean; evidence_gate_passed: boolean; unsupported_high_rate_trials: number;
  best_validation: {
    config: { model: string; features: string; horizon: number; risk_scale: number; threshold: number };
    validation: Metrics; folds: { closed_trades: number; win_rate: number | null; total_r: number }[];
  };
  test: Metrics & { period_start: string; period_end: string };
  notes: string[];
};

const percent = (n: number | null) => n === null ? "No trades" : `${n.toFixed(2)}%`;

export function IndexExperimentResults() {
  const [results, setResults] = useState<Experiment[]>([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  async function load() {
    setBusy(true); setMessage("");
    try {
      const response = await fetch(`${API_BASE_URL}/index-swing/experiments`);
      if (!response.ok) throw new Error("Could not load saved experiments. Check the backend.");
      const data = await response.json();
      setResults(data.results);
      if (data.status === "NOT_RUN") setMessage("No completed search yet. Results appear here after the offline research run completes.");
    } catch (e) { setMessage(e instanceof Error ? e.message : "Could not load experiments"); }
    finally { setBusy(false); }
  }
  return <div className="mt-5 border-t border-slate-700 pt-4">
    <div className="flex items-center justify-between gap-3">
      <div><h3 className="font-semibold">Expanded model experiments</h3>
        <p className="text-xs text-slate-400">Three chronological validation periods select the model; the final period evaluates it. Research results do not replace the active agent.</p></div>
      <button disabled={busy} onClick={load} className="shrink-0 rounded bg-slate-700 px-3 py-2 text-sm disabled:opacity-50">{busy ? "Loading…" : "Load research results"}</button>
    </div>
    {message && <p role="status" className="mt-3 text-sm text-amber-300">{message}</p>}
    <div className="mt-3 grid gap-4 xl:grid-cols-2">{results.map(r => <article key={r.symbol} className="rounded border border-slate-700 p-3 text-sm">
      <h4 className="font-semibold">{r.symbol} — {r.configurations.toLocaleString()} configurations / {r.walk_forward_simulations.toLocaleString()} validation simulations</h4>
      <p className="mt-1 text-amber-300">{r.observed_80_percent_goal ? "Above 80% in this final-period sample; future accuracy unverified" : "80% target-hit goal not achieved"}</p>
      <p className="text-xs text-slate-400">{r.evidence_gate_passed ? "Historical evidence criteria passed; prospective validation still required." : "Insufficient evidence for the requested accuracy."}</p>
      <p className="mt-2 text-xs">Selected: {r.best_validation.config.model} / {r.best_validation.config.features} · {r.best_validation.config.risk_scale}× stop budget · {r.best_validation.config.horizon} session-bar limit · {(100*r.best_validation.config.threshold).toFixed(0)}% model threshold</p>
      <table className="mt-3 w-full text-left text-xs">
        <thead><tr><th className="py-1">Metric</th><th>Walk-forward validation</th><th>Final period</th></tr></thead>
        <tbody>
          <tr><td className="py-1">Full 3R target-hit rate</td><td>{percent(r.best_validation.validation.win_rate)}</td><td>{percent(r.test.win_rate)}</td></tr>
          <tr><td className="py-1">Trades</td><td>{r.best_validation.validation.closed_trades}</td><td>{r.test.closed_trades}</td></tr>
          <tr><td className="py-1">Net R after costs</td><td>{r.best_validation.validation.total_r}</td><td>{r.test.total_r}</td></tr>
          <tr><td className="py-1">Expectancy (R/trade)</td><td>{r.best_validation.validation.mean_r ?? "—"}</td><td>{r.test.mean_r ?? "—"}</td></tr>
          <tr><td className="py-1">Profit factor</td><td>{r.best_validation.validation.profit_factor ?? "Undefined"}</td><td>{r.test.profit_factor ?? "Undefined"}</td></tr>
        </tbody>
      </table>
      <p className="mt-2 text-xs text-slate-400">Final period: {new Date(r.test.period_start).toLocaleDateString()}–{new Date(r.test.period_end).toLocaleDateString()}. This history was used during development; it is not a fresh prospective trial.</p>
      <details className="mt-3"><summary className="cursor-pointer text-indigo-300">Risk, reliability & fold metrics</summary>
        <div className="mt-2 space-y-1 text-xs">
          <p>95% target-rate interval: {r.test.win_rate_interval?.map(v => `${v}%`).join("–") ?? "No trades"}; trade dependence and model selection add uncertainty.</p>
          <p>Closed-trade drawdown: {r.test.max_drawdown_r}R · Longest losing streak: {r.test.longest_losing_streak}</p>
          <p>Double-cost stress: {r.test.double_cost_net_r}R · Profitable exits: {percent(r.test.profitable_rate)} · Time exits: {r.test.timeouts}</p>
          <p>Average holding time: {r.test.average_holding_bars ?? "—"} session bars · Signal coverage: {r.test.signal_coverage_pct}%</p>
          <p>Brier score: {r.test.brier_score_all_patterns} (both directions, all patterns; lower is better)</p>
          {r.best_validation.folds.map((f, i) => <p key={i}>Validation period {i+1}: {f.closed_trades} trades · {percent(f.win_rate)} target hits · {f.total_r}R</p>)}
          <p>{r.unsupported_high_rate_trials} trials above 80% had insufficient validation samples and were not accepted as evidence.</p>
          {r.notes.map(note => <p key={note} className="text-slate-400">{note}</p>)}
        </div>
      </details>
    </article>)}</div>
  </div>;
}
