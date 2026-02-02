"""
Tests for the scraper module.

Tests HTTPCache, DomainRateLimiter, FeedHealthTracker, and RSSScraper components.
"""

import pytest
import json
import os
import time
import threading
import tempfile
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from scraper import (
    HTTPCache,
    DomainRateLimiter,
    FeedHealthTracker,
    RSSScraper,
    ArticleData,
    BaseScraper,
    init_http_cache,
    init_domain_rate_limiter,
    init_feed_health_tracker,
    get_http_cache,
    get_domain_rate_limiter,
    get_feed_health_tracker,
)


# =============================================================================
# HTTPCache Tests
# =============================================================================


class TestHTTPCache:
    """Tests for HTTPCache class."""

    def test_cache_initialization_creates_empty_cache(self, tmp_path):
        """Test that HTTPCache initializes with empty cache when no file exists."""
        cache_file = tmp_path / "test_cache.json"
        cache = HTTPCache(cache_file=str(cache_file), enabled=True, log_stats=False)

        assert cache.enabled is True
        assert cache._cache == {}
        hits, misses = cache.get_stats()
        assert hits == 0
        assert misses == 0

    def test_cache_disabled_mode(self, tmp_path):
        """Test that disabled cache returns empty headers and doesn't save."""
        cache_file = tmp_path / "test_cache.json"
        cache = HTTPCache(cache_file=str(cache_file), enabled=False, log_stats=False)

        # Should return empty headers when disabled
        headers = cache.get_cache_headers("http://example.com/feed")
        assert headers == {}

    def test_cache_save_and_load(self, tmp_path):
        """Test saving and loading cache from file."""
        cache_file = tmp_path / "data" / "test_cache.json"

        # Create cache and add entry
        cache1 = HTTPCache(cache_file=str(cache_file), enabled=True, log_stats=False)

        # Simulate updating cache with response headers
        mock_response = Mock()
        mock_response.headers = {
            "ETag": '"abc123"',
            "Last-Modified": "Sun, 01 Jan 2025 00:00:00 GMT",
        }

        cache1.update_cache("http://example.com/feed", mock_response)

        # Create new cache instance to load from file
        cache2 = HTTPCache(cache_file=str(cache_file), enabled=True, log_stats=False)

        # Verify loaded cache has the entry
        assert "http://example.com/feed" in cache2._cache
        assert cache2._cache["http://example.com/feed"]["etag"] == '"abc123"'

    def test_get_cache_headers_returns_conditional_headers(self, tmp_path):
        """Test that get_cache_headers returns correct conditional request headers."""
        cache_file = tmp_path / "test_cache.json"
        cache = HTTPCache(cache_file=str(cache_file), enabled=True, log_stats=False)

        # Manually set cache entry
        cache._cache["http://example.com/feed"] = {
            "etag": '"xyz789"',
            "last_modified": "Mon, 02 Jan 2025 12:00:00 GMT",
            "last_fetched": datetime.now().isoformat(),
        }

        headers = cache.get_cache_headers("http://example.com/feed")

        assert headers.get("If-None-Match") == '"xyz789"'
        assert headers.get("If-Modified-Since") == "Mon, 02 Jan 2025 12:00:00 GMT"

    def test_cache_hit_and_miss_stats(self, tmp_path):
        """Test cache hit and miss statistics recording."""
        cache_file = tmp_path / "test_cache.json"
        cache = HTTPCache(cache_file=str(cache_file), enabled=True, log_stats=False)

        cache.record_hit()
        cache.record_hit()
        cache.record_miss()

        hits, misses = cache.get_stats()
        assert hits == 2
        assert misses == 1

    def test_reset_stats(self, tmp_path):
        """Test resetting cache statistics."""
        cache_file = tmp_path / "test_cache.json"
        cache = HTTPCache(cache_file=str(cache_file), enabled=True, log_stats=False)

        cache.record_hit()
        cache.record_miss()
        cache.reset_stats()

        hits, misses = cache.get_stats()
        assert hits == 0
        assert misses == 0

    def test_cleanup_old_entries(self, tmp_path):
        """Test cleanup of old cache entries."""
        cache_file = tmp_path / "test_cache.json"
        cache = HTTPCache(cache_file=str(cache_file), enabled=True, log_stats=False)

        # Add entries with different ages
        old_date = (datetime.now() - timedelta(days=10)).isoformat()
        recent_date = (datetime.now() - timedelta(days=1)).isoformat()

        cache._cache = {
            "http://old.com/feed": {"etag": '"old"', "last_fetched": old_date},
            "http://recent.com/feed": {"etag": '"recent"', "last_fetched": recent_date},
            "http://no-date.com/feed": {
                "etag": '"nodate"'
                # No last_fetched - should be removed
            },
        }

        # Cleanup entries older than 7 days
        removed = cache.cleanup_old_entries(max_age_days=7)

        assert removed == 2  # old entry and no-date entry removed
        assert "http://old.com/feed" not in cache._cache
        assert "http://no-date.com/feed" not in cache._cache
        assert "http://recent.com/feed" in cache._cache

    def test_cache_cleanup_disabled_returns_zero(self, tmp_path):
        """Test that cleanup returns 0 when cache is disabled."""
        cache_file = tmp_path / "test_cache.json"
        cache = HTTPCache(cache_file=str(cache_file), enabled=False, log_stats=False)

        removed = cache.cleanup_old_entries(max_age_days=7)
        assert removed == 0

    def test_cache_load_handles_corrupt_file(self, tmp_path):
        """Test that cache handles corrupted JSON file gracefully."""
        cache_file = tmp_path / "corrupt_cache.json"
        cache_file.write_text("{ invalid json }")

        # Should not raise, should start with empty cache
        cache = HTTPCache(cache_file=str(cache_file), enabled=True, log_stats=False)
        assert cache._cache == {}


