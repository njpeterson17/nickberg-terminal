"""
News scraper module for Reuters, Bloomberg, and other sources
"""

import requests
from requests.exceptions import (
    RequestException,
    Timeout,
    ConnectionError,
    HTTPError,
    TooManyRedirects,
)
import feedparser
import time
import random
import json
import os
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Tuple
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
import yaml
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from pathlib import Path

from logging_config import get_logger

# Default timeout for HTTP requests (seconds)
DEFAULT_REQUEST_TIMEOUT = 30

# Thread-local storage for sessions
_thread_local = threading.local()

logger = get_logger(__name__)


class DomainRateLimiter:
    """
    Per-domain rate limiter to avoid IP bans.
    Ensures minimum delay between requests to the same domain.
    Thread-safe implementation.
    """

    def __init__(self, min_delay: float = 2.0):
        """
        Initialize the rate limiter.

        Args:
            min_delay: Minimum seconds between requests to the same domain
        """
        self.min_delay = min_delay
        self._last_request_time: dict[str, float] = {}
        self._lock = threading.Lock()

    def _extract_domain(self, url: str) -> str:
        """Extract domain from URL"""
        parsed = urlparse(url)
        return parsed.netloc.lower()

    def wait_if_needed(self, url: str) -> float:
        """
        Wait if needed to respect rate limit for the domain.

        Args:
            url: The URL being requested

        Returns:
            The time waited in seconds (0 if no wait was needed)
        """
        domain = self._extract_domain(url)
        waited = 0.0

        with self._lock:
            current_time = time.time()
            last_time = self._last_request_time.get(domain, 0)
            elapsed = current_time - last_time

            if elapsed < self.min_delay:
                wait_time = self.min_delay - elapsed
                # Release lock while sleeping to allow other domains to proceed
                self._lock.release()
                try:
                    time.sleep(wait_time)
                    waited = wait_time
                finally:
                    self._lock.acquire()

            # Update last request time
            self._last_request_time[domain] = time.time()

        if waited > 0:
            logger.debug("Rate limited", extra={"domain": domain, "wait_seconds": round(waited, 2)})

        return waited

    def get_stats(self) -> dict[str, Any]:
        """Get rate limiter statistics"""
        with self._lock:
            return {
                "tracked_domains": len(self._last_request_time),
                "min_delay": self.min_delay,
                "domains": list(self._last_request_time.keys()),
            }


# Global rate limiter instance (will be initialized by ScraperManager)
_domain_rate_limiter: DomainRateLimiter | None = None


def get_domain_rate_limiter() -> DomainRateLimiter | None:
    """Get the global domain rate limiter instance"""
    return _domain_rate_limiter


def init_domain_rate_limiter(config: dict[str, Any]) -> DomainRateLimiter:
    """Initialize the global domain rate limiter from config"""
    global _domain_rate_limiter

    rate_limit_config = config.get("rate_limiting", {})
    min_delay = rate_limit_config.get("per_domain_delay", 2.0)

    _domain_rate_limiter = DomainRateLimiter(min_delay=min_delay)
    logger.info("Domain rate limiter initialized", extra={"min_delay_seconds": min_delay})

    return _domain_rate_limiter


