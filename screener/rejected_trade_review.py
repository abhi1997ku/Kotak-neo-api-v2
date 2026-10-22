"""Sparse-snapshot counterfactual, NOT an executable intratick backtest."""
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.paper_trend import TrendRunnerRules


def main():
    day = "2026-10-14"
    db = sqlite3.connect("file:backend/paper_trading.db?mode=ro", uri=True)
    events = db.execute("SELECT occurred_at,event_type,direction,ltp,details FROM paper_day_events WHERE trade_date=? ORDER BY id", (day,)).fetchall()
    snapshots = sorted([json.loads(e[4]) for e in events if e[1] == "oi_chain_snapshot"], key=lambda s: s['received_at'])
    results = []
    for stamp, kind, direction, spot, reason in events:
        if kind != "entry_skipped":
            continue
        at = datetime.fromisoformat(stamp).timestamp()
        result = {"time_ist": datetime.fromtimestamp(at, ZoneInfo('Asia/Kolkata')).strftime('%H:%M:%S'), "direction": direction, "rejection": reason, "classification": "not_verifiable"}
        # Choose contract using only a chain already received by the rejection time.
        before = [s for s in snapshots if s['received_at'] <= at]
        if not before or at-before[-1]['received_at'] > 120:
            result['missing'] = 'No recent pre-rejection chain to infer contract'
            results.append(result)
            continue
        prefix = 'call' if direction == 'long' else 'put'
        rows = sorted([r for r in before[-1]['chain']['chains'] if r.get(prefix+'_token')], key=lambda r:r['strike_price'])
        atm = min(range(len(rows)),key=lambda i:abs(rows[i]['strike_price']-spot))
        index = atm-2 if direction == 'long' else atm+2
        if not 0 <= index < len(rows):
            result['missing'] = 'Selected strike outside saved chain'
            results.append(result)
            continue
        contract = rows[index]
        token = contract[prefix+'_token']
        marks = []
        for s in snapshots:
            if s['received_at'] < at or s.get('latency_seconds',999) > 3:
                continue
            row = next((r for r in s['chain']['chains'] if r.get(prefix+'_token')==token),None)
            if row and 0 < row.get(prefix+'_bid',0) <= row.get(prefix+'_ask',0):
                marks.append((s['received_at'],row[prefix+'_bid'],row[prefix+'_ask']))
        result['inferred_symbol'] = contract[prefix+'_symbol']
        if not marks or marks[0][0]-at > 120:
            result['missing'] = 'No valid entry snapshot within 120 seconds'
            results.append(result)
            continue
        start, bid, ask = marks[0]
        result.update(entry_delay_seconds=round(start-at,1), entry_ask=ask, entry_spread=round(ask-bid,2), passes_spread_check=ask-bid <= min(3,.005*ask))
        rules = TrendRunnerRules()
        trade = rules.open_trade(contract[prefix+'_symbol'],int(contract[prefix+'_lot_size'])*3,ask,direction)
        previous = start
        gaps = []
        for received,bid,ask in marks:
            gaps.append(received-previous)
            previous = received
            rules.on_tick(trade,bid)
            local = datetime.fromtimestamp(received,ZoneInfo('Asia/Kolkata'))
            if trade.status == 'open' and (local.hour,local.minute) >= (15,20):
                rules.close_for_market_close(trade,bid)
            if trade.status == 'closed':
                result.update(sampled_outcome='profit' if trade.exit_price>trade.entry_price else 'loss',sampled_pnl=round((trade.exit_price-trade.entry_price)*trade.quantity,2),exit_bid=trade.exit_price,exit_time_ist=local.strftime('%H:%M:%S'),exit_reason=trade.exit_reason)
                break
        result.update(max_quote_gap_seconds=round(max(gaps,default=0),1),note='Independent delayed-entry scenario; intervening stops/peaks unknown; no fees; no daily limits or overlapping-position checks. Exact selected contract was not logged for skipped entries.')
        results.append(result)
    out=Path('screener/index_research_cache/rejected_2026_10_14')
    out.mkdir(parents=True,exist_ok=True)
    (out/'results.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    print(json.dumps(results,indent=2))


if __name__ == '__main__':
    main()