# =============================================================================
# DomainRateLimiter Tests
# =============================================================================


class TestDomainRateLimiter:
    """Tests for DomainRateLimiter class."""

    def test_rate_limiter_initialization(self):
        """Test rate limiter initializes with correct min_delay."""
        limiter = DomainRateLimiter(min_delay=1.0)
        assert limiter.min_delay == 1.0
        assert limiter._last_request_time == {}

    def test_extract_domain(self):
        """Test domain extraction from URLs."""
        limiter = DomainRateLimiter()

        assert limiter._extract_domain("http://example.com/path") == "example.com"
        assert limiter._extract_domain("https://WWW.Example.COM/path") == "www.example.com"
        assert (
            limiter._extract_domain("https://sub.domain.example.com/") == "sub.domain.example.com"
        )

    def test_first_request_no_wait(self):
        """Test that first request to a domain doesn't wait."""
        limiter = DomainRateLimiter(min_delay=2.0)

        waited = limiter.wait_if_needed("http://example.com/feed")

        # First request should not wait
        assert waited == 0.0

    def test_subsequent_request_waits(self):
        """Test that subsequent requests to same domain wait."""
        limiter = DomainRateLimiter(min_delay=0.1)  # Short delay for testing

        # First request
        limiter.wait_if_needed("http://example.com/feed1")

        # Immediate second request should wait
        start = time.time()
        waited = limiter.wait_if_needed("http://example.com/feed2")
        elapsed = time.time() - start

        # Should have waited approximately min_delay
        assert waited > 0
        assert elapsed >= 0.08  # Allow some tolerance

    def test_different_domains_no_wait(self):
        """Test that requests to different domains don't wait for each other."""
        limiter = DomainRateLimiter(min_delay=1.0)

        # Request to first domain
        limiter.wait_if_needed("http://domain1.com/feed")

        # Request to different domain should not wait
        waited = limiter.wait_if_needed("http://domain2.com/feed")
        assert waited == 0.0

    def test_get_stats(self):
        """Test get_stats returns correct information."""
        limiter = DomainRateLimiter(min_delay=1.5)

        limiter.wait_if_needed("http://example1.com/feed")
        limiter.wait_if_needed("http://example2.com/feed")

        stats = limiter.get_stats()

        assert stats["min_delay"] == 1.5
        assert stats["tracked_domains"] == 2
        assert "example1.com" in stats["domains"]
        assert "example2.com" in stats["domains"]

    def test_thread_safety(self):
        """Test that rate limiter is thread-safe."""
        limiter = DomainRateLimiter(min_delay=0.05)
        results = []

        def make_request(url, thread_id):
            waited = limiter.wait_if_needed(url)
            results.append((thread_id, waited))

        threads = []
        for i in range(5):
            t = threading.Thread(target=make_request, args=(f"http://example.com/feed{i}", i))
            threads.append(t)

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        # Should have 5 results
        assert len(results) == 5


# =============================================================================
# FeedHealthTracker Tests
# =============================================================================