class FeedHealthTracker:
    """
    Tracks feed health and implements dead feed detection with exponential backoff.
    Feeds with consecutive failures are temporarily skipped.
    """

    def __init__(self, max_consecutive_failures: int = 5, base_backoff_minutes: int = 15):
        """
        Initialize the feed health tracker.

        Args:
            max_consecutive_failures: Number of failures before marking feed as potentially dead
            base_backoff_minutes: Base time for exponential backoff in minutes
        """
        self.max_consecutive_failures = max_consecutive_failures
        self.base_backoff_minutes = base_backoff_minutes
        self._feed_status: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def record_success(self, feed_url: str):
        """Record a successful fetch for a feed"""
        with self._lock:
            self._feed_status[feed_url] = {
                "consecutive_failures": 0,
                "last_success": datetime.now(),
                "last_attempt": datetime.now(),
                "is_dead": False,
                "next_retry": None,
            }
            logger.debug("Feed success recorded", extra={"feed_url": feed_url})

    def record_failure(self, feed_url: str) -> bool:
        """
        Record a failed fetch for a feed.

        Args:
            feed_url: The feed URL that failed

        Returns:
            True if the feed is now marked as potentially dead
        """
        with self._lock:
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
                # Backoff doubles with each failure beyond the threshold
                failures_beyond = status["consecutive_failures"] - self.max_consecutive_failures
                backoff_multiplier = 2 ** min(failures_beyond, 6)  # Cap at 64x to prevent overflow
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

    def should_skip_feed(self, feed_url: str) -> tuple[bool, str | None]:
        """
        Check if a feed should be skipped due to being dead.

        Args:
            feed_url: The feed URL to check

        Returns:
            Tuple of (should_skip, reason_message)
        """
        with self._lock:
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

    def get_feed_health(self, feed_url: str) -> dict[str, Any] | None:
        """Get health status for a specific feed"""
        with self._lock:
            return self._feed_status.get(feed_url)

    def get_all_dead_feeds(self) -> list[dict[str, Any]]:
        """Get a list of all feeds currently marked as dead"""
        with self._lock:
            return [
                {"url": url, **status}
                for url, status in self._feed_status.items()
                if status.get("is_dead", False)
            ]

    def get_stats(self) -> dict[str, Any]:
        """Get overall feed health statistics"""
        with self._lock:
            total_feeds = len(self._feed_status)
            dead_feeds = sum(1 for s in self._feed_status.values() if s.get("is_dead", False))
            return {
                "total_tracked": total_feeds,
                "dead_feeds": dead_feeds,
                "healthy_feeds": total_feeds - dead_feeds,
            }


# Global feed health tracker instance
_feed_health_tracker: FeedHealthTracker | None = None


def get_feed_health_tracker() -> FeedHealthTracker | None:
    """Get the global feed health tracker instance"""
    return _feed_health_tracker


def init_feed_health_tracker(config: dict[str, Any]) -> FeedHealthTracker:
    """Initialize the global feed health tracker from config"""
    global _feed_health_tracker

    health_config = config.get("feed_health", {})
    max_failures = health_config.get("max_consecutive_failures", 5)
    base_backoff = health_config.get("base_backoff_minutes", 15)

    _feed_health_tracker = FeedHealthTracker(
        max_consecutive_failures=max_failures, base_backoff_minutes=base_backoff
    )
    logger.info(
        f"Feed health tracker initialized (max failures: {max_failures}, "
        f"base backoff: {base_backoff} minutes)"
    )

    return _feed_health_tracker


