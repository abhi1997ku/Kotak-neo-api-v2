from __future__ import annotations

from typing import Iterable

from screener.feature_engine import SymbolSetup


def render_cli_table(rows: Iterable[SymbolSetup]) -> str:
    """Render a compact CLI table with key trade details."""
    rows = list(rows)
    if not rows:
        return "No valid setups found."

    headers = ["Symbol", "Setup", "Entry", "Stop", "Target", "R:R", "Qty", "Rationale"]
    lines = [" | ".join(headers)]
    for row in rows:
        values = [
            row.symbol,
            row.setup_type,
            f"{row.entry:.2f}",
            f"{row.stop:.2f}",
            f"{row.target:.2f}",
            f"{row.rr:.2f}",
            str(row.suggested_qty),
            row.rationale[:80],
        ]
        lines.append(" | ".join(values))
    return "\n".join(lines)
