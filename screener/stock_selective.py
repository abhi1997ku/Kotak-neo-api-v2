"""Selective paper-trading mandate. No unvalidated probability can authorize a trade."""
from __future__ import annotations

import math

POLICY_ID = "selective_20_25_v1"
HOLD_SESSIONS = 60


def policy():
    return dict(id=POLICY_ID, name="Selective swing research", target_min_pct=20,
                target_max_pct=25, max_stop_pct=5, min_probability_pct=80,
                probability_comparison="strictly_greater_than", hold_sessions=HOLD_SESSIONS,
                validation_status="UNVALIDATED", entries_enabled=False,
                monthly_goal_pct=None,
                message="New entries paused: no validated model establishes greater than 80% target-before-stop probability.")


def wilson_lower(wins, count):
    """Approximate 95% lower bound; correlated trades need additional validation."""
    if not count:
        return 0.0
    p, z = wins / count, 1.959963984540054
    return (p + z*z/(2*count) - z * math.sqrt(p*(1-p)/count + z*z/(4*count*count))) / (1 + z*z/count)


def design_candidate(candidate, target_pct=20):
    """Research levels, never a probability or an authorization to fill."""
    if target_pct not in (20, 25):
        raise ValueError("Use a separately validated 20% or 25% target")
    result = dict(candidate, strategy=POLICY_ID, probability=None,
                  probability_status="UNVALIDATED", target_return_pct=target_pct,
                  max_stop_pct=5, hold_sessions=HOLD_SESSIONS)
    if candidate["status"] == "QUALIFIED":
        reference = candidate["reference"]
        result["target"] = round(reference * (1 + target_pct / 100), 2)
        if (reference - candidate["stop"]) / reference > 0.05:
            result.update(status="REJECTED", reason="Structural stop exceeds the 5% maximum; skip rather than force a tighter stop")
        else:
            result.update(status="WATCH", reason="Technical setup only: >80% target-before-stop probability is unvalidated; no paper entry")
    return result


def cancel_unvalidated_orders(account, day):
    """Preserve history and open-trade rules, but never fill an old queued idea."""
    for order in account["orders"]:
        if order["status"] == "QUEUED":
            reason = "Superseded by selective mandate: validated >80% probability required"
            order.update(status="CANCELLED", outcome_date=day, outcome_reason=reason)
            account["events"].append(dict(date=day, symbol=order["symbol"], kind="CANCELLED", detail=reason))