class HTTPCache:
    """
    HTTP cache manager using ETags and Last-Modified headers.
    Stores cache metadata in a JSON file for persistence between runs.
    """

    def __init__(
        self, cache_file: str = "data/http_cache.json", enabled: bool = True, log_stats: bool = True
    ):
        self.cache_file = cache_file
        self.enabled = enabled
        self.log_stats = log_stats
        self._cache: dict[str, dict[str, str]] = {}
        self._lock = threading.Lock()

        # Statistics
        self._hits = 0
        self._misses = 0

        if self.enabled:
            self._load_cache()

    def _load_cache(self):
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

    def _save_cache(self):
        """Save cache to file"""
        try:
            # Ensure directory exists
            cache_dir = os.path.dirname(self.cache_file)
            if cache_dir:
                Path(cache_dir).mkdir(parents=True, exist_ok=True)

            with open(self.cache_file, "w") as f:
                json.dump(self._cache, f, indent=2)
            logger.debug("Saved HTTP cache", extra={"entries": len(self._cache)})
        except OSError as e:
            logger.error("Failed to save HTTP cache", extra={"error": str(e)})

    def get_cache_headers(self, url: str) -> dict[str, str]:
        """Get conditional request headers for a URL"""
        if not self.enabled:
            return {}

        headers = {}
        with self._lock:
            if url in self._cache:
                entry = self._cache[url]
                if "etag" in entry:
                    headers["If-None-Match"] = entry["etag"]
                if "last_modified" in entry:
                    headers["If-Modified-Since"] = entry["last_modified"]

        return headers

    def update_cache(self, url: str, response: requests.Response):
        """Update cache with response headers"""
        if not self.enabled:
            return

        with self._lock:
            entry = self._cache.get(url, {})

            # Store ETag if present
            etag = response.headers.get("ETag")
            if etag:
                entry["etag"] = etag

            # Store Last-Modified if present
            last_modified = response.headers.get("Last-Modified")
            if last_modified:
                entry["last_modified"] = last_modified

            # Store last fetched timestamp
            entry["last_fetched"] = datetime.now().isoformat()

            if entry:
                self._cache[url] = entry
                self._save_cache()

    def record_hit(self):
        """Record a cache hit (304 Not Modified)"""
        with self._lock:
            self._hits += 1

    def record_miss(self):
        """Record a cache miss (200 OK or other)"""
        with self._lock:
            self._misses += 1

    def get_stats(self) -> tuple[int, int]:
        """Get cache statistics (hits, misses)"""
        with self._lock:
            return self._hits, self._misses

    def log_statistics(self):
        """Log cache statistics"""
        if not self.log_stats:
            return

        hits, misses = self.get_stats()
        total = hits + misses
        if total > 0:
            hit_rate = (hits / total) * 100
            logger.info(
                "HTTP Cache stats",
                extra={"hits": hits, "misses": misses, "hit_rate": round(hit_rate, 1)},
            )
        else:
            logger.info("HTTP Cache stats - No requests made")

    def reset_stats(self):
        """Reset cache statistics"""
        with self._lock:
            self._hits = 0
            self._misses = 0

    def cleanup_old_entries(self, max_age_days: int = 7) -> int:
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

        with self._lock:
            urls_to_remove = []

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
                self._save_cache()
                logger.info(
                    "HTTP cache cleanup",
                    extra={"removed_entries": removed_count, "max_age_days": max_age_days},
                )

        return removed_count


# Global cache instance (will be initialized by ScraperManager)
_http_cache: HTTPCache | None = None


def get_http_cache() -> HTTPCache | None:
    """Get the global HTTP cache instance"""
    return _http_cache


def init_http_cache(config: dict[str, Any]) -> HTTPCache:
    """Initialize the global HTTP cache from config"""
    global _http_cache

    cache_config = config.get("caching", {})
    enabled = cache_config.get("enabled", True)
    cache_file = cache_config.get("cache_file", "data/http_cache.json")
    log_stats = cache_config.get("log_stats", True)

    _http_cache = HTTPCache(cache_file=cache_file, enabled=enabled, log_stats=log_stats)

    logger.info("HTTP caching configured", extra={"enabled": enabled})
    return _http_cache


class ArticleData:
    def __init__(
        self, url: str, title: str, content: str, source: str, published_at: datetime | None = None
    ):
        self.url = url
        self.title = title
        self.content = content
        self.source = source
        self.published_at = published_at or datetime.now()

    def __repr__(self):
        return f"ArticleData({self.source}: {self.title[:50]}...)"


