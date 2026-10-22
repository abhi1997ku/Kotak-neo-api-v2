"""Evaluate 20/25% target-before-stop probabilities, without changing paper accounts."""
from collections import Counter
import copy
import gzip
import hashlib
import json

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import brier_score_loss

from screener.stock_ai_research import OUTPUT as AI_OUTPUT, FEATURES
from screener.stock_paper import advance, new_account, queue_setups
from screener.stock_paper_backtest import OUTPUT as SOURCE
from screener.stock_selective import HOLD_SESSIONS, design_candidate, wilson_lower

OUTPUT = SOURCE.parent / "stock_selective_research"


def label_records(prepared, target):
    indexes = {day: i for i, day in enumerate(prepared["sessions"])}
    records = []
    for item in prepared["records"]:
        candidate = design_candidate(item["candidate"], target)
        if candidate["status"] == "REJECTED":
            continue
        i = indexes[item["day"]]
        future = prepared["sessions"][i+1:i+1+HOLD_SESSIONS]
        record = dict(id=item["id"], day=item["day"], symbol=candidate["symbol"], x=item["x"],
                      y=None, outcome="CENSORED", label_end=None, trade=None)
        if len(future) != HOLD_SESSIONS:
            records.append(record)
            continue
        account = new_account()
        account["last_session"] = item["day"]
        # Isolated hypothetical labels, NOT authorization by the production gate.
        candidate["status"] = "QUALIFIED"
        queue_setups(account, [candidate], item["day"], item["day"])
        for day in future:
            advance(account, prepared["execution"][day], [day])
            if account["warnings"]:
                break
        record["label_end"] = future[-1]
        if account["warnings"]:
            record["outcome"] = "MISSING_DATA"
        elif not account["trades"]:
            record["outcome"] = "SKIPPED"
            record["skip_reason"] = account["orders"][0]["outcome_reason"]
        else:
            trade = account["trades"][0]
            if trade["status"] == "OPEN":
                raise AssertionError("Unresolved label at holding limit")
            record.update(y=int(trade["status"] == "TARGET"), outcome=trade["status"], trade=trade)
        records.append(record)
    return records


def nonoverlapping(records):
    """Do not count repeated signals on a held symbol as independent examples."""
    end_by_symbol, selected = {}, []
    for record in sorted(records, key=lambda r: (r["day"], r["symbol"])):
        if record["y"] is None or record["day"] <= end_by_symbol.get(record["symbol"], ""):
            continue
        selected.append(record)
        end_by_symbol[record["symbol"]] = record["label_end"]
    return selected


def split_records(records, calibration_start, test_start):
    train = [r for r in records if r["label_end"] < calibration_start]
    calibration = [r for r in records if calibration_start <= r["day"] < test_start and r["label_end"] < test_start]
    test = [r for r in records if r["day"] >= test_start]
    return train, calibration, test


def logits(probabilities):
    clipped = np.clip(probabilities, 1e-6, 1-1e-6)
    return np.log(clipped / (1-clipped)).reshape(-1, 1)


