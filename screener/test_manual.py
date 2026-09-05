#!/usr/bin/env python
"""Test with manually crafted data that should definitely trigger a breakout setup."""

try:
    from screener.feature_engine import detect_consolidation_range, breakout_signal, compute_setup
except ModuleNotFoundError:
    from feature_engine import detect_consolidation_range, breakout_signal, compute_setup
import pandas as pd

# Create test data with explicit consolidation -> breakout
dates = pd.date_range(start="2024-01-01", periods=100, freq="D")

# Days 0-79: consolidation at price 100 +/- 0.5
prices_0_79 = [100 + ((i % 3) - 1.5) * 0.3 for i in range(80)]

# Days 80-99: EXPLOSIVE BREAKOUT with 2-point daily move
prices_80_99 = [150 + i * 2.0 for i in range(20)]

# Combine
all_prices = prices_0_79 + prices_80_99
# Volume: low in consolidation, high in breakout (last day even higher for acceleration)
all_vol = [1000 + (i % 50) for i in range(80)] + [5000] * 19 + [18000]  # last day surge

print(f"Consolidation last: {prices_0_79[-1]:.2f}")
print(f"Breakout first: {prices_80_99[0]:.2f}, last: {prices_80_99[-1]:.2f}")
print(f"All prices last 5: {all_prices[-5:]}")
print()

df = pd.DataFrame({
    "Open": all_prices,
    "High": [p + 1 for p in all_prices],
    "Low": [p - 1 for p in all_prices],
    "Close": all_prices,
    "Volume": all_vol,
}, index=dates)

print("Last 5 rows:")
print(df.tail())
print()

consol_info = detect_consolidation_range(df)
print(f"Consolidation: {consol_info}")

breakout = breakout_signal(df)
print(f"Breakout signal: {breakout}")

setup = compute_setup(df)
print(f"Setup: {setup}")