class BaseScraper(ABC):
    def __init__(self, config: dict[str, Any], global_config: dict[str, Any]):
        self.config = config
        self.global_config = global_config
        self.session = requests.Session()
        self._setup_session()

    def _setup_session(self):
        """Setup requests session with headers and settings"""
        user_agents = self.global_config.get(
            "user_agents", ["Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"]
        )

        self.session.headers.update(
            {
                "User-Agent": random.choice(user_agents),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": "1",
                "Connection": "keep-alive",
            }
        )

    def _get_delay(self) -> float:
        """Get random delay between requests"""
        delay_min = self.global_config.get("delay_min", 2)
        delay_max = self.global_config.get("delay_max", 5)
        return random.uniform(delay_min, delay_max)

    def _sleep(self):
        """Sleep for random delay"""
        time.sleep(self._get_delay())

    def _fetch(self, url: str, retries: int = None) -> str | None:
        """Fetch URL content with retries and exponential backoff.

        Args:
            url: The URL to fetch
            retries: Number of retry attempts (default from config or 3)

        Returns:
            Response text on success, None on failure
        """
        if retries is None:
            retries = self.global_config.get("max_retries", 3)

        timeout = self.global_config.get("timeout", DEFAULT_REQUEST_TIMEOUT)

        for attempt in range(retries):
            try:
                logger.debug("Fetching URL", extra={"url": url})
                response = self.session.get(url, timeout=timeout)
                response.raise_for_status()
                return response.text

            except Timeout:
                logger.warning(
                    "Request timed out",
                    extra={
                        "url": url,
                        "attempt": attempt + 1,
                        "max_attempts": retries,
                        "timeout": timeout,
                    },
                )
            except ConnectionError as e:
                logger.warning(
                    "Connection failed",
                    extra={
                        "url": url,
                        "attempt": attempt + 1,
                        "max_attempts": retries,
                        "error": str(e),
                    },
                )
            except TooManyRedirects as e:
                logger.error("Too many redirects", extra={"url": url, "error": str(e)})
                return None  # Don't retry redirect loops
            except HTTPError as e:
                status_code = e.response.status_code if e.response is not None else "unknown"
                if e.response is not None and 400 <= e.response.status_code < 500:
                    # Client errors (except 429) typically won't succeed on retry
                    if e.response.status_code == 404:
                        logger.warning("Resource not found", extra={"url": url, "status_code": 404})
                        return None
                    elif e.response.status_code == 403:
                        logger.warning("Access forbidden", extra={"url": url, "status_code": 403})
                        return None
                    elif e.response.status_code == 429:
                        logger.warning(
                            "Rate limited",
                            extra={
                                "url": url,
                                "status_code": 429,
                                "attempt": attempt + 1,
                                "max_attempts": retries,
                            },
                        )
                    else:
                        logger.warning(
                            "Client error",
                            extra={"url": url, "status_code": status_code, "error": str(e)},
                        )
                        return None
                else:
                    logger.warning(
                        "HTTP error",
                        extra={
                            "url": url,
                            "status_code": status_code,
                            "attempt": attempt + 1,
                            "max_attempts": retries,
                        },
                    )
            except RequestException as e:
                logger.warning(
                    "Request failed",
                    extra={
                        "url": url,
                        "attempt": attempt + 1,
                        "max_attempts": retries,
                        "error": str(e),
                    },
                )

            # Exponential backoff before retry
            if attempt < retries - 1:
                delay = 2**attempt
                logger.debug("Retrying", extra={"url": url, "delay_seconds": delay})
                time.sleep(delay)

        logger.error(
            "Failed to fetch URL after all attempts", extra={"url": url, "attempts": retries}
        )
        return None

    def _parse_date(self, date_str: str) -> datetime | None:
        """Parse various date formats"""
        formats = [
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%d %H:%M:%S",
            "%a, %d %b %Y %H:%M:%S %Z",
            "%a, %d %b %Y %H:%M:%S %z",
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
    def scrape(self) -> list[ArticleData]:
        """Scrape articles from this source"""
        pass


class RSSScraper(BaseScraper):
    """Generic RSS feed scraper"""

    def _fetch_feed_with_cache(self, feed_url: str) -> tuple[Any | None, bool]:
        """
        Fetch RSS feed with HTTP caching support.

        Returns:
            Tuple of (feed, was_modified):
            - If feed returns 304 Not Modified: (None, False)
            - If feed was fetched successfully: (feed_object, True)
            - If fetch failed: (None, None) - None for was_modified indicates failure
        """
        cache = get_http_cache()
        health_tracker = get_feed_health_tracker()
        timeout = self.global_config.get("timeout", DEFAULT_REQUEST_TIMEOUT)

        # Prepare headers with cache validators
        headers = {}
        if cache:
            headers.update(cache.get_cache_headers(feed_url))

        try:
            # Make request with conditional headers
            response = self.session.get(feed_url, timeout=timeout, headers=headers)

            # Handle 304 Not Modified
            if response.status_code == 304:
                logger.debug("Cache HIT (304 Not Modified)", extra={"feed_url": feed_url})
                if cache:
                    cache.record_hit()
                return None, False

            # Handle successful response
            response.raise_for_status()

            # Update cache with new headers
            if cache:
                cache.update_cache(feed_url, response)
                cache.record_miss()

            logger.debug("Cache MISS (fetched new content)", extra={"feed_url": feed_url})

            # Parse the feed from content
            feed = feedparser.parse(response.content)
            return feed, True

        except Timeout:
            logger.warning(
                "Timeout fetching feed", extra={"feed_url": feed_url, "timeout": timeout}
            )
            if health_tracker:
                health_tracker.record_failure(feed_url)
        except ConnectionError as e:
            logger.warning(
                "Connection error fetching feed", extra={"feed_url": feed_url, "error": str(e)}
            )
            if health_tracker:
                health_tracker.record_failure(feed_url)
        except HTTPError as e:
            status_code = e.response.status_code if e.response is not None else "unknown"
            if e.response is not None and e.response.status_code == 404:
                logger.warning("Feed not found", extra={"feed_url": feed_url, "status_code": 404})
            elif e.response is not None and e.response.status_code == 403:
                logger.warning(
                    "Feed access forbidden", extra={"feed_url": feed_url, "status_code": 403}
                )
            else:
                logger.warning(
                    "HTTP error fetching feed",
                    extra={"feed_url": feed_url, "status_code": status_code, "error": str(e)},
                )
            if health_tracker:
                health_tracker.record_failure(feed_url)
        except RequestException as e:
            logger.warning("Failed to fetch feed", extra={"feed_url": feed_url, "error": str(e)})
            if health_tracker:
                health_tracker.record_failure(feed_url)

        if cache:
            cache.record_miss()
        return None, None  # None for was_modified indicates failure

    def scrape(self) -> list[ArticleData]:
        articles = []
        rss_feeds = self.config.get("rss_feeds", [])
        cache = get_http_cache()
        rate_limiter = get_domain_rate_limiter()
        health_tracker = get_feed_health_tracker()

        for feed_url in rss_feeds:
            # Check if feed should be skipped due to being dead
            if health_tracker:
                should_skip, reason = health_tracker.should_skip_feed(feed_url)
                if should_skip:
                    logger.info(
                        "Skipping dead feed", extra={"feed_url": feed_url, "reason": reason}
                    )
                    continue

            # Apply per-domain rate limiting
            if rate_limiter:
                rate_limiter.wait_if_needed(feed_url)

            logger.info("Fetching RSS feed", extra={"feed_url": feed_url})

            try:
                # Use caching-aware fetch
                feed, was_modified = self._fetch_feed_with_cache(feed_url)

                # Handle fetch result
                if was_modified is None:
                    # Fetch failed - failure already recorded in _fetch_feed_with_cache
                    continue
                elif was_modified is False:
                    # Feed not modified (304) - this is still a success
                    logger.info("Feed not modified, skipping", extra={"feed_url": feed_url})
                    if health_tracker:
                        health_tracker.record_success(feed_url)
                    continue
                elif feed is None:
                    # Unexpected state
                    logger.warning(
                        "Unexpected state: was_modified=True but feed is None",
                        extra={"feed_url": feed_url},
                    )
                    continue

                # Feed fetched successfully - record success
                if health_tracker:
                    health_tracker.record_success(feed_url)

                for entry in feed.entries[:50]:  # Limit to 50 most recent per feed
                    try:
                        # Extract article URL
                        url = entry.get("link", "")
                        if not url:
                            continue

                        # Check if URL is valid
                        parsed = urlparse(url)
                        if not parsed.scheme or not parsed.netloc:
                            continue

                        # Extract title
                        title = entry.get("title", "").strip()
                        if not title:
                            continue

                        # Extract published date
                        published = None
                        if hasattr(entry, "published"):
                            published = self._parse_date(entry.published)
                        elif hasattr(entry, "updated"):
                            published = self._parse_date(entry.updated)

                        # Get content (may be summary or full content)
                        content = ""
                        if hasattr(entry, "content"):
                            content = entry.content[0].value
                        elif hasattr(entry, "summary"):
                            content = entry.summary
                        elif hasattr(entry, "description"):
                            content = entry.description

                        # Clean HTML from content if it looks like HTML
                        if content and ("<" in content and ">" in content):
                            try:
                                content = BeautifulSoup(content, "lxml").get_text()
                            except ImportError:
                                # lxml not installed, fall back to html.parser
                                content = BeautifulSoup(content, "html.parser").get_text()
                            except Exception as e:
                                logger.debug(
                                    "lxml parsing failed, using html.parser",
                                    extra={"error": str(e)},
                                )
                                content = BeautifulSoup(content, "html.parser").get_text()

                        article = ArticleData(
                            url=url,
                            title=title,
                            content=content,
                            source=self.config.get("name", "RSS"),
                            published_at=published,
                        )
                        articles.append(article)

                        self._sleep()

                    except (KeyError, AttributeError) as e:
                        logger.warning(
                            "Error parsing RSS entry (missing expected field)",
                            extra={"feed_url": feed_url, "error": str(e)},
                        )
                        continue
                    except (TypeError, ValueError) as e:
                        logger.warning(
                            "Error parsing RSS entry (invalid data format)",
                            extra={"feed_url": feed_url, "error": str(e)},
                        )
                        continue
                    except Exception as e:
                        logger.error(
                            "Unexpected error parsing RSS entry",
                            extra={
                                "feed_url": feed_url,
                                "error_type": type(e).__name__,
                                "error": str(e),
                            },
                        )
                        continue

            except (KeyError, AttributeError) as e:
                logger.warning(
                    "Error parsing RSS feed structure",
                    extra={"feed_url": feed_url, "error": str(e)},
                )
                # Record failure for health tracking
                if health_tracker:
                    health_tracker.record_failure(feed_url)
                continue
            except Exception as e:
                logger.error(
                    "Unexpected error processing RSS feed",
                    extra={"feed_url": feed_url, "error_type": type(e).__name__, "error": str(e)},
                )
                # Record failure for health tracking
                if health_tracker:
                    health_tracker.record_failure(feed_url)
                continue

        logger.info(
            "RSS scraper complete",
            extra={"source": self.config.get("name", "RSS"), "articles": len(articles)},
        )
        return articles


class WebScraper(BaseScraper):
    """Web page scraper for sites without good RSS"""

    def scrape(self) -> list[ArticleData]:
        articles = []
        base_url = self.config.get("base_url", "")

        # Get homepage
        html = self._fetch(base_url)
        if not html:
            return articles

        soup = BeautifulSoup(html, "html.parser")

        # Find article links
        selectors = self.config.get("selectors", {})
        article_links = selectors.get("article_links", "article a")

        links = set()
        for link in soup.select(article_links):
            href = link.get("href", "")
            if href:
                full_url = urljoin(base_url, href)
                # Filter for article URLs (usually contain dates or /article/)
                if self._is_article_url(full_url):
                    links.add(full_url)

        logger.info("Found article links", extra={"url": base_url, "links_found": len(links)})

        # Fetch each article
        for url in list(links)[:10]:  # Limit to 10 articles per run
            article = self._fetch_article(url)
            if article:
                articles.append(article)
            self._sleep()

        return articles

    def _is_article_url(self, url: str) -> bool:
        """Check if URL looks like an article"""
        patterns = [
            "/article/",
            "/story/",
            "/news/",
            "/202",  # Date-based URLs
            "/20",  # Year-based URLs
        ]
        return any(p in url for p in patterns)

    def _fetch_article(self, url: str) -> ArticleData | None:
        """Fetch and parse a single article"""
        html = self._fetch(url)
        if not html:
            return None

        soup = BeautifulSoup(html, "html.parser")
        selectors = self.config.get("selectors", {})

        # Extract title
        title = ""
        title_selector = selectors.get("headline", "h1")
        title_elem = soup.select_one(title_selector)
        if title_elem:
            title = title_elem.get_text().strip()

        if not title:
            # Try meta tags
            meta_title = soup.find("meta", property="og:title")
            if meta_title:
                title = meta_title.get("content", "")

        # Extract content
        content = ""
        content_selector = selectors.get("content", "article p")
        content_elems = soup.select(content_selector)
        if content_elems:
            content = " ".join(p.get_text().strip() for p in content_elems)

        # Extract timestamp
        published = None
        time_selector = selectors.get("timestamp", "time")
        time_elem = soup.select_one(time_selector)
        if time_elem:
            datetime_attr = time_elem.get("datetime") or time_elem.get("content")
            if datetime_attr:
                published = self._parse_date(datetime_attr)
            else:
                # Try parsing text content
                published = self._parse_date(time_elem.get_text().strip())

        if not published:
            # Try meta tags
            meta_date = soup.find("meta", property="article:published_time")
            if meta_date:
                published = self._parse_date(meta_date.get("content", ""))

        if title and content:
            return ArticleData(
                url=url,
                title=title,
                content=content,
                source=self.config.get("name", "Web"),
                published_at=published,
            )

        return None


class ReutersScraper(RSSScraper):
    """Reuters news scraper"""

    def __init__(self, config: dict[str, Any], global_config: dict[str, Any]):
        super().__init__(config, global_config)
        self.config["name"] = "Reuters"


class BloombergScraper(RSSScraper):
    """Bloomberg news scraper"""

    def __init__(self, config: dict[str, Any], global_config: dict[str, Any]):
        super().__init__(config, global_config)
        self.config["name"] = "Bloomberg"


class CNBCScraper(RSSScraper):
    """CNBC news scraper"""

    def __init__(self, config: dict[str, Any], global_config: dict[str, Any]):
        super().__init__(config, global_config)
        self.config["name"] = "CNBC"


class ScraperManager:
    """Manages multiple scrapers with parallel execution"""

    SCRAPER_MAP = {
        "reuters": ReutersScraper,
        "bloomberg": BloombergScraper,
        "cnbc": CNBCScraper,
    }

    def __init__(self, config_path: str):
        with open(config_path) as f:
            self.config = yaml.safe_load(f)

        self.scrapers = []
        self._init_http_cache()
        self._init_domain_rate_limiter()
        self._init_feed_health_tracker()
        self._init_scrapers()

    def _init_http_cache(self):
        """Initialize HTTP cache from config"""
        global_config = self.config.get("scraping", {})
        self.http_cache = init_http_cache(global_config)

    def _init_domain_rate_limiter(self):
        """Initialize per-domain rate limiter from config"""
        global_config = self.config.get("scraping", {})
        self.rate_limiter = init_domain_rate_limiter(global_config)

    def _init_feed_health_tracker(self):
        """Initialize feed health tracker from config"""
        global_config = self.config.get("scraping", {})
        self.feed_health_tracker = init_feed_health_tracker(global_config)

    def _init_scrapers(self):
        """Initialize enabled scrapers"""
        global_config = self.config.get("scraping", {})
        sources = self.config.get("sources", {})

        for source_name, source_config in sources.items():
            if not source_config.get("enabled", True):
                logger.info("Skipping disabled source", extra={"source": source_name})
                continue

            scraper_class = self.SCRAPER_MAP.get(source_name, RSSScraper)

            try:
                scraper = scraper_class(source_config, global_config)
                self.scrapers.append(scraper)
                logger.info("Initialized scraper", extra={"source": source_name})
            except Exception as e:
                logger.error(
                    "Failed to initialize scraper", extra={"source": source_name, "error": str(e)}
                )

    def _scrape_single(self, scraper) -> list[ArticleData]:
        """Scrape from a single scraper (for parallel execution)"""
        source_name = scraper.config.get("name")
        try:
            articles = scraper.scrape()
            logger.info(
                "Scraper completed", extra={"source": source_name, "articles": len(articles)}
            )
            return articles
        except Exception as e:
            logger.error("Scraper failed", extra={"source": source_name, "error": str(e)})
            return []

    def scrape_all(self, max_workers: int = 10) -> list[ArticleData]:
        """Run all scrapers in parallel and return combined results"""
        all_articles = []

        # Reset cache statistics for this run
        if self.http_cache:
            self.http_cache.reset_stats()

        # Clean up old cache entries (once per scraping cycle)
        self._cleanup_http_cache()

        # Use ThreadPoolExecutor for parallel scraping
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_scraper = {
                executor.submit(self._scrape_single, scraper): scraper for scraper in self.scrapers
            }

            for future in as_completed(future_to_scraper):
                scraper = future_to_scraper[future]
                try:
                    articles = future.result()
                    all_articles.extend(articles)
                except Exception as e:
                    logger.error(
                        "Scraper generated an exception",
                        extra={"source": scraper.config.get("name"), "error": str(e)},
                    )

        # Remove duplicates by URL
        seen_urls = set()
        unique_articles = []
        for article in all_articles:
            if article.url not in seen_urls:
                seen_urls.add(article.url)
                unique_articles.append(article)

        # Log cache statistics
        if self.http_cache:
            self.http_cache.log_statistics()

        # Log feed health statistics
        if self.feed_health_tracker:
            health_stats = self.feed_health_tracker.get_stats()
            if health_stats["dead_feeds"] > 0:
                logger.warning(
                    "Feed health issues detected",
                    extra={
                        "dead_feeds": health_stats["dead_feeds"],
                        "total_tracked": health_stats["total_tracked"],
                    },
                )
                # Log details of dead feeds
                dead_feeds = self.feed_health_tracker.get_all_dead_feeds()
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

    def _cleanup_http_cache(self):
        """Clean up old HTTP cache entries"""
        if not self.http_cache:
            return

        cache_config = self.config.get("scraping", {}).get("caching", {})
        max_age_days = cache_config.get("max_age_days", 7)

        try:
            removed = self.http_cache.cleanup_old_entries(max_age_days=max_age_days)
            if removed > 0:
                logger.info(
                    "Cleaned up old HTTP cache entries",
                    extra={"removed_entries": removed},
                )
        except Exception as e:
            logger.error("Error during HTTP cache cleanup", extra={"error": str(e)})

    def get_feed_health_report(self) -> dict[str, Any]:
        """Get a detailed report of feed health status"""
        if not self.feed_health_tracker:
            return {"error": "Feed health tracking not initialized"}

        return {
            "stats": self.feed_health_tracker.get_stats(),
            "dead_feeds": self.feed_health_tracker.get_all_dead_feeds(),
        }
