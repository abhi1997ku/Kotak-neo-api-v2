from dataclasses import dataclass


@dataclass
class ScreenerConfig:
    account_risk_pct: float = 0.5
    min_rr: float = 2.0
    universe_source: str = "nse500"
    atr_multiplier: float = 1.5
    volume_confirmation_multiplier: float = 1.2
    consolidation_window: int = 20


DEFAULT_CONFIG = ScreenerConfig()
