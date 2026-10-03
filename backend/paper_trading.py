"""Paper-only execution rules for the BANKNIFTY short-signal experiment.

This module deliberately contains no broker-order call.  It turns an accepted
entry price and subsequent option ticks into an auditable virtual trade.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Literal


ExitReason = Literal["stop_loss", "target", "signal_reversal", "market_close"]


@dataclass
class PaperTrade:
    symbol: str
    quantity: int
    entry_price: float
    stop_price: float
    target_price: float
    opened_at: str
    status: str = "open"
    exit_price: float | None = None
    exit_reason: ExitReason | None = None
    closed_at: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


class PaperShortOptionRules:
    """Manage a long PE opened from a bearish BANKNIFTY signal.

    The name refers to the underlying direction: buying a PE is a bearish
    directional position.  All values are option-premium points.
    """

    stop_loss_points = 30.0
    target_points = 90.0
    trail_levels = ((30.0, 10.0), (60.0, 30.0), (80.0, 50.0))
    paper_lots = 3

    def quantity_for_lot_size(self, lot_size: int) -> int:
        """Return the configured paper quantity using the live contract lot size."""
        if lot_size <= 0:
            raise ValueError("Contract lot size must be greater than zero.")
        return self.paper_lots * lot_size

    def open_trade(self, symbol: str, quantity: int, entry_price: float) -> PaperTrade:
        if not symbol.strip():
            raise ValueError("Option symbol is required.")
        if quantity <= 0:
            raise ValueError("Quantity must be greater than zero.")
        if entry_price <= 0:
            raise ValueError("Entry premium must be greater than zero.")
        return PaperTrade(
            symbol=symbol,
            quantity=quantity,
            entry_price=entry_price,
            stop_price=entry_price - self.stop_loss_points,
            target_price=entry_price + self.target_points,
            opened_at=datetime.now(timezone.utc).isoformat(),
        )

    def on_tick(self, trade: PaperTrade, premium: float) -> PaperTrade:
        """Apply profit trailing before testing exits, using the supplied tick."""
        if trade.status != "open":
            return trade
        if premium <= 0:
            raise ValueError("Option premium must be greater than zero.")

        profit = premium - trade.entry_price
        for trigger, protected_profit in self.trail_levels:
            if profit >= trigger:
                trade.stop_price = max(trade.stop_price, trade.entry_price + protected_profit)

        if premium >= trade.target_price:
            return self._close(trade, premium, "target")
        if premium <= trade.stop_price:
            return self._close(trade, premium, "stop_loss")
        return trade

    def close_for_signal_reversal(self, trade: PaperTrade, premium: float) -> PaperTrade:
        return self._close(trade, premium, "signal_reversal") if trade.status == "open" else trade

    def close_for_market_close(self, trade: PaperTrade, premium: float) -> PaperTrade:
        return self._close(trade, premium, "market_close") if trade.status == "open" else trade

    @staticmethod
    def _close(trade: PaperTrade, premium: float, reason: ExitReason) -> PaperTrade:
        trade.status = "closed"
        trade.exit_price = premium
        trade.exit_reason = reason
        trade.closed_at = datetime.now(timezone.utc).isoformat()
        return trade