class TestFeedHealthTracker:
    """Tests for FeedHealthTracker class."""

    def test_initialization(self):
        """Test feed health tracker initialization."""
        tracker = FeedHealthTracker(max_consecutive_failures=5, base_backoff_minutes=15)

        assert tracker.max_consecutive_failures == 5
        assert tracker.base_backoff_minutes == 15
        assert tracker._feed_status == {}

    def test_record_success(self):
        """Test recording successful fetch."""
        tracker = FeedHealthTracker()

        tracker.record_success("http://example.com/feed")

        status = tracker.get_feed_health("http://example.com/feed")
        assert status is not None
        assert status["consecutive_failures"] == 0
        assert status["is_dead"] is False
        assert status["last_success"] is not None

    def test_record_failure_increments_count(self):
        """Test that failures increment the counter."""
        tracker = FeedHealthTracker(max_consecutive_failures=5)

        for i in range(3):
            tracker.record_failure("http://example.com/feed")

        status = tracker.get_feed_health("http://example.com/feed")
        assert status["consecutive_failures"] == 3
        assert status["is_dead"] is False

    def test_feed_marked_dead_after_max_failures(self):
        """Test that feed is marked dead after max consecutive failures."""
        tracker = FeedHealthTracker(max_consecutive_failures=3, base_backoff_minutes=15)

        for i in range(3):
            is_dead = tracker.record_failure("http://example.com/feed")

        assert is_dead is True

        status = tracker.get_feed_health("http://example.com/feed")
        assert status["is_dead"] is True
        assert status["next_retry"] is not None

    def test_success_resets_failure_count(self):
        """Test that success resets the failure count."""
        tracker = FeedHealthTracker(max_consecutive_failures=5)

        # Record some failures
        for i in range(3):
            tracker.record_failure("http://example.com/feed")

        # Record success
        tracker.record_success("http://example.com/feed")

        status = tracker.get_feed_health("http://example.com/feed")
        assert status["consecutive_failures"] == 0
        assert status["is_dead"] is False

    def test_should_skip_feed_healthy(self):
        """Test should_skip_feed returns False for healthy feeds."""
        tracker = FeedHealthTracker()

        tracker.record_success("http://example.com/feed")

        should_skip, reason = tracker.should_skip_feed("http://example.com/feed")
        assert should_skip is False
        assert reason is None

    def test_should_skip_feed_dead_in_backoff(self):
        """Test should_skip_feed returns True for dead feeds in backoff period."""
        tracker = FeedHealthTracker(max_consecutive_failures=2, base_backoff_minutes=60)

        # Make feed dead
        tracker.record_failure("http://example.com/feed")
        tracker.record_failure("http://example.com/feed")

        should_skip, reason = tracker.should_skip_feed("http://example.com/feed")
        assert should_skip is True
        assert "dead" in reason.lower() or "retry" in reason.lower()

    def test_should_skip_feed_unknown(self):
        """Test should_skip_feed returns False for unknown feeds."""
        tracker = FeedHealthTracker()

        should_skip, reason = tracker.should_skip_feed("http://unknown.com/feed")
        assert should_skip is False
        assert reason is None

    def test_exponential_backoff(self):
        """Test that backoff time increases exponentially."""
        tracker = FeedHealthTracker(max_consecutive_failures=2, base_backoff_minutes=15)

        # First dead state
        tracker.record_failure("http://example.com/feed")
        tracker.record_failure("http://example.com/feed")

        status1 = tracker.get_feed_health("http://example.com/feed")
        first_retry = status1["next_retry"]

        # Additional failure should increase backoff
        tracker.record_failure("http://example.com/feed")

        status2 = tracker.get_feed_health("http://example.com/feed")
        second_retry = status2["next_retry"]

        # Second retry should be further in the future
        assert second_retry > first_retry

    def test_get_all_dead_feeds(self):
        """Test getting all dead feeds."""
        tracker = FeedHealthTracker(max_consecutive_failures=2)

        # Create two dead feeds
        tracker.record_failure("http://dead1.com/feed")
        tracker.record_failure("http://dead1.com/feed")

        tracker.record_failure("http://dead2.com/feed")
        tracker.record_failure("http://dead2.com/feed")

        # One healthy feed
        tracker.record_success("http://healthy.com/feed")

        dead_feeds = tracker.get_all_dead_feeds()

        assert len(dead_feeds) == 2
        urls = [f["url"] for f in dead_feeds]
        assert "http://dead1.com/feed" in urls
        assert "http://dead2.com/feed" in urls

    def test_get_stats(self):
        """Test getting feed health statistics."""
        tracker = FeedHealthTracker(max_consecutive_failures=2)

        # Create feeds
        tracker.record_failure("http://dead.com/feed")
        tracker.record_failure("http://dead.com/feed")
        tracker.record_success("http://healthy.com/feed")

        stats = tracker.get_stats()

        assert stats["total_tracked"] == 2
        assert stats["dead_feeds"] == 1
        assert stats["healthy_feeds"] == 1