def estimate(records, calibration_start, test_start):
    train, calibration, test = split_records(records, calibration_start, test_start)
    result = dict(training_samples=len(train), calibration_samples=len(calibration), test_samples=len(test),
                  status="INSUFFICIENT_DATA", selected_samples=0, selected_hits=0,
                  selected_hit_rate=None, selected_wilson_lower=None, predictions=[], calibration_bins=[])
    enough = (len(train) >= 100 and len(calibration) >= 50 and len(test) >= 30
              and min(Counter(r["y"] for r in train).get(k, 0) for k in (0, 1)) >= 10
              and min(Counter(r["y"] for r in calibration).get(k, 0) for k in (0, 1)) >= 5)
    if not enough:
        return result
    model = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=2000, random_state=42))
    model.fit([r["x"] for r in train], [r["y"] for r in train])
    # Platt calibration is fit only on a later, purged calibration block.
    calibrator = LogisticRegression(C=1.0, max_iter=2000, random_state=42)
    calibrator.fit(logits(model.predict_proba([r["x"] for r in calibration])[:, 1]), [r["y"] for r in calibration])
    probabilities = calibrator.predict_proba(logits(model.predict_proba([r["x"] for r in test])[:, 1]))[:, 1]
    selected = [r for r, p in zip(test, probabilities) if p > 0.8]
    hits = sum(r["y"] for r in selected)
    result.update(status="RESEARCH_ONLY", selected_samples=len(selected), selected_hits=hits,
                  selected_hit_rate=hits / len(selected) if selected else None,
                  selected_wilson_lower=wilson_lower(hits, len(selected)) if selected else None,
                  test_brier=float(brier_score_loss([r["y"] for r in test], probabilities)),
                  max_predicted_probability=float(max(probabilities)),
                  train_last_label=max(r["label_end"] for r in train), calibration_last_label=max(r["label_end"] for r in calibration),
                  predictions=[dict(id=r["id"], day=r["day"], probability=float(p), actual=r["y"]) for r, p in zip(test, probabilities)])
    for lower in (0, 0.2, 0.4, 0.6, 0.8):
        group = [(r, p) for r, p in zip(test, probabilities) if lower <= p < lower + 0.2 or (lower == 0.8 and p == 1)]
        result["calibration_bins"].append(dict(lower=lower, upper=lower+0.2, count=len(group),
                                               mean_prediction=float(np.mean([p for r, p in group])) if group else None,
                                               hit_rate=sum(r["y"] for r, p in group)/len(group) if group else None))
    return result


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    source_hash = hashlib.sha256((SOURCE / "source_history.json.gz").read_bytes()).hexdigest()
    parent = AI_OUTPUT / "prepared.json.gz"
    with gzip.open(parent, "rt", encoding="utf-8") as stream:
        saved = json.load(stream)
    if not saved["key"].startswith(source_hash):
        raise ValueError("Prepared real-data cache does not match historical source")
    prepared = saved["data"]
    days = prepared["sessions"]
    calibration_start, test_start = days[-252], days[-126]
    protocol = dict(targets=[20, 25], primary_target=20, maximum_stop_pct=5, probability_threshold=0.8,
                    threshold_comparison=">", holding_sessions=HOLD_SESSIONS, source_sha256=source_hash,
                    feature_cache_sha256=hashlib.sha256(parent.read_bytes()).hexdigest(), features=FEATURES,
                    calibration_start=calibration_start, test_start=test_start,
                    model="StandardScaler + LogisticRegression(C=1), followed by sigmoid calibration on a later purged block",
                    evaluation="Separate 20% and 25% labels. No target/model/threshold search. Nonoverlapping per-symbol 60-session windows.",
                    evidence_requirements="At least 100 selected later/forward outcomes, point rate and approximate 95% lower bound >80%, calibration assessment and independent forward confirmation. No automatic activation.")
    (OUTPUT / "protocol.json").write_text(json.dumps(protocol, indent=2), encoding="utf-8")
    results = {}
    for target in (20, 25):
        records = label_records(prepared, target)
        independent = nonoverlapping(records)
        hits = sum(r["y"] for r in independent)
        results[str(target)] = dict(total_setups=len(records), outcomes=dict(Counter(r["outcome"] for r in records)),
                                    nonoverlapping_samples=len(independent), target_hits=hits,
                                    target_hit_rate=hits/len(independent) if independent else None,
                                    lower_bound=wilson_lower(hits, len(independent)),
                                    probability_research=estimate(independent, calibration_start, test_start), records=records)
        print(json.dumps({k: v for k, v in results[str(target)].items() if k not in ("records", "probability_research")}), flush=True)
    report = dict(protocol=protocol, results=results, decision="UNVALIDATED_NO_NEW_ENTRIES")
    (OUTPUT / "results.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    lines = ["# Selective swing probability research", "", "Decision: **UNVALIDATED — no new paper entries**.", "",
             "Success = chosen gross target hit before the structural stop (maximum 5% from actual fill), within 60 trading sessions. Time exits count as failures even when profitable. Stop-first daily-bar ambiguity; fees/slippage and gap fills use the paper engine.", "",
             "| Gross target | Nonoverlapping filled examples | Target hits | Historical target-hit rate | Approx. 95% lower bound |", "|---|---:|---:|---:|---:|"]
    for target, r in results.items():
        rate = f"{100*r['target_hit_rate']:.2f}%" if r['target_hit_rate'] is not None else "N/A"
        lines.append(f"| {target}% | {r['nonoverlapping_samples']} | {r['target_hits']} | {rate} | {100*r['lower_bound']:.2f}% |")
    lines += ["", "## Chronological probability model", "", f"Calibration starts {calibration_start}; later testing starts {test_start}. Entire 60-session label windows must end before the following block.", "",
              "| Target | Train / calibration / later samples | Model status | Later predictions >80% | Target hits in that group |", "|---|---|---|---:|---:|"]
    for target, r in results.items():
        m = r["probability_research"]
        lines.append(f"| {target}% | {m['training_samples']} / {m['calibration_samples']} / {m['test_samples']} | {m['status']} | {m['selected_samples']} | {m['selected_hits']} |")
    lines += ["", "## Limits", "",
              "- A historical hit rate is not an individual stock's probability. No confidence value is assigned when data is insufficient. Ordinary classification accuracy is not used: predicting every setup will fail can be highly accurate but useless for selecting trades.",
              "- Model fit requires >=100 training, >=50 calibration and >=30 later examples, with both outcomes represented. Fixed logistic classifier and sigmoid calibration; no tuning on later data. If those requirements fail, no model is fitted.",
              "- The feature cache contains only causal, confirmed-breakout features from the prior research. Old regression labels are discarded and replaced with the new target-before-stop outcomes. Source hash is verified.",
              "- Sample results are isolated candidate outcomes, not a realizable portfolio return. Four-position, sector and weekly limits would further restrict actual trades. Overlapping signals on the same stock are removed for probability evaluation; cross-stock market correlation still remains.",
              "- Recent signals without a full 60-session outcome window are censored, not classified as losses. Missing data and skipped openings are excluded from filled-trade probability estimates.",
              "- Current universe membership introduces survivorship bias; the historical period was already inspected. Wilson bounds assume independent Bernoulli outcomes and are only approximate here, not proof of an 80% edge.",
              "- 20% is the primary research target; 25% needs its own evidence. Reaching 20% does not count as success for the 25% target. No partial-profit or breakeven exit is added to inflate the target-hit rate.",
              "- Stop distance is a gross price level, not a guaranteed maximum loss. Gaps and transaction costs can exceed 5%. Cash risk is limited separately by position size.",
              "- No research artifact is loaded by the live scanner. The selective mandate blocks new entries until a validated model is integrated and independently confirmed. Existing filled trades retain their original exit rules.", "",
              "[Full setup labels, model audit and probabilities](./results.json)", "",
              "[Probability calibration reference](https://scikit-learn.org/stable/modules/calibration.html)"]
    (OUTPUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("UNVALIDATED_NO_NEW_ENTRIES", flush=True)


if __name__ == "__main__":
    main()
