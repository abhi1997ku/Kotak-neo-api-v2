"""
Data fetcher for real market data from Yahoo Finance.
Supports both Indian and international stock symbols.
"""

import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Optional
import logging

logger = logging.getLogger(__name__)


class DataFetcher:
    """Fetch historical OHLCV data from Yahoo Finance."""

    # Map NSE symbols to Yahoo Finance symbols
    NSE_TO_YAHOO = {
        "RELIANCE": "RELIANCE.NS",
        "TCS": "TCS.NS",
        "INFY": "INFY.NS",
        "HDFCBANK": "HDFCBANK.NS",
        "ICICIBANK": "ICICIBANK.NS",
        "SBIN": "SBIN.NS",
        "HDFC": "HDFC.NS",
        "ASIANPAINT": "ASIANPAINT.NS",
        "BAJAJFINSV": "BAJAJFINSV.NS",
        "ADANIPORTS": "ADANIPORTS.NS",
        "BHARTIARTL": "BHARTIARTL.NS",
        "MARUTI": "MARUTI.NS",
        "NTPC": "NTPC.NS",
        "POWERGRID": "POWERGRID.NS",
        "JSWSTEEL": "JSWSTEEL.NS",
        "HINDALCO": "HINDALCO.NS",
        "GAIL": "GAIL.NS",
        "ULTRACEMCO": "ULTRACEMCO.NS",
        "WIPRO": "WIPRO.NS",
        "LT": "LT.NS",
    }

    def __init__(self, use_cache: bool = True):
        self.use_cache = use_cache
        self.cache: Dict[str, pd.DataFrame] = {}

    def get_yahoo_symbol(self, nse_symbol: str) -> str:
        """Convert NSE symbol to Yahoo Finance symbol."""
        return self.NSE_TO_YAHOO.get(nse_symbol, f"{nse_symbol}.NS")

    def fetch_historical_data(
        self,
        symbol: str,
        days: int = 100,
        interval: str = "1d"
    ) -> Optional[pd.DataFrame]:
        """
        Fetch historical OHLCV data from Yahoo Finance.

        Args:
            symbol: NSE symbol (e.g., 'RELIANCE') or full yahoo symbol
            days: Number of days of history to fetch
            interval: '1d' for daily, '1h' for hourly, '15m' for 15-min, etc.

        Returns:
            DataFrame with OHLCV data or None if fetch failed
        """
        # Check cache first
        cache_key = f"{symbol}_{days}_{interval}"
        if self.use_cache and cache_key in self.cache:
            logger.debug(f"Cache hit for {symbol}")
            return self.cache[cache_key]

        try:
            # Convert NSE symbol to Yahoo Finance format if needed
            yahoo_symbol = self.get_yahoo_symbol(symbol) if not symbol.endswith(".NS") else symbol

            # Calculate date range
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days + 10)  # +10 for buffer

            logger.info(f"Fetching {yahoo_symbol} from {start_date.date()} to {end_date.date()}")

            # Fetch data
            data = yf.download(
                yahoo_symbol,
                start=start_date,
                end=end_date,
                interval=interval,
                progress=False,
            )

            if data.empty:
                logger.warning(f"No data returned for {yahoo_symbol}")
                return None

            # Handle MultiIndex columns (yfinance returns (col, ticker) tuples)
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = data.columns.get_level_values(0)

            # Rename columns to match screener expectations
            data.columns = [col.lower() for col in data.columns]

            # Ensure required columns exist
            required_cols = ["open", "high", "low", "close", "volume"]
            if not all(col in data.columns for col in required_cols):
                logger.error(f"Missing required columns in data for {yahoo_symbol}")
                return None

            # Standardize column names to uppercase for consistency
            data = data[["open", "high", "low", "close", "volume"]].copy()
            data.columns = ["Open", "High", "Low", "Close", "Volume"]

            # Take last N days
            data = data.iloc[-days:].copy()

            # Cache the result
            if self.use_cache:
                self.cache[cache_key] = data

            logger.info(f"Successfully fetched {len(data)} rows for {yahoo_symbol}")
            return data

        except Exception as e:
            logger.error(f"Error fetching data for {symbol}: {str(e)}")
            return None

    def fetch_multiple(
        self,
        symbols: list[str],
        days: int = 100,
        interval: str = "1d"
    ) -> Dict[str, pd.DataFrame]:
        """
        Fetch data for multiple symbols.

        Args:
            symbols: List of NSE symbols
            days: Number of days of history
            interval: Time interval

        Returns:
            Dictionary mapping symbol to DataFrame
        """
        result = {}
        for symbol in symbols:
            try:
                data = self.fetch_historical_data(symbol, days=days, interval=interval)
                if data is not None and not data.empty:
                    result[symbol] = data
                    logger.info(f"✓ Fetched {symbol}")
                else:
                    logger.warning(f"✗ Failed to fetch {symbol}")
            except Exception as e:
                logger.error(f"Error processing {symbol}: {str(e)}")

        return result


# Global instance
_fetcher = DataFetcher()


def get_fetcher() -> DataFetcher:
    """Get the global data fetcher instance."""
    return _fetcher


def fetch_data(symbol: str, days: int = 100) -> Optional[pd.DataFrame]:
    """Convenience function to fetch data for a single symbol."""
    return _fetcher.fetch_historical_data(symbol, days=days)


def fetch_data_batch(symbols: list[str], days: int = 100) -> Dict[str, pd.DataFrame]:
    """Convenience function to fetch data for multiple symbols."""
    return _fetcher.fetch_multiple(symbols, days=days)


if __name__ == "__main__":
    # Test fetching real data
    logging.basicConfig(level=logging.INFO)

    symbols = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK"]
    print(f"Fetching real data for {len(symbols)} symbols...")

    data_dict = fetch_data_batch(symbols, days=100)

    print(f"\nSuccessfully fetched data for {len(data_dict)} symbols:")
    for symbol, df in data_dict.items():
        print(f"  {symbol}: {len(df)} bars (latest close: ₹{df['Close'].iloc[-1]:.2f})")