# =============================================================================
# RSSScraper Tests
# =============================================================================


class TestRSSScraper:
    """Tests for RSSScraper class."""

    @pytest.fixture
    def scraper_config(self):
        """Default scraper configuration."""
        return {"name": "TestSource", "rss_feeds": ["http://example.com/feed.rss"], "enabled": True}

    @pytest.fixture
    def global_config(self):
        """Default global configuration."""
        return {
            "user_agents": ["TestUserAgent/1.0"],
            "delay_min": 0,
            "delay_max": 0,
            "max_retries": 2,
            "timeout": 10,
        }

    def test_parse_date_iso_format(self, scraper_config, global_config):
        """Test parsing ISO date format."""
        scraper = RSSScraper(scraper_config, global_config)

        result = scraper._parse_date("2025-01-15T14:30:00")

        assert result is not None
        assert result.year == 2025
        assert result.month == 1
        assert result.day == 15
        assert result.hour == 14
        assert result.minute == 30

    def test_parse_date_iso_with_z(self, scraper_config, global_config):
        """Test parsing ISO date with Z suffix."""
        scraper = RSSScraper(scraper_config, global_config)

        result = scraper._parse_date("2025-01-15T14:30:00Z")

        assert result is not None
        assert result.year == 2025

    def test_parse_date_rfc_format(self, scraper_config, global_config):
        """Test parsing RFC 2822 date format."""
        scraper = RSSScraper(scraper_config, global_config)

        result = scraper._parse_date("Wed, 15 Jan 2025 14:30:00 GMT")

        assert result is not None
        assert result.year == 2025
        assert result.month == 1
        assert result.day == 15

    def test_parse_date_invalid_returns_none(self, scraper_config, global_config):
        """Test that invalid date returns None."""
        scraper = RSSScraper(scraper_config, global_config)

        result = scraper._parse_date("not a valid date")

        assert result is None

    def test_parse_date_space_separated(self, scraper_config, global_config):
        """Test parsing space-separated date format."""
        scraper = RSSScraper(scraper_config, global_config)

        result = scraper._parse_date("2025-01-15 14:30:00")

        assert result is not None
        assert result.year == 2025

    @patch("scraper.get_http_cache")
    @patch("scraper.get_domain_rate_limiter")
    @patch("scraper.get_feed_health_tracker")
    def test_scrape_with_mock_feed(
        self, mock_health, mock_limiter, mock_cache, scraper_config, global_config
    ):
        """Test scraping with mocked feed response."""
        # Setup mocks
        mock_cache.return_value = None
        mock_limiter.return_value = None
        mock_health.return_value = None

        scraper = RSSScraper(scraper_config, global_config)

        # Mock the session.get to return feed content
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
            <channel>
                <title>Test Feed</title>
                <item>
                    <title>Test Article 1</title>
                    <link>http://example.com/article1</link>
                    <description>This is test content for article 1.</description>
                    <pubDate>Wed, 15 Jan 2025 14:30:00 GMT</pubDate>
                </item>
                <item>
                    <title>Test Article 2</title>
                    <link>http://example.com/article2</link>
                    <description>This is test content for article 2.</description>
                    <pubDate>Wed, 15 Jan 2025 15:30:00 GMT</pubDate>
                </item>
            </channel>
        </rss>"""
        mock_response.raise_for_status = Mock()

        with patch.object(scraper.session, "get", return_value=mock_response):
            articles = scraper.scrape()

        assert len(articles) == 2
        assert articles[0].title == "Test Article 1"
        assert articles[0].url == "http://example.com/article1"
        assert articles[0].source == "TestSource"

    @patch("scraper.get_http_cache")
    @patch("scraper.get_domain_rate_limiter")
    @patch("scraper.get_feed_health_tracker")
    def test_scrape_handles_304_not_modified(
        self, mock_health, mock_limiter, mock_cache, scraper_config, global_config
    ):
        """Test scraping handles 304 Not Modified response."""
        # Setup mocks
        mock_cache_instance = Mock()
        mock_cache_instance.get_cache_headers.return_value = {"If-None-Match": '"abc"'}
        mock_cache_instance.record_hit = Mock()
        mock_cache.return_value = mock_cache_instance
        mock_limiter.return_value = None

        mock_health_instance = Mock()
        mock_health_instance.should_skip_feed.return_value = (False, None)
        mock_health_instance.record_success = Mock()
        mock_health.return_value = mock_health_instance

        scraper = RSSScraper(scraper_config, global_config)

        # Mock 304 response
        mock_response = Mock()
        mock_response.status_code = 304

        with patch.object(scraper.session, "get", return_value=mock_response):
            articles = scraper.scrape()

        # Should return empty list (no new articles)
        assert articles == []
        # Should record cache hit
        mock_cache_instance.record_hit.assert_called_once()

    @patch("scraper.get_http_cache")
    @patch("scraper.get_domain_rate_limiter")
    @patch("scraper.get_feed_health_tracker")
    def test_scrape_skips_dead_feeds(
        self, mock_health, mock_limiter, mock_cache, scraper_config, global_config
    ):
        """Test that dead feeds are skipped."""
        mock_cache.return_value = None
        mock_limiter.return_value = None

        mock_health_instance = Mock()
        mock_health_instance.should_skip_feed.return_value = (True, "Feed marked as dead")
        mock_health.return_value = mock_health_instance

        scraper = RSSScraper(scraper_config, global_config)

        with patch.object(scraper.session, "get") as mock_get:
            articles = scraper.scrape()

        # Should not make any requests
        mock_get.assert_not_called()
        assert articles == []

    @patch("scraper.get_http_cache")
    @patch("scraper.get_domain_rate_limiter")
    @patch("scraper.get_feed_health_tracker")
    def test_scrape_records_failure_on_error(
        self, mock_health, mock_limiter, mock_cache, scraper_config, global_config
    ):
        """Test that feed failures are recorded."""
        mock_cache.return_value = None
        mock_limiter.return_value = None

        mock_health_instance = Mock()
        mock_health_instance.should_skip_feed.return_value = (False, None)
        mock_health_instance.record_failure = Mock()
        mock_health.return_value = mock_health_instance

        scraper = RSSScraper(scraper_config, global_config)

        # Mock connection error
        from requests.exceptions import ConnectionError

        with patch.object(scraper.session, "get", side_effect=ConnectionError("Connection failed")):
            articles = scraper.scrape()

        assert articles == []
        mock_health_instance.record_failure.assert_called_once()


# =============================================================================
# Global Function Tests
# =============================================================================


class TestGlobalFunctions:
    """Tests for global initialization functions."""

    def test_init_http_cache(self, tmp_path):
        """Test HTTP cache initialization from config."""
        config = {
            "caching": {
                "enabled": True,
                "cache_file": str(tmp_path / "cache.json"),
                "log_stats": False,
            }
        }

        cache = init_http_cache(config)

        assert cache is not None
        assert cache.enabled is True
        assert get_http_cache() is cache

    def test_init_domain_rate_limiter(self):
        """Test domain rate limiter initialization from config."""
        config = {"rate_limiting": {"per_domain_delay": 3.0}}

        limiter = init_domain_rate_limiter(config)

        assert limiter is not None
        assert limiter.min_delay == 3.0
        assert get_domain_rate_limiter() is limiter

    def test_init_feed_health_tracker(self):
        """Test feed health tracker initialization from config."""
        config = {"feed_health": {"max_consecutive_failures": 10, "base_backoff_minutes": 30}}

        tracker = init_feed_health_tracker(config)

        assert tracker is not None
        assert tracker.max_consecutive_failures == 10
        assert tracker.base_backoff_minutes == 30
        assert get_feed_health_tracker() is tracker


# =============================================================================
# ArticleData Tests
# =============================================================================


class TestArticleData:
    """Tests for ArticleData class."""

    def test_article_data_creation(self):
        """Test creating ArticleData instance."""
        article = ArticleData(
            url="http://example.com/article",
            title="Test Article",
            content="This is test content.",
            source="TestSource",
            published_at=datetime(2025, 1, 15, 12, 0, 0),
        )

        assert article.url == "http://example.com/article"
        assert article.title == "Test Article"
        assert article.content == "This is test content."
        assert article.source == "TestSource"
        assert article.published_at == datetime(2025, 1, 15, 12, 0, 0)

    def test_article_data_default_published_at(self):
        """Test that published_at defaults to now if not provided."""
        before = datetime.now()
        article = ArticleData(
            url="http://example.com/article",
            title="Test Article",
            content="Content",
            source="TestSource",
        )
        after = datetime.now()

        assert article.published_at >= before
        assert article.published_at <= after

    def test_article_data_repr(self):
        """Test ArticleData string representation."""
        article = ArticleData(
            url="http://example.com/article",
            title="This is a very long title that should be truncated in the repr",
            content="Content",
            source="TestSource",
        )

        repr_str = repr(article)
        assert "ArticleData" in repr_str
        assert "TestSource" in repr_str
