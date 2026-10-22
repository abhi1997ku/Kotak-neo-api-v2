"""Paper-only execution rules for the BANKNIFTY short-signal experiment.

This module deliberately contains no broker-order call.  It turns an accepted
entry price and subsequent option ticks into an auditable virtual trade.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo


ExitReason = Literal["stop_loss", "trailing_stop", "target", "signal_reversal", "market_close"]
SignalDirection = Literal["long", "short"]


@dataclass
class PaperTrade:
    symbol: str
    quantity: int
    entry_price: float
    stop_price: float
    target_price: float | None
    opened_at: str
    direction: SignalDirection | None = None
    status: str = "open"
    exit_price: float | None = None
    exit_reason: ExitReason | None = None
    closed_at: str | None = None
    peak_premium: float | None = None

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

    def open_trade(self, symbol: str, quantity: int, entry_price: float, direction: SignalDirection | None = None) -> PaperTrade:
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
            direction=direction,
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

        if trade.target_price is not None and premium >= trade.target_price:
            return self._close(trade, premium, "target")
        if premium <= trade.stop_price:
            reason = "trailing_stop" if trade.stop_price > trade.entry_price else "stop_loss"
            return self._close(trade, premium, reason)
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


@dataclass
class OneMinuteCandle:
    minute: int
    open: float
    high: float
    low: float
    close: float
    volume: int


class BankNiftySignalEngine:
    """Closed-candle BANKNIFTY regime signals with a neutral no-trade state."""

    def __init__(self) -> None:
        self.candles: list[OneMinuteCandle] = []
        self.last_completed_candle: OneMinuteCandle | None = None
        self._current: OneMinuteCandle | None = None
        self._last_total_volume: int | None = None
        self._regime: Literal["bullish", "bearish", "neutral"] | None = None
        self._initialized = False
        self.session_day: str | None = None

    def _session_candles(self) -> list[OneMinuteCandle]:
        day = self.session_day or (self._candle_day(self.candles[-1]) if self.candles else "")
        return [c for c in self.candles if self._candle_day(c) == day]

    @staticmethod
    def _candle_day(candle: OneMinuteCandle) -> str:
        return datetime.fromtimestamp(candle.minute * 60, ZoneInfo("Asia/Kolkata")).date().isoformat()

    def on_tick(self, price: float, total_volume: int, timestamp: float) -> SignalDirection | None:
        minute = int(timestamp // 60)
        if self.candles and minute <= self.candles[-1].minute:
            return None
        if self._current is not None and minute < self._current.minute:
            return None
        if self._current is None:
            self._current = OneMinuteCandle(minute, price, price, price, price, 0)
            self._last_total_volume = total_volume
            return None
        volume_delta = max(0, total_volume - (self._last_total_volume if self._last_total_volume is not None else total_volume))
        self._last_total_volume = total_volume
        if minute == self._current.minute:
            self._current.high = max(self._current.high, price)
            self._current.low = min(self._current.low, price)
            self._current.close = price
            self._current.volume += volume_delta
            return None
        completed = self._current
        self.candles.append(completed)
        self.last_completed_candle = completed
        self.candles = self.candles[-500:]
        self._current = OneMinuteCandle(minute, price, price, price, price, volume_delta)
        return self._short_trigger()

    def load_completed_candles(self, candles: list[OneMinuteCandle], *, allow_initial_signal: bool = False) -> int:
        """Seed the indicator with broker-supplied, already closed one-minute bars."""
        self.candles = candles[-500:]
        self._current = None
        self._last_total_volume = None
        self._initialized = len(self.candles) >= 25 and not allow_initial_signal
        self._regime = self._market_regime() if self._initialized else None
        return len(self.candles)

    def _market_regime(self) -> Literal["bullish", "bearish", "neutral"]:
        if len(self.candles) < 25:
            return "neutral"
        closes = [c.close for c in self.candles]
        session = self._session_candles()
        if len(session) < 3:
            return "neutral"
        volumes = [c.volume for c in session]
        if sum(volumes) <= 0:
            return "neutral"
        vwaps = []
        pv = vol = 0
        for candle in session:
            pv += ((candle.high + candle.low + candle.close) / 3) * candle.volume
            vol += candle.volume
            vwaps.append(pv / vol if vol else candle.close)
        mvwap = self._ema(vwaps[-21:], min(21, len(vwaps)))
        fast = self._ema(closes[-7:], 7)
        slow = self._ema(closes[-25:], 25)
        if len(session) < 25:
            # Opening rule: historical EMAs establish direction; today's price
            # and VWAP must independently confirm it after three closed bars.
            if fast > slow and session[-1].close > vwaps[-1] > mvwap and session[-1].close > session[0].close:
                return "bullish"
            if fast < slow and session[-1].close < vwaps[-1] < mvwap and session[-1].close < session[0].close:
                return "bearish"
            return "neutral"
        if fast > mvwap and slow > mvwap and vwaps[-1] > mvwap:
            return "bullish"
        if fast < mvwap and slow < mvwap and vwaps[-1] < mvwap:
            return "bearish"
        return "neutral"

    def latest_pattern_features(self) -> dict[str, float | int]:
        """Return point-in-time OHLCV features for research/model training.

        Features use only completed candles up to the latest candle, so they can
        later be replayed without accidentally looking ahead.
        """
        if not self.candles:
            return {}
        current = self.candles[-1]
        previous = self.candles[-2] if len(self.candles) > 1 else current
        candle_range = max(current.high - current.low, 0.01)
        body = abs(current.close - current.open)
        upper_wick = current.high - max(current.open, current.close)
        lower_wick = min(current.open, current.close) - current.low
        close_location = (current.close - current.low) / candle_range

        def return_pct(period: int) -> float:
            if len(self.candles) <= period:
                return 0.0
            base = self.candles[-period - 1].close
            return (current.close / base - 1) * 100 if base else 0.0

        recent = self.candles[-25:]
        ranges = [c.high - c.low for c in recent]
        avg_range = sum(ranges[:-1]) / max(1, len(ranges) - 1)
        prior_volumes = [c.volume for c in self.candles[-21:-1]]
        avg_volume = sum(prior_volumes) / len(prior_volumes) if prior_volumes else 0.0
        typical_volume = sum(c.volume for c in recent)
        typical_price_volume = sum(((c.high + c.low + c.close) / 3) * c.volume for c in recent)
        recent_vwap = typical_price_volume / typical_volume if typical_volume else current.close
        closes = [c.close for c in self.candles]
        ema7 = self._ema(closes[-7:], min(7, len(closes)))
        ema25 = self._ema(closes[-25:], min(25, len(closes)))
        is_bull = current.close > current.open
        prev_bear = previous.close < previous.open
        is_bear = current.close < current.open
        prev_bull = previous.close > previous.open
        near_zero_body = body / candle_range <= 0.1

        return {
            "body_pct_range": body / candle_range,
            "upper_wick_pct_range": max(0.0, upper_wick) / candle_range,
            "lower_wick_pct_range": max(0.0, lower_wick) / candle_range,
            "close_location": close_location,
            "return_1m_pct": return_pct(1),
            "return_3m_pct": return_pct(3),
            "return_5m_pct": return_pct(5),
            "return_10m_pct": return_pct(10),
            "range_vs_recent_avg": candle_range / avg_range if avg_range else 0.0,
            "volume_vs_recent_avg": current.volume / avg_volume if avg_volume else 0.0,
            "close_vs_ema7_pct": (current.close / ema7 - 1) * 100 if ema7 else 0.0,
            "close_vs_ema25_pct": (current.close / ema25 - 1) * 100 if ema25 else 0.0,
            "close_vs_vwap_pct": (current.close / recent_vwap - 1) * 100 if recent_vwap else 0.0,
            "bullish_engulfing": int(is_bull and prev_bear and current.open <= previous.close and current.close >= previous.open),
            "bearish_engulfing": int(is_bear and prev_bull and current.open >= previous.close and current.close <= previous.open),
            "hammer": int(lower_wick >= 2 * body and upper_wick <= body and close_location >= 0.65),
            "shooting_star": int(upper_wick >= 2 * body and lower_wick <= body and close_location <= 0.35),
            "doji": int(near_zero_body),
            "inside_bar": int(current.high < previous.high and current.low > previous.low),
        }

    @staticmethod
    def _ema(values: list[float], length: int) -> float:
        value = values[0]
        factor = 2 / (length + 1)
        for item in values[1:]: value += factor * (item - value)
        return value

    def _short_trigger(self) -> SignalDirection | None:
        if len(self.candles) < 25:
            return None
        regime = self._market_regime()
        if not self._initialized:
            # A directional first reading is actionable after warm-up, even
            # when the trend began before the agent had enough closed bars.
            self._regime = regime
            self._initialized = True
            return None if regime == "neutral" else ("long" if regime == "bullish" else "short")
        previous = self._regime
        self._regime = regime
        if regime == previous or regime == "neutral":
            return None
        return "long" if regime == "bullish" else "short"

    def diagnostics(self) -> dict[str, object]:
        """Expose only the signal-readiness state needed by the paper dashboard."""
        completed = len(self.candles)
        features = self.latest_pattern_features()
        pattern_names = [
            name.replace("_", " ")
            for name in ("bullish_engulfing", "bearish_engulfing", "hammer", "shooting_star", "doji", "inside_bar")
            if features.get(name)
        ]
        if completed < 25:
            phase = "warming_up"
        elif len(self._session_candles()) < 3:
            phase = "waiting_session_confirmation"
        elif not self._initialized:
            phase = "establishing_market_baseline"
        else:
            phase = f"market_{self._regime or 'neutral'}"
        return {
            "completed_candles": completed,
            "session_candles": len(self._session_candles()),
            "required_session_candles": 3,
            "required_candles": 25,
            "phase": phase,
            "market_regime": self._regime or "neutral",
            "current_minute": self._current.minute if self._current else None,
            "latest_patterns": pattern_names,
        }
