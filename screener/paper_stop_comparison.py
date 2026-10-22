"""Offline structure-stop sensitivity study. Never imports or controls the live agent.

This cannot rank option strategies without matched option quotes and chain history.
"""
import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

INDIA = ZoneInfo("Asia/Kolkata")


def atr(bars):
    ranges = []
    for i, bar in enumerate(bars):
        previous = bars[i-1]["close"] if i else bar["open"]
        ranges.append(max(bar["high"]-bar["low"], abs(bar["high"]-previous), abs(bar["low"]-previous)))
    return sum(ranges[-14:])/len(ranges[-14:])


def structure_trace(bars, entry_index, direction, buffer=0.25):
    if direction not in ("long", "short") or buffer < 0 or entry_index < 3:
        raise ValueError("Need a valid direction, non-negative buffer and completed setup bars")
    if any(b["minute"] != a["minute"]+1 for a,b in zip(bars,bars[1:])):
        raise ValueError("Missing or unordered candles; cannot reconstruct stop crossings")
    if entry_index >= len(bars):
        raise ValueError("Entry candle unavailable")
    short = direction == "short"
    setup = bars[:entry_index]
    level = max(b["high"] for b in setup[-3:]) if short else min(b["low"] for b in setup[-3:])
    stop = level + (1 if short else -1)*buffer*atr(setup)
    entry = bars[entry_index]["open"]
    if (short and stop <= entry) or (not short and stop >= entry):
        return {"status":"invalid_initial_stop", "entry_proxy":entry, "initial_stop":stop}
    result = {"status":"incomplete", "direction":direction,"buffer_atr":buffer,"entry_proxy":entry,
              "initial_stop":round(stop,2),"initial_futures_risk":round(abs(stop-entry),2),"updates":[],
              "option_pnl":None,"eligible_for_adoption":False}
    for i in range(entry_index,len(bars)):
        bar=bars[i]
        local=datetime.fromtimestamp(bar["minute"]*60,INDIA)
        crossed=bar["high"] >= stop if short else bar["low"] <= stop
        if local.strftime("%H:%M") >= "15:20" or crossed:
            timed=local.strftime("%H:%M") >= "15:20"
            exit_proxy=bar["open"] if timed else (max(bar["open"],stop) if short else min(bar["open"],stop))
            result.update(status="underlying_trigger_only",exit_bar_ist=local.isoformat(),
                          reason="time" if timed else "structure_stop",exit_proxy=round(exit_proxy,2),
                          directional_futures_points=round((entry-exit_proxy) if short else (exit_proxy-entry),2))
            break
        # Pivot i-2 is only knowable after candle i closes. New stop applies at i+1.
        p=i-2
        if p<2:
            continue
        key="high" if short else "low"
        pivot=bars[p][key]
        left=[b[key] for b in bars[p-2:p]]
        right=[b[key] for b in bars[p+1:i+1]]
        confirmed=(pivot>max(left) and pivot>=max(right)) if short else (pivot<min(left) and pivot<=min(right))
        if not confirmed:
            continue
        candidate=pivot+(1 if short else -1)*buffer*atr(bars[:i+1])
        tightens=(bar["close"]<candidate<stop) if short else (stop<candidate<bar["close"])
        if tightens:
            stop=candidate
            result["updates"].append({"known_after_minute":bar["minute"],"pivot_minute":bars[p]["minute"],"stop":round(stop,2)})
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot",type=Path,default=Path("screener/index_research_cache/paper_day_review_2026_10_12/candles.json"))
    parser.add_argument("--day",default="2026-10-12")
    parser.add_argument("--entry",default="09:18")
    parser.add_argument("--direction",choices=("long","short"),default="short")
    parser.add_argument("--output",type=Path,default=Path("screener/index_research_cache/stop_comparison_2026_10_12"))
    args=parser.parse_args()
    bars=[]
    for day, minute, o,h,l,c,v in json.loads(args.snapshot.read_text(encoding="utf-8")):
        local=datetime.fromtimestamp(minute*60,INDIA)
        if day==args.day and local.date().isoformat()==args.day and "09:15"<=local.strftime("%H:%M")<"15:30":
            bars.append(dict(minute=minute,open=o,high=h,low=l,close=c,volume=v))
    entry=next(i for i,b in enumerate(bars) if datetime.fromtimestamp(b["minute"]*60,INDIA).strftime("%H:%M")==args.entry)
    variants=[structure_trace(bars,entry,args.direction,buffer) for buffer in (0.25,0.5,1.0)]
    result={"day":args.day,"entry_ist":args.entry,"direction":args.direction,"regular_session_bars":len(bars),
            "current_premium_trail":{"status":"blocked_missing_option_bid_ask","net_pnl":None},
            "structure_traces":variants,
            "chain_assisted_trail":{"status":"blocked_missing_historical_chain_and_option_quotes","net_pnl":None},
            "decision":"retain_current_strategy","improvement_established":False,
            "method":"Identical underlying entry; closed-candle two-left/two-right pivots. Stop checked before update; gap-through uses next open. Only underlying crossing proxies, not option executions.",
            "limitations":["No option risk conversion, simulated option fills, fees, sizing or portfolio cooldown comparison.","Buffer sensitivity is exploratory on development data, not selection of a winner.","Intraminute option stop chronology and chain inputs are unavailable."]}
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/"results.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    lines=[f"# Stop comparison — {args.day}","",f"Same {args.entry} {args.direction} entry; {len(bars)} regular-session futures candles.","", "**No improvement established; active strategy unchanged.**", "", "| Research buffer | Initial futures risk | Futures stop-crossing candle IST | Directional futures points |", "|---|---:|---|---:|"]
    for v in variants:
        lines.append(f"| {v['buffer_atr']} ATR | {v['initial_futures_risk']} | {v.get('exit_bar_ist','incomplete')} | {v.get('directional_futures_points','unknown')} |")
    lines += ["", "The active premium trail and chain-assisted alternative cannot be ranked: opening-contract option bid/ask history and chain snapshots are missing. Futures points above are not option profits. Different buffer sizes also change initial risk, so larger point gains would not establish better risk-adjusted performance.", "", "All three buffers are reported; none is adopted based on this session. These are development-data sensitivities, not independent validation.", "", "Next evidence required: matched entry ask and continuous option bids through every candidate exit, timestamped strike-level OI/volume and basis data, actual costs, and independent sessions. Preserve quote observations even after the active trade exits."]
    (args.output/"REPORT.md").write_text('\n'.join(lines)+'\n',encoding="utf-8")
    print(json.dumps(result,indent=2))


if __name__=="__main__":
    main()
