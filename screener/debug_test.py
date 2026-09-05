#!/usr/bin/env python
"""Debug screener detection logic."""

try:
    from screener.live_scan import synthetic_universe
    from screener.feature_engine import detect_consolidation_range, breakout_signal, compute_setup
except ModuleNotFoundError:
    from live_scan import synthetic_universe
    from feature_engine import detect_consolidation_range, breakout_signal, compute_setup

# Get synthetic data for first symbol
universe = synthetic_universe()
symbol = list(universe.keys())[0]
df = universe[symbol]

print("Last 5 rows of data:")
print(df.tail())
print()

# Test consolidation detection
consol_info = detect_consolidation_range(df)
print(f"Consolidation detection: {consol_info}")
print()

# Test breakout signal
breakout = breakout_signal(df)
print(f"Breakout signal: {breakout}")
print()

# Check last close vs range
last_close = float(df["Close"].iloc[-1])
range_high = consol_info["range_high"]
print(f"Last close: {last_close}, Range high: {range_high}")
print(f"Close > Range High? {last_close > range_high}")
print()

# Check volume
last_vol = float(df["Volume"].iloc[-1])
avg_vol = float(df["Volume"].iloc[-20:].mean())
print(f"Last volume: {last_vol}, 20-day avg: {avg_vol}")
print(f"Volume >= 1.2x avg? {last_vol >= (avg_vol * 1.2)}")
print()

# Test full setup
setup = compute_setup(df)
print(f"Setup result: {setup}")
