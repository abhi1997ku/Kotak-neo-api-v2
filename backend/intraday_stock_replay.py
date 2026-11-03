"""Reconstruct cached shortlists without fabricating timely recommendations."""
import json
from collections import Counter
import pandas as pd
from backend.intraday_stock_store import ROOT
from backend.indstocks_monitor import completed_bars
from screener.intraday_stocks import rank


def replay(root=ROOT):
    mapping = json.loads((root/"universe.json").read_text())
    now = pd.Timestamp.now(tz="Asia/Kolkata")
    benchmark, _ = completed_bars(json.loads((root/"history"/(mapping["benchmark"]+".json")).read_text()), now)
    day = mapping["as_of"]
    slots = ("09:45", "11:00")
    rows = {s: [] for s in slots}
    rejected = {s: Counter() for s in slots}
    for i, (symbol, code) in enumerate(mapping["stocks"].items()):
        frame, _ = completed_bars(json.loads((root/"history"/(code+".json")).read_text()), now)
        for slot in slots:
            cutoff = pd.Timestamp(day+" "+slot, tz="Asia/Kolkata")
            if cutoff > now:
                continue
            selected, failures = rank({symbol: frame}, benchmark, cutoff)
            rows[slot].extend(selected)
            rejected[slot].update(failures.values())
        if (i+1) % 100 == 0:
            print("Evaluated", i+1, flush=True)
    report = dict(date=day, historical_reconstruction=True, fills_enabled=False,
                  scoring_version="smooth_v2", universe_count=len(mapping["stocks"]),
                  snapshots={s: dict(rows=[r for side in ("long", "short") for r in
                      sorted([x for x in rows[s] if x["side"] == side], key=lambda x: (-x["ranking_score"], x["symbol"]))[:3]],
                      exclusion_counts=dict(rejected[s])) for s in slots})
    path = root/"today_reconstruction_v2.json"
    path.write_text(json.dumps(report, indent=2))
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    replay()
