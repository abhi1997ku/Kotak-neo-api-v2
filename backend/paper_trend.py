"""Versioned paper experiment. Parameters are fixed research defaults, not fitted returns."""
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.paper_trading import BankNiftySignalEngine, PaperShortOptionRules

STRATEGY_VERSION = "opening-trend-v3.2"


class OpeningTrendEngine(BankNiftySignalEngine):
    """Three closed session candles suffice; yesterday's prices are optional."""
    def __init__(self, *, allow_late_start=False):
        super().__init__()
        self.allow_late_start = allow_late_start

    def _analysis_bars(self):
        bars = self._session_candles()
        if not bars:
            return bars, False
        late = datetime.fromtimestamp(bars[0].minute*60, ZoneInfo("Asia/Kolkata")).strftime("%H:%M") != "09:15"
        if late and self.allow_late_start:
            # The first observed minute may be partial. Never call its open the session open.
            bars = bars[1:]
            # A feed gap starts a new observed-data anchor; require fresh confirmation.
            for i in range(len(bars)-1, 0, -1):
                if bars[i].minute != bars[i-1].minute+1:
                    bars = bars[i:]
                    break
        return bars, late

    def load_completed_candles(self, candles, *, allow_initial_signal=False):
        count = super().load_completed_candles(candles, allow_initial_signal=allow_initial_signal)
        self._initialized = len(self._session_candles()) >= 3 and not allow_initial_signal
        self._regime = self._market_regime() if self._initialized else None
        return count

    def _market_regime(self):
        bars, late = self._analysis_bars()
        if len(bars) < 3 or any(b.minute != a.minute+1 for a, b in zip(bars[-3:], bars[-2:])):
            return "neutral"
        if late and not self.allow_late_start:
            return "neutral"  # Missing the actual open is not an opening setup.
        volume = sum(c.volume for c in bars)
        if volume <= 0:
            return "neutral"
        pv = vol = 0
        vwaps = []
        for c in bars:
            pv += (c.high+c.low+c.close)/3*c.volume
            vol += c.volume
            vwaps.append(pv/vol if vol else c.close)
        vwap = vwaps[-1]
        closes = [c.close for c in bars]
        recent = bars[-10:]
        travel = abs(recent[0].close-recent[0].open) + sum(abs(b.close-a.close) for a,b in zip(recent,recent[1:]))
        efficiency = abs(recent[-1].close-recent[0].open)/travel if travel else 0
        if efficiency < 0.55:
            return "neutral"
        fast = self._ema(closes, 5)
        slow = self._ema(closes, 13)
        previous_fast = self._ema(closes[:-1], 5)
        last = bars[-1]
        avg_range = sum(c.high-c.low for c in recent)/len(recent)
        if avg_range <= 0 or abs(last.close-vwap) > 2*avg_range:
            return "neutral"  # Do not initiate when already stretched from VWAP.
        # Two consecutive closes on the same side of VWAP and the opening price.
        if all(c.close > v and c.close > bars[0].open for c,v in zip(bars[-2:],vwaps[-2:])) and fast > slow and fast > previous_fast:
            return "bullish"
        if all(c.close < v and c.close < bars[0].open for c,v in zip(bars[-2:],vwaps[-2:])) and fast < slow and fast < previous_fast:
            return "bearish"
        return "neutral"

    def _short_trigger(self):
        if len(self._session_candles()) < 3:
            return None
        regime = self._market_regime()
        previous = self._regime
        self._regime = regime
        self._initialized = True
        if regime == "neutral" or regime == previous:
            return None
        return "long" if regime == "bullish" else "short"

    def diagnostics(self):
        result = super().diagnostics()
        bars = self._session_candles()
        result.update(strategy_version=STRATEGY_VERSION, required_candles=3,
                      completed_candles=len(bars), required_session_candles=3)
        result["phase"] = "warming_up" if len(bars)<3 else f"market_{self._regime or 'neutral'}"
        if bars and datetime.fromtimestamp(bars[0].minute*60, ZoneInfo("Asia/Kolkata")).strftime("%H:%M") != "09:15":
            result["phase"] = "missing_opening_history"
        analysis, late = self._analysis_bars()
        result.update(readiness_mode="observed_session" if late and self.allow_late_start else "session_open",
                      analysis_candles=len(analysis),
                      anchor_minute=analysis[0].minute if analysis else None)
        if late and self.allow_late_start:
            result["phase"] = "late_start_warming_up" if len(analysis)<3 else f"market_{self._regime or 'neutral'}"
        return result


class TrendRunnerRules(PaperShortOptionRules):
    """Keep the initial risk; allow a continuous runner instead of a fixed target."""
    def open_trade(self, *args, **kwargs):
        trade = super().open_trade(*args, **kwargs)
        trade.target_price = None
        trade.peak_premium = trade.entry_price
        return trade

    def on_tick(self, trade, premium):
        if trade.status != "open":
            return trade
        if premium <= 0:
            raise ValueError("Option premium must be positive")
        trade.peak_premium = max(trade.peak_premium or trade.entry_price, premium)
        gain = trade.peak_premium-trade.entry_price
        if gain >= 30:
            trade.stop_price = max(trade.stop_price, trade.entry_price+5)
        if gain >= 60:
            trade.stop_price = max(trade.stop_price, trade.peak_premium-30)
        if premium <= trade.stop_price:
            return self._close(trade, premium, "trailing_stop" if trade.stop_price > trade.entry_price else "stop_loss")
        return trade
