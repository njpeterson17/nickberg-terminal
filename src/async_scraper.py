"""
Async news scraper module for concurrent RSS feed scraping using aiohttp.

This module provides async versions of the scraper classes from scraper.py,
offering significantly improved performance through concurrent HTTP requests.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

import aiohttp
import feedparser
import yaml
from aiohttp import ClientTimeout, TCPConnector
from bs4 import BeautifulSoup

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from logging_config import get_logger

logger = get_logger(__name__)

# Default timeout for HTTP requests (seconds)
DEFAULT_REQUEST_TIMEOUT = 30


class ArticleData:
    """Article data container - compatible with scraper.py version"""

    def __init__(
        self,
        url: str,
        title: str,
        content: str,
        source: str,
        published_at: Optional[datetime] = None,
    ):
        self.url = url
        self.title = title
        self.content = content
        self.source = source
        self.published_at = published_at or datetime.now()

    def __repr__(self) -> str:
        return f"ArticleData({self.source}: {self.title[:50]}...)"

    def __hash__(self) -> int:
        """Make ArticleData hashable for deduplication"""
        return hash(self.url)

    def __eq__(self, other: object) -> bool:
        """Compare articles by URL for deduplication"""
        if not isinstance(other, ArticleData):
            return NotImplemented
        return self.url == other.url


class AsyncDomainRateLimiter:
    """
    Async per-domain rate limiter to avoid IP bans.
    Ensures minimum delay between requests to the same domain.
    Async-safe implementation using asyncio.Lock.
    """

    def __init__(self, min_delay: float = 2.0):
        """
        Initialize the rate limiter.

        Args:
            min_delay: Minimum seconds between requests to the same domain
        """
        self.min_delay = min_delay
        self._last_request_time: Dict[str, float] = {}
        self._lock = asyncio.Lock()

    def _extract_domain(self, url: str) -> str:
        """Extract domain from URL"""
        parsed = urlparse(url)
        return parsed.netloc.lower()

    async def wait_if_needed(self, url: str) -> float:
        """
        Wait if needed to respect rate limit for the domain.

        Args:
            url: The URL being requested

        Returns:
            The time waited in seconds (0 if no wait was needed)
        """
        domain = self._extract_domain(url)

        async with self._lock:
            current_time = time.time()
            last_time = self._last_request_time.get(domain, 0)
            elapsed = current_time - last_time

            if elapsed < self.min_delay:
                wait_time = self.min_delay - elapsed
                # Release lock while sleeping
                self._last_request_time[domain] = current_time + wait_time
                await asyncio.sleep(wait_time)
                waited = wait_time
            else:
                self._last_request_time[domain] = current_time
                waited = 0.0

        if waited > 0:
            logger.debug(
                "Rate limited", extra={"domain": domain, "wait_seconds": round(waited, 2)}
            )

        return waited

    def get_stats(self) -> Dict[str, Any]:
        """Get rate limiter statistics"""
        return {
            "tracked_domains": len(self._last_request_time),
            "min_delay": self.min_delay,
            "domains": list(self._last_request_time.keys()),
        }


class AsyncFeedHealthTracker:
    """
    Async feed health tracker with exponential backoff.
    Feeds with consecutive failures are temporarily skipped.
    """

    def __init__(
        self,
        max_consecutive_failures: int = 5,
        base_backoff_minutes: int = 15,
    ):
        """
        Initialize the feed health tracker.

        Args:
            max_consecutive_failures: Number of failures before marking feed as potentially dead
            base_backoff_minutes: Base time for exponential backoff in minutes
        """
        self.max_consecutive_failures = max_consecutive_failures
        self.base_backoff_minutes = base_backoff_minutes
        self._feed_status: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def record_success(self, feed_url: str) -> None:
        """Record a successful fetch for a feed"""
        async with self._lock:
            self._feed_status[feed_url] = {
                "consecutive_failures": 0,
                "last_success": datetime.now(),
                "last_attempt": datetime.now(),
                "is_dead": False,
                "next_retry": None,
            }
            logger.debug("Feed success recorded", extra={"feed_url": feed_url})

    async def record_failure(self, feed_url: str) -> bool:
        """
        Record a failed fetch for a feed.

        Args:
            feed_url: The feed URL that failed

        Returns:
            True if the feed is now marked as potentially dead
        """
        async with self._lock:
            if feed_url not in self._feed_status:
                self._feed_status[feed_url] = {
                    "consecutive_failures": 0,
                    "last_success": None,
                    "last_attempt": None,
                    "is_dead": False,
                    "next_retry": None,
                }

            status = self._feed_status[feed_url]
            status["consecutive_failures"] += 1
            status["last_attempt"] = datetime.now()

            if status["consecutive_failures"] >= self.max_consecutive_failures:
                if not status["is_dead"]:
                    logger.warning(
                        "Feed may be dead",
                        extra={
                            "feed_url": feed_url,
                            "consecutive_failures": status["consecutive_failures"],
                        },
                    )
                    status["is_dead"] = True

                # Calculate exponential backoff
                failures_beyond = (
                    status["consecutive_failures"] - self.max_consecutive_failures
                )
                backoff_multiplier = 2 ** min(failures_beyond, 6)  # Cap at 64x
                backoff_minutes = self.base_backoff_minutes * backoff_multiplier
                status["next_retry"] = datetime.now() + timedelta(minutes=backoff_minutes)
                logger.info(
                    "Feed retry scheduled",
                    extra={
                        "feed_url": feed_url,
                        "retry_minutes": backoff_minutes,
                        "attempt": status["consecutive_failures"],
                    },
                )
                return True

            logger.debug(
                "Feed failure recorded",
                extra={
                    "feed_url": feed_url,
                    "failures": status["consecutive_failures"],
                    "max_failures": self.max_consecutive_failures,
                },
            )
            return False

    async def should_skip_feed(self, feed_url: str) -> Tuple[bool, Optional[str]]:
        """
        Check if a feed should be skipped due to being dead.

        Args:
            feed_url: The feed URL to check

        Returns:
            Tuple of (should_skip, reason_message)
        """
        async with self._lock:
            if feed_url not in self._feed_status:
                return False, None

            status = self._feed_status[feed_url]

            if not status["is_dead"]:
                return False, None

            # Check if it's time to retry
            if status["next_retry"] and datetime.now() >= status["next_retry"]:
                logger.info("Retrying previously dead feed", extra={"feed_url": feed_url})
                return False, None

            # Still in backoff period
            if status["next_retry"]:
                time_remaining = status["next_retry"] - datetime.now()
                minutes_remaining = int(time_remaining.total_seconds() / 60)
                reason = (
                    f"Feed marked as dead after {status['consecutive_failures']} failures. "
                    f"Will retry in {minutes_remaining} minutes."
                )
                return True, reason

            return True, f"Feed marked as dead after {status['consecutive_failures']} failures."

    async def get_feed_health(self, feed_url: str) -> Optional[Dict[str, Any]]:
        """Get health status for a specific feed"""
        async with self._lock:
            return self._feed_status.get(feed_url)

    async def get_all_dead_feeds(self) -> List[Dict[str, Any]]:
        """Get a list of all feeds currently marked as dead"""
        async with self._lock:
            return [
                {"url": url, **status}
                for url, status in self._feed_status.items()
                if status.get("is_dead", False)
            ]

    async def get_stats(self) -> Dict[str, Any]:
        """Get overall feed health statistics"""
        async with self._lock:
            total_feeds = len(self._feed_status)
            dead_feeds = sum(
                1 for s in self._feed_status.values() if s.get("is_dead", False)
            )
            return {
                "total_tracked": total_feeds,
                "dead_feeds": dead_feeds,
                "healthy_feeds": total_feeds - dead_feeds,
            }


class AsyncHTTPCache:
    """
    Async HTTP cache manager using ETags and Last-Modified headers.
    Stores cache metadata in a JSON file for persistence between runs.
    """

    def __init__(
        self,
        cache_file: str = "data/http_cache.json",
        enabled: bool = True,
        log_stats: bool = True,
    ):
        self.cache_file = cache_file
        self.enabled = enabled
        self.log_stats = log_stats
        self._cache: Dict[str, Dict[str, str]] = {}
        self._hits = 0
        self._misses = 0
        self._lock = asyncio.Lock()

        if self.enabled:
            self._load_cache()

    def _load_cache(self) -> None:
        """Load cache from file"""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file) as f:
                    self._cache = json.load(f)
                logger.info(
                    "Loaded HTTP cache",
                    extra={"entries": len(self._cache), "cache_file": self.cache_file},
                )
            else:
                logger.info(
                    "No existing HTTP cache file found, starting fresh",
                    extra={"cache_file": self.cache_file},
                )
                self._cache = {}
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Failed to load HTTP cache, starting fresh", extra={"error": str(e)})
            self._cache = {}

    async def _save_cache(self) -> None:
        """Save cache to file"""
        try:
            # Ensure directory exists
            cache_dir = os.path.dirname(self.cache_file)
            if cache_dir:
                Path(cache_dir).mkdir(parents=True, exist_ok=True)

            async with self._lock:
                with open(self.cache_file, "w") as f:
                    json.dump(self._cache, f, indent=2)
                logger.debug("Saved HTTP cache", extra={"entries": len(self._cache)})
        except OSError as e:
            logger.error("Failed to save HTTP cache", extra={"error": str(e)})

    def get_cache_headers(self, url: str) -> Dict[str, str]:
        """Get conditional request headers for a URL"""
        if not self.enabled:
            return {}

        headers = {}
        if url in self._cache:
            entry = self._cache[url]
            if "etag" in entry:
                headers["If-None-Match"] = entry["etag"]
            if "last_modified" in entry:
                headers["If-Modified-Since"] = entry["last_modified"]

        return headers

    async def update_cache(self, url: str, headers: Dict[str, str]) -> None:
        """Update cache with response headers"""
        if not self.enabled:
            return

        async with self._lock:
            entry = self._cache.get(url, {})

            # Store ETag if present
            etag = headers.get("ETag")
            if etag:
                entry["etag"] = etag

            # Store Last-Modified if present
            last_modified = headers.get("Last-Modified")
            if last_modified:
                entry["last_modified"] = last_modified

            # Store last fetched timestamp
            entry["last_fetched"] = datetime.now().isoformat()

            if entry:
                self._cache[url] = entry

        # Save outside the lock to minimize lock time
        await self._save_cache()

    def record_hit(self) -> None:
        """Record a cache hit (304 Not Modified)"""
        self._hits += 1

    def record_miss(self) -> None:
        """Record a cache miss (200 OK or other)"""
        self._misses += 1

    def get_stats(self) -> Tuple[int, int]:
        """Get cache statistics (hits, misses)"""
        return self._hits, self._misses

    def log_statistics(self) -> None:
        """Log cache statistics"""
        if not self.log_stats:
            return

        total = self._hits + self._misses
        if total > 0:
            hit_rate = (self._hits / total) * 100
            logger.info(
                "HTTP Cache stats",
                extra={"hits": self._hits, "misses": self._misses, "hit_rate": round(hit_rate, 1)},
            )
        else:
            logger.info("HTTP Cache stats - No requests made")

    def reset_stats(self) -> None:
        """Reset cache statistics"""
        self._hits = 0
        self._misses = 0

    async def cleanup_old_entries(self, max_age_days: int = 7) -> int:
        """
        Remove cache entries older than the specified number of days.

        Args:
            max_age_days: Maximum age of cache entries in days (default: 7)

        Returns:
            Number of entries removed
        """
        if not self.enabled:
            return 0

        cutoff = datetime.now() - timedelta(days=max_age_days)
        removed_count = 0

        async with self._lock:
            urls_to_remove: List[str] = []

            for url, entry in self._cache.items():
                last_fetched_str = entry.get("last_fetched")
                if last_fetched_str:
                    try:
                        last_fetched = datetime.fromisoformat(last_fetched_str)
                        if last_fetched < cutoff:
                            urls_to_remove.append(url)
                    except (ValueError, TypeError):
                        # Invalid date format, mark for removal
                        urls_to_remove.append(url)
                else:
                    # No last_fetched timestamp, mark for removal
                    urls_to_remove.append(url)

            for url in urls_to_remove:
                del self._cache[url]
                removed_count += 1

        if removed_count > 0:
            await self._save_cache()
            logger.info(
                "HTTP cache cleanup",
                extra={"removed_entries": removed_count, "max_age_days": max_age_days},
            )

        return removed_count


class AsyncBaseScraper(ABC):
    """Base class for async scrapers"""

    def __init__(
        self,
        config: Dict[str, Any],
        global_config: Dict[str, Any],
        session: aiohttp.ClientSession,
        rate_limiter: AsyncDomainRateLimiter,
    ):
        self.config = config
        self.global_config = global_config
        self.session = session
        self.rate_limiter = rate_limiter

    def _get_headers(self) -> Dict[str, str]:
        """Get request headers with random user agent"""
        user_agents = self.global_config.get(
            "user_agents",
            ["Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"],
        )

        return {
            "User-Agent": random.choice(user_agents),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
        }

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """Parse various date formats"""
        formats = [
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%d %H:%M:%S",
            "%a, %d %b %Y %H:%M:%S %Z",
            "%a, %d %b %Y %H:%M:%S %z",
            "%b %d, %Y %H:%M %Z",
            "%b %d, %Y %H:%M %z",
        ]

        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue

        # Try feedparser's date parser
        try:
            parsed = feedparser._parse_date(date_str)
            if parsed:
                return datetime(*parsed[:6])
        except (TypeError, ValueError, AttributeError) as e:
            logger.debug(
                "Feedparser date parsing failed", extra={"date_str": date_str, "error": str(e)}
            )

        logger.warning("Could not parse date", extra={"date_str": date_str})
        return None

    @abstractmethod
    async def scrape(self) -> List[ArticleData]:
        """Scrape articles from this source"""
        pass


class AsyncRSSScraper(AsyncBaseScraper):
    """Async RSS feed scraper"""

    def __init__(
        self,
        config: Dict[str, Any],
        global_config: Dict[str, Any],
        session: aiohttp.ClientSession,
        rate_limiter: AsyncDomainRateLimiter,
        http_cache: Optional[AsyncHTTPCache] = None,
        feed_health_tracker: Optional[AsyncFeedHealthTracker] = None,
    ):
        super().__init__(config, global_config, session, rate_limiter)
        self.http_cache = http_cache
        self.feed_health_tracker = feed_health_tracker

    async def _fetch_feed_with_cache(
        self, feed_url: str
    ) -> Tuple[Optional[Any], Optional[bool]]:
        """
        Fetch RSS feed with HTTP caching support.

        Returns:
            Tuple of (feed, was_modified):
            - If feed returns 304 Not Modified: (None, False)
            - If feed was fetched successfully: (feed_object, True)
            - If fetch failed: (None, None) - None for was_modified indicates failure
        """
        timeout = self.global_config.get("timeout", DEFAULT_REQUEST_TIMEOUT)
        headers = self._get_headers()

        # Add cache headers if available
        if self.http_cache:
            headers.update(self.http_cache.get_cache_headers(feed_url))

        try:
            # Apply rate limiting
            await self.rate_limiter.wait_if_needed(feed_url)

            async with self.session.get(
                feed_url, headers=headers, timeout=ClientTimeout(total=timeout)
            ) as response:
                # Handle 304 Not Modified
                if response.status == 304:
                    logger.debug("Cache HIT (304 Not Modified)", extra={"feed_url": feed_url})
                    if self.http_cache:
                        self.http_cache.record_hit()
                    return None, False

                # Handle other status codes
                if response.status == 404:
                    logger.warning("Feed not found", extra={"feed_url": feed_url, "status_code": 404})
                    if self.feed_health_tracker:
                        await self.feed_health_tracker.record_failure(feed_url)
                    if self.http_cache:
                        self.http_cache.record_miss()
                    return None, None

                if response.status == 403:
                    logger.warning(
                        "Feed access forbidden", extra={"feed_url": feed_url, "status_code": 403}
                    )
                    if self.feed_health_tracker:
                        await self.feed_health_tracker.record_failure(feed_url)
                    if self.http_cache:
                        self.http_cache.record_miss()
                    return None, None

                if response.status == 429:
                    logger.warning(
                        "Rate limited by server",
                        extra={"feed_url": feed_url, "status_code": 429},
                    )
                    if self.feed_health_tracker:
                        await self.feed_health_tracker.record_failure(feed_url)
                    if self.http_cache:
                        self.http_cache.record_miss()
                    return None, None

                response.raise_for_status()

                # Get content and update cache
                content = await response.read()

                if self.http_cache:
                    await self.http_cache.update_cache(feed_url, dict(response.headers))
                    self.http_cache.record_miss()

                # Parse feed
                feed = feedparser.parse(content)
                return feed, True

        except asyncio.TimeoutError:
            logger.warning("Timeout fetching feed", extra={"feed_url": feed_url, "timeout": timeout})
            if self.feed_health_tracker:
                await self.feed_health_tracker.record_failure(feed_url)
            if self.http_cache:
                self.http_cache.record_miss()
        except aiohttp.ClientError as e:
            logger.warning(
                "Client error fetching feed", extra={"feed_url": feed_url, "error": str(e)}
            )
            if self.feed_health_tracker:
                await self.feed_health_tracker.record_failure(feed_url)
            if self.http_cache:
                self.http_cache.record_miss()
        except Exception as e:
            logger.error(
                "Unexpected error fetching feed",
                extra={"feed_url": feed_url, "error": str(e), "error_type": type(e).__name__},
            )
            if self.feed_health_tracker:
                await self.feed_health_tracker.record_failure(feed_url)
            if self.http_cache:
                self.http_cache.record_miss()

        return None, None

    async def scrape(self) -> List[ArticleData]:
        """Scrape RSS feeds"""
        articles: List[ArticleData] = []
        rss_feeds = self.config.get("rss_feeds", [])

        for feed_url in rss_feeds:
            # Check if feed should be skipped
            if self.feed_health_tracker:
                should_skip, reason = await self.feed_health_tracker.should_skip_feed(feed_url)
                if should_skip:
                    logger.info("Skipping dead feed", extra={"feed_url": feed_url, "reason": reason})
                    continue

            logger.info("Fetching RSS feed", extra={"feed_url": feed_url})

            try:
                feed, was_modified = await self._fetch_feed_with_cache(feed_url)

                # Handle fetch result
                if was_modified is None:
                    # Fetch failed
                    continue
                elif was_modified is False:
                    # Feed not modified (304) - success
                    logger.info("Feed not modified, skipping", extra={"feed_url": feed_url})
                    if self.feed_health_tracker:
                        await self.feed_health_tracker.record_success(feed_url)
                    continue
                elif feed is None:
                    logger.warning(
                        "Unexpected state: was_modified=True but feed is None",
                        extra={"feed_url": feed_url},
                    )
                    continue

                # Feed fetched successfully
                if self.feed_health_tracker:
                    await self.feed_health_tracker.record_success(feed_url)

                # Process feed entries
                entries = feed.entries[:50] if hasattr(feed, "entries") else []
                for entry in entries:
                    try:
                        article = await self._parse_entry(entry, feed_url)
                        if article:
                            articles.append(article)
                    except (KeyError, AttributeError) as e:
                        logger.warning(
                            "Error parsing RSS entry",
                            extra={"feed_url": feed_url, "error": str(e)},
                        )
                        continue
                    except Exception as e:
                        logger.error(
                            "Unexpected error parsing RSS entry",
                            extra={
                                "feed_url": feed_url,
                                "error": str(e),
                                "error_type": type(e).__name__,
                            },
                        )
                        continue

            except Exception as e:
                logger.error(
                    "Error processing RSS feed",
                    extra={"feed_url": feed_url, "error": str(e), "error_type": type(e).__name__},
                )
                if self.feed_health_tracker:
                    await self.feed_health_tracker.record_failure(feed_url)
                continue

        logger.info(
            "RSS scraper complete",
            extra={"source": self.config.get("name", "RSS"), "articles": len(articles)},
        )
        return articles

    async def _parse_entry(self, entry: Any, feed_url: str) -> Optional[ArticleData]:
        """Parse a single RSS entry into ArticleData"""
        # Extract article URL
        url = entry.get("link", "")
        if not url:
            return None

        # Check if URL is valid
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return None

        # Extract title
        title = entry.get("title", "").strip()
        if not title:
            return None

        # Extract published date
        published = None
        if hasattr(entry, "published"):
            published = self._parse_date(entry.published)
        elif hasattr(entry, "updated"):
            published = self._parse_date(entry.updated)

        # Get content
        content = ""
        if hasattr(entry, "content"):
            content = entry.content[0].value
        elif hasattr(entry, "summary"):
            content = entry.summary
        elif hasattr(entry, "description"):
            content = entry.description

        # Clean HTML from content
        if content and ("<" in content and ">" in content):
            try:
                content = BeautifulSoup(content, "lxml").get_text()
            except ImportError:
                content = BeautifulSoup(content, "html.parser").get_text()
            except Exception:
                content = BeautifulSoup(content, "html.parser").get_text()

        return ArticleData(
            url=url,
            title=title,
            content=content,
            source=self.config.get("name", "RSS"),
            published_at=published,
        )


class AsyncScraperManager:
    """Async manager for coordinating multiple scrapers with concurrent execution"""

    SCRAPER_MAP = {
        "reuters": AsyncRSSScraper,
        "bloomberg": AsyncRSSScraper,
        "cnbc": AsyncRSSScraper,
    }

    def __init__(self, config_path: str = "config/settings.yaml"):
        self.config = self._load_config(config_path)
        self.session: Optional[aiohttp.ClientSession] = None
        self.rate_limiter: Optional[AsyncDomainRateLimiter] = None
        self.http_cache: Optional[AsyncHTTPCache] = None
        self.feed_health_tracker: Optional[AsyncFeedHealthTracker] = None
        self.scrapers: List[AsyncBaseScraper] = []

    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """Load configuration from YAML file"""
        # Try multiple paths
        paths_to_try = [
            config_path,
            os.path.join(os.path.dirname(__file__), "..", config_path),
            os.path.join(os.path.dirname(__file__), config_path),
            "/home/nick/nickberg-terminal/config/settings.yaml",
        ]

        for path in paths_to_try:
            if os.path.exists(path):
                with open(path) as f:
                    return yaml.safe_load(f)

        raise FileNotFoundError(f"Config file not found: {config_path}")

    async def __aenter__(self) -> AsyncScraperManager:
        """Async context manager entry"""
        await self._initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit"""
        await self._cleanup()

    async def _initialize(self) -> None:
        """Initialize all components"""
        scraping_config = self.config.get("scraping", {})

        # Initialize rate limiter
        rate_limit_config = scraping_config.get("rate_limiting", {})
        min_delay = rate_limit_config.get("per_domain_delay", 2.0)
        self.rate_limiter = AsyncDomainRateLimiter(min_delay=min_delay)

        # Initialize HTTP cache
        cache_config = scraping_config.get("caching", {})
        self.http_cache = AsyncHTTPCache(
            cache_file=cache_config.get("cache_file", "data/http_cache.json"),
            enabled=cache_config.get("enabled", True),
            log_stats=cache_config.get("log_stats", True),
        )

        # Initialize feed health tracker
        health_config = scraping_config.get("feed_health", {})
        self.feed_health_tracker = AsyncFeedHealthTracker(
            max_consecutive_failures=health_config.get("max_consecutive_failures", 5),
            base_backoff_minutes=health_config.get("base_backoff_minutes", 15),
        )

        # Create aiohttp session with connection pooling
        connector = TCPConnector(
            limit=100,  # Total concurrent connections
            limit_per_host=10,  # Per-host connections
            ttl_dns_cache=300,
            use_dns_cache=True,
        )
        timeout = ClientTimeout(total=scraping_config.get("timeout", DEFAULT_REQUEST_TIMEOUT))
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers={"User-Agent": "NickbergTerminal/1.0"},
        )

        # Initialize scrapers
        self._init_scrapers()

        logger.info(
            "AsyncScraperManager initialized",
            extra={
                "rate_limit_delay": min_delay,
                "cache_enabled": cache_config.get("enabled", True),
            },
        )

    def _init_scrapers(self) -> None:
        """Initialize enabled scrapers"""
        global_config = self.config.get("scraping", {})
        sources = self.config.get("sources", {})

        for source_name, source_config in sources.items():
            if not source_config.get("enabled", True):
                logger.info("Skipping disabled source", extra={"source": source_name})
                continue

            scraper_class = self.SCRAPER_MAP.get(source_name, AsyncRSSScraper)

            try:
                scraper = scraper_class(
                    config=source_config,
                    global_config=global_config,
                    session=self.session,
                    rate_limiter=self.rate_limiter,
                    http_cache=self.http_cache,
                    feed_health_tracker=self.feed_health_tracker,
                )
                self.scrapers.append(scraper)
                logger.info("Initialized scraper", extra={"source": source_name})
            except Exception as e:
                logger.error(
                    "Failed to initialize scraper",
                    extra={"source": source_name, "error": str(e)},
                )

    async def _cleanup(self) -> None:
        """Cleanup resources"""
        if self.session:
            await self.session.close()
            self.session = None

    async def _scrape_single(self, scraper: AsyncBaseScraper) -> List[ArticleData]:
        """Scrape from a single scraper with error handling"""
        source_name = scraper.config.get("name", "Unknown")
        try:
            articles = await scraper.scrape()
            logger.info(
                "Scraper completed", extra={"source": source_name, "articles": len(articles)}
            )
            return articles
        except Exception as e:
            logger.error(
                "Scraper failed", extra={"source": source_name, "error": str(e)}
            )
            return []

    async def scrape_all(self, max_concurrent: int = 10) -> List[ArticleData]:
        """
        Run all scrapers concurrently and return combined results.

        Args:
            max_concurrent: Maximum number of scrapers to run concurrently

        Returns:
            List of unique ArticleData objects
        """
        if not self.session:
            raise RuntimeError("ScraperManager not initialized. Use async context manager.")

        all_articles: List[ArticleData] = []

        # Reset cache statistics
        if self.http_cache:
            self.http_cache.reset_stats()

        # Clean up old cache entries
        await self._cleanup_http_cache()

        # Use semaphore to limit concurrent scrapers
        semaphore = asyncio.Semaphore(max_concurrent)

        async def scrape_with_limit(scraper: AsyncBaseScraper) -> List[ArticleData]:
            async with semaphore:
                return await self._scrape_single(scraper)

        # Run all scrapers concurrently
        tasks = [scrape_with_limit(scraper) for scraper in self.scrapers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Scraper task failed with exception: {result}")
                continue
            if isinstance(result, list):
                all_articles.extend(result)

        # Remove duplicates by URL
        seen_urls: Set[str] = set()
        unique_articles: List[ArticleData] = []
        for article in all_articles:
            if article.url not in seen_urls:
                seen_urls.add(article.url)
                unique_articles.append(article)

        # Log statistics
        if self.http_cache:
            self.http_cache.log_statistics()

        if self.feed_health_tracker:
            health_stats = await self.feed_health_tracker.get_stats()
            if health_stats["dead_feeds"] > 0:
                logger.warning(
                    "Feed health issues detected",
                    extra={
                        "dead_feeds": health_stats["dead_feeds"],
                        "total_tracked": health_stats["total_tracked"],
                    },
                )
                dead_feeds = await self.feed_health_tracker.get_all_dead_feeds()
                for feed in dead_feeds:
                    logger.warning(
                        "Dead feed",
                        extra={
                            "feed_url": feed["url"],
                            "consecutive_failures": feed["consecutive_failures"],
                        },
                    )

        logger.info("Scraping complete", extra={"unique_articles": len(unique_articles)})
        return unique_articles

    async def _cleanup_http_cache(self) -> None:
        """Clean up old HTTP cache entries"""
        if not self.http_cache:
            return

        cache_config = self.config.get("scraping", {}).get("caching", {})
        max_age_days = cache_config.get("max_age_days", 7)

        try:
            removed = await self.http_cache.cleanup_old_entries(max_age_days=max_age_days)
            if removed > 0:
                logger.info(
                    "Cleaned up old HTTP cache entries",
                    extra={"removed_entries": removed},
                )
        except Exception as e:
            logger.error("Error during HTTP cache cleanup", extra={"error": str(e)})

    async def get_feed_health_report(self) -> Dict[str, Any]:
        """Get a detailed report of feed health status"""
        if not self.feed_health_tracker:
            return {"error": "Feed health tracking not initialized"}

        return {
            "stats": await self.feed_health_tracker.get_stats(),
            "dead_feeds": await self.feed_health_tracker.get_all_dead_feeds(),
        }


# =============================================================================
# Factory Functions
# =============================================================================


async def scrape_all_sources(config_path: str = "config/settings.yaml") -> List[ArticleData]:
    """
    Async factory function to scrape all sources.

    Args:
        config_path: Path to the configuration file

    Returns:
        List of unique ArticleData objects

    Example:
        articles = await scrape_all_sources()
    """
    async with AsyncScraperManager(config_path) as manager:
        return await manager.scrape_all()


def scrape_all_sync(config_path: str = "config/settings.yaml") -> List[ArticleData]:
    """
    Synchronous wrapper for backwards compatibility.

    Args:
        config_path: Path to the configuration file

    Returns:
        List of unique ArticleData objects

    Example:
        articles = scrape_all_sync()
    """
    return asyncio.run(scrape_all_sources(config_path))


# =============================================================================
# Testing
# =============================================================================


async def main():
    """Test the async scraper"""
    logger.info("Starting async scraper test")
    
    try:
        articles = await scrape_all_sources()
        print(f"\n{'='*60}")
        print(f"Scraped {len(articles)} articles")
        print(f"{'='*60}\n")
        
        # Show first 5 articles
        for i, article in enumerate(articles[:5], 1):
            print(f"{i}. [{article.source}] {article.title[:60]}...")
            print(f"   URL: {article.url[:70]}...")
            if article.published_at:
                print(f"   Published: {article.published_at}")
            print()
            
    except FileNotFoundError as e:
        logger.error(f"Configuration file not found: {e}")
        print(f"\nError: {e}")
        print("Make sure you're running from the project root directory.")
    except Exception as e:
        logger.error(f"Test failed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
