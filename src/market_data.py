"""
Market data provider for fetching stock prices and calculating price changes.

Uses yfinance library to get stock data. Includes caching to minimize API calls.
"""

import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from dataclasses import dataclass

from logging_config import get_logger

logger = get_logger(__name__)

# Try to import yfinance, gracefully handle if not installed
try:
    import yfinance as yf

    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False
    logger.warning("yfinance not installed. Market data features will be disabled.")


@dataclass
class PriceData:
    """Container for price data."""

    ticker: str
    price: float
    timestamp: datetime
    change_pct: Optional[float] = None
    volume: Optional[int] = None


@dataclass
class CacheEntry:
    """Cache entry with timestamp for TTL."""

    data: Any
    created_at: float


class MarketDataProvider:
    """
    Fetch stock market data using yfinance.

    Includes caching with configurable TTL to avoid repeated API calls.
    All methods handle errors gracefully - market data should enhance but not break
    core functionality.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize the market data provider.

        Args:
            config: Optional configuration dict with keys:
                - enabled: bool (default True)
                - cache_ttl_minutes: int (default 15)
        """
        self.config = config or {}
        self.enabled = self.config.get("enabled", True) and YFINANCE_AVAILABLE
        self.cache_ttl_seconds = self.config.get("cache_ttl_minutes", 15) * 60

        # Simple in-memory cache: key -> CacheEntry
        self._cache: Dict[str, CacheEntry] = {}

        if not YFINANCE_AVAILABLE:
            logger.warning("MarketDataProvider initialized but yfinance not available")

    def _get_cached(self, key: str) -> Optional[Any]:
        """Get value from cache if not expired."""
        if key in self._cache:
            entry = self._cache[key]
            if time.time() - entry.created_at < self.cache_ttl_seconds:
                return entry.data
            else:
                # Expired, remove it
                del self._cache[key]
        return None

    def _set_cached(self, key: str, data: Any) -> None:
        """Store value in cache."""
        self._cache[key] = CacheEntry(data=data, created_at=time.time())

    def _clean_cache(self) -> None:
        """Remove expired cache entries."""
        now = time.time()
        expired = [
            key
            for key, entry in self._cache.items()
            if now - entry.created_at >= self.cache_ttl_seconds
        ]
        for key in expired:
            del self._cache[key]

    def get_price(self, ticker: str, date: Optional[datetime] = None) -> Optional[float]:
        """
        Get closing price for a ticker on a specific date.

        Args:
            ticker: Stock ticker symbol (e.g., 'AAPL')
            date: Date to get price for (defaults to most recent trading day)

        Returns:
            Closing price or None if not available
        """
        if not self.enabled:
            return None

        try:
            cache_key = f"price:{ticker}:{date.isoformat() if date else 'latest'}"
            cached = self._get_cached(cache_key)
            if cached is not None:
                return cached

            stock = yf.Ticker(ticker)

            if date is None:
                # Get the most recent price
                hist = stock.history(period="1d")
                if hist.empty:
                    # Try 5 days in case market was closed
                    hist = stock.history(period="5d")

                if not hist.empty:
                    price = float(hist["Close"].iloc[-1])
                    self._set_cached(cache_key, price)
                    return price
            else:
                # Get price for specific date
                end_date = date + timedelta(days=1)
                hist = stock.history(start=date, end=end_date)

                if not hist.empty:
                    price = float(hist["Close"].iloc[0])
                    self._set_cached(cache_key, price)
                    return price

            logger.debug(f"No price data available for {ticker}")
            return None

        except Exception as e:
            logger.warning(f"Failed to get price for {ticker}: {e}")
            return None

    def get_price_change(
        self, ticker: str, start: datetime, end: Optional[datetime] = None
    ) -> Optional[float]:
        """
        Calculate percentage price change over a period.

        Args:
            ticker: Stock ticker symbol
            start: Start date
            end: End date (defaults to now)

        Returns:
            Percentage change (e.g., 5.2 for +5.2%) or None if not available
        """
        if not self.enabled:
            return None

        try:
            end = end or datetime.now()
            cache_key = f"change:{ticker}:{start.date()}:{end.date()}"
            cached = self._get_cached(cache_key)
            if cached is not None:
                return cached

            stock = yf.Ticker(ticker)
            hist = stock.history(start=start, end=end + timedelta(days=1))

            if len(hist) >= 2:
                start_price = float(hist["Close"].iloc[0])
                end_price = float(hist["Close"].iloc[-1])

                if start_price > 0:
                    change_pct = ((end_price - start_price) / start_price) * 100
                    self._set_cached(cache_key, round(change_pct, 2))
                    return round(change_pct, 2)

            logger.debug(f"Insufficient data for price change calculation for {ticker}")
            return None

        except Exception as e:
            logger.warning(f"Failed to get price change for {ticker}: {e}")
            return None

    def get_intraday_change(self, ticker: str) -> Optional[float]:
        """
        Get today's price change so far.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Percentage change from open to current price, or None if not available
        """
        if not self.enabled:
            return None

        try:
            cache_key = f"intraday:{ticker}:{datetime.now().date()}"
            cached = self._get_cached(cache_key)
            if cached is not None:
                return cached

            stock = yf.Ticker(ticker)

            # Get today's data with 1-minute interval for intraday
            hist = stock.history(period="1d", interval="1m")

            if not hist.empty:
                open_price = float(hist["Open"].iloc[0])
                current_price = float(hist["Close"].iloc[-1])

                if open_price > 0:
                    change_pct = ((current_price - open_price) / open_price) * 100
                    # Use shorter cache TTL for intraday data (5 minutes)
                    self._cache[cache_key] = CacheEntry(
                        data=round(change_pct, 2), created_at=time.time()
                    )
                    return round(change_pct, 2)

            # Fallback: compare to previous close
            hist_daily = stock.history(period="2d")
            if len(hist_daily) >= 2:
                prev_close = float(hist_daily["Close"].iloc[-2])
                current = float(hist_daily["Close"].iloc[-1])

                if prev_close > 0:
                    change_pct = ((current - prev_close) / prev_close) * 100
                    self._set_cached(cache_key, round(change_pct, 2))
                    return round(change_pct, 2)

            logger.debug(f"No intraday data available for {ticker}")
            return None

        except Exception as e:
            logger.warning(f"Failed to get intraday change for {ticker}: {e}")
            return None

    def get_historical_prices(self, ticker: str, days: int = 30) -> Optional[Dict[str, float]]:
        """
        Get price history for a ticker.

        Args:
            ticker: Stock ticker symbol
            days: Number of days of history to fetch

        Returns:
            Dict mapping date strings (YYYY-MM-DD) to closing prices,
            or None if not available
        """
        if not self.enabled:
            return None

        try:
            cache_key = f"history:{ticker}:{days}"
            cached = self._get_cached(cache_key)
            if cached is not None:
                return cached

            stock = yf.Ticker(ticker)
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days)

            hist = stock.history(start=start_date, end=end_date)

            if not hist.empty:
                prices = {
                    date.strftime("%Y-%m-%d"): round(float(row["Close"]), 2)
                    for date, row in hist.iterrows()
                }
                self._set_cached(cache_key, prices)
                return prices

            logger.debug(f"No historical data available for {ticker}")
            return None

        except Exception as e:
            logger.warning(f"Failed to get historical prices for {ticker}: {e}")
            return None

    def get_market_context(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Get comprehensive market context for a ticker.

        This is a convenience method that aggregates multiple data points
        for use in alert enrichment.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Dict with current_price, day_change_pct, week_change_pct,
            or None if not available
        """
        if not self.enabled:
            return None

        try:
            current_price = self.get_price(ticker)
            if current_price is None:
                return None

            day_change = self.get_intraday_change(ticker)

            # Calculate week change
            week_ago = datetime.now() - timedelta(days=7)
            week_change = self.get_price_change(ticker, week_ago)

            return {
                "current_price": round(current_price, 2),
                "day_change_pct": day_change,
                "week_change_pct": week_change,
                "timestamp": datetime.now().isoformat(),
            }

        except Exception as e:
            logger.warning(f"Failed to get market context for {ticker}: {e}")
            return None

    def is_significant_move(
        self, ticker: str, threshold_pct: float = 2.0, days: int = 1
    ) -> Optional[bool]:
        """
        Check if ticker has made a significant price move.

        Args:
            ticker: Stock ticker symbol
            threshold_pct: Percentage threshold for significance (default 2%)
            days: Number of days to look back

        Returns:
            True if move exceeds threshold, False if not, None if data unavailable
        """
        if not self.enabled:
            return None

        try:
            if days <= 1:
                change = self.get_intraday_change(ticker)
            else:
                start = datetime.now() - timedelta(days=days)
                change = self.get_price_change(ticker, start)

            if change is not None:
                return abs(change) >= threshold_pct

            return None

        except Exception as e:
            logger.warning(f"Failed to check significant move for {ticker}: {e}")
            return None
