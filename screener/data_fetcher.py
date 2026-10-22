"""
Data fetcher for real market data from Yahoo Finance.
Supports both Indian and international stock symbols.
"""

import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Optional
import logging
import time

try:
    import yfinance as yf
except ImportError:  # pragma: no cover - depends on optional runtime package
    yf = None

logger = logging.getLogger(__name__)
YAHOO_DOWNLOAD_BATCH_SIZE = 50


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
        self.cache: Dict[str, tuple[float, pd.DataFrame]] = {}
        # Daily histories change slowly; a five-minute cache avoids re-downloading
        # the full 500-name universe if the user tweaks scan settings.
        self.cache_ttl_seconds = 300

    def get_yahoo_symbol(self, nse_symbol: str) -> str:
        """Convert NSE symbol to Yahoo Finance symbol."""
        if nse_symbol.startswith("^") or "=" in nse_symbol or nse_symbol.endswith((".NS", ".BO")):
            return nse_symbol
        return self.NSE_TO_YAHOO.get(nse_symbol, f"{nse_symbol}.NS")

    @staticmethod
    def _normalize_ohlcv(data: pd.DataFrame, symbol: str) -> Optional[pd.DataFrame]:
        if data is None or data.empty:
            return None
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)
        data.columns = [str(col).lower() for col in data.columns]
        required = ["open", "high", "low", "close"]
        if not all(col in data.columns for col in required):
            logger.warning("Missing OHLCV columns for %s", symbol)
            return None
        if "volume" not in data.columns:
            data["volume"] = 0
        result = data[required + ["volume"]].copy()
        result.columns = ["Open", "High", "Low", "Close", "Volume"]
        result = result.dropna(subset=["Open", "High", "Low", "Close"])
        return result if not result.empty else None

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
        cached = self.cache.get(cache_key)
        if self.use_cache and cached and time.monotonic() - cached[0] < self.cache_ttl_seconds:
            logger.debug("Cache hit for %s", symbol)
            return cached[1]

        try:
            if yf is None:
                logger.warning("yfinance is not installed; real market data is unavailable")
                return None

            # Convert NSE symbol to Yahoo Finance format if needed
            yahoo_symbol = self.get_yahoo_symbol(symbol) if not symbol.endswith(".NS") else symbol

            # Calculate date range
            end_date = datetime.now()
            start_date = end_date - timedelta(days=int(days * 1.8) + 10)

            logger.info(f"Fetching {yahoo_symbol} from {start_date.date()} to {end_date.date()}")

            # Fetch data
            data = yf.download(
                yahoo_symbol,
                start=start_date,
                end=end_date,
                interval=interval,
                progress=False,
                auto_adjust=False,
                timeout=20,
            )

            normalized = self._normalize_ohlcv(data, yahoo_symbol)
            if normalized is None:
                logger.warning("No valid OHLCV data returned for %s", yahoo_symbol)
                return None

            # Take last N days
            normalized = normalized.iloc[-days:].copy()

            # Cache the result
            if self.use_cache:
                self.cache[cache_key] = (time.monotonic(), normalized)

            logger.info("Successfully fetched %s rows for %s", len(normalized), yahoo_symbol)
            return normalized

        except Exception as e:
            logger.error(f"Error fetching data for {symbol}: {str(e)}")
            return None

    def _fetch_multiple_sequential(
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


    def fetch_multiple(
        self,
        symbols: list[str],
        days: int = 100,
        interval: str = "1d",
    ) -> Dict[str, pd.DataFrame]:
        """Download multiple real tickers together and keep partial successes."""
        result: Dict[str, pd.DataFrame] = {}
        unresolved: dict[str, str] = {}
        now = time.monotonic()
        for symbol in dict.fromkeys(symbols):
            cache_key = f"{symbol}_{days}_{interval}"
            cached = self.cache.get(cache_key)
            if self.use_cache and cached and now - cached[0] < self.cache_ttl_seconds:
                result[symbol] = cached[1]
            else:
                unresolved[symbol] = self.get_yahoo_symbol(symbol)

        if not unresolved:
            return result
        if yf is None:
            logger.warning("yfinance is not installed; real market data is unavailable")
            return result

        end_date = datetime.now() + timedelta(days=1)
        start_date = end_date - timedelta(days=int(days * 1.8) + 10)
        unresolved_items = list(unresolved.items())
        for offset in range(0, len(unresolved_items), YAHOO_DOWNLOAD_BATCH_SIZE):
            batch = dict(unresolved_items[offset : offset + YAHOO_DOWNLOAD_BATCH_SIZE])
            try:
                downloaded = yf.download(
                    tickers=list(batch.values()),
                    start=start_date,
                    end=end_date,
                    interval=interval,
                    group_by="ticker",
                    auto_adjust=False,
                    progress=False,
                    threads=min(10, len(batch)),
                    timeout=30,
                    multi_level_index=True,
                )
            except Exception as exc:
                logger.error("Yahoo Finance bulk download failed for batch %s-%s: %s", offset + 1, offset + len(batch), exc)
                continue

            if downloaded is None or downloaded.empty:
                logger.warning("Yahoo Finance returned no data for batch %s-%s", offset + 1, offset + len(batch))
                continue

            for symbol, yahoo_symbol in batch.items():
                try:
                    if isinstance(downloaded.columns, pd.MultiIndex):
                        level_zero = {str(value).upper() for value in downloaded.columns.get_level_values(0)}
                        level_one = {str(value).upper() for value in downloaded.columns.get_level_values(1)}
                        if yahoo_symbol.upper() in level_zero:
                            frame = downloaded[yahoo_symbol].copy()
                        elif yahoo_symbol.upper() in level_one:
                            frame = downloaded.xs(yahoo_symbol, axis=1, level=1, drop_level=True).copy()
                        else:
                            logger.warning("Yahoo Finance returned no data for %s", yahoo_symbol)
                            continue
                    else:
                        frame = downloaded.copy()

                    normalized = self._normalize_ohlcv(frame, yahoo_symbol)
                    if normalized is None:
                        continue
                    normalized = normalized.iloc[-days:].copy()
                    result[symbol] = normalized
                    if self.use_cache:
                        self.cache[f"{symbol}_{days}_{interval}"] = (time.monotonic(), normalized)
                except Exception as exc:
                    logger.warning("Could not normalize Yahoo Finance data for %s: %s", yahoo_symbol, exc)

        logger.info("Yahoo Finance returned data for %s of %s requested symbols", len(result), len(symbols))
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
