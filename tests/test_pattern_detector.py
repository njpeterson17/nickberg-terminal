"""
Unit tests for PatternDetector class.
"""

import pytest
import sys
import os
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime, timedelta

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pattern_detector import PatternDetector, PatternAlert


class TestPatternAlert:
    """Tests for PatternAlert dataclass."""

    def test_pattern_alert_creation(self):
        """Test creating a PatternAlert."""
        alert = PatternAlert(
            pattern_type="volume_spike",
            ticker="AAPL",
            company_name="Apple",
            severity="high",
            message="Test alert message",
            details={"key": "value"},
        )

        assert alert.pattern_type == "volume_spike"
        assert alert.ticker == "AAPL"
        assert alert.company_name == "Apple"
        assert alert.severity == "high"
        assert alert.message == "Test alert message"
        assert alert.details == {"key": "value"}

    def test_pattern_alert_to_dict(self):
        """Test PatternAlert serialization."""
        alert = PatternAlert(
            pattern_type="sentiment_shift",
            ticker="GOOGL",
            company_name="Google",
            severity="medium",
            message="Sentiment changed",
            details={"shift": 0.5},
        )

        alert_dict = alert.to_dict()

        assert alert_dict["pattern_type"] == "sentiment_shift"
        assert alert_dict["ticker"] == "GOOGL"
        assert alert_dict["company_name"] == "Google"
        assert alert_dict["severity"] == "medium"
        assert alert_dict["message"] == "Sentiment changed"
        assert alert_dict["details"] == {"shift": 0.5}
        assert "timestamp" in alert_dict


class TestPatternDetectorVolumeSpike:
    """Tests for volume spike detection."""

    def test_volume_spike_detection_high_severity(self, mock_database, sample_config):
        """Test detection of high severity volume spike."""
        # Configure mock database
        mock_database.get_mention_counts.return_value = [
            {"company_ticker": "AAPL", "company_name": "Apple", "count": 20}
        ]

        # Volume spike: 3 calls + Momentum: 7 calls = 10 total
        # For high spike: 6h=10, 24h=15, 7d=28 (avg=4/day, expected_6h=1, spike=10x)
        volume_counts = [10, 15, 28]
        # Momentum: cumulative for days 1-7 (need 7 values showing no increasing trend)
        momentum_counts = [28, 28, 28, 28, 28, 28, 28]

        mock_database.get_article_count_for_company.side_effect = volume_counts + momentum_counts

        detector = PatternDetector(mock_database, sample_config)

        # Mock _get_company_articles to return empty (no sentiment/negative cluster alerts)
        with patch.object(detector, "_get_company_articles", return_value=[]):
            alerts = detector.detect_all_patterns()

        # Should detect volume spike
        volume_alerts = [a for a in alerts if a.pattern_type == "volume_spike"]
        assert len(volume_alerts) >= 1

        # Check severity - 10 articles vs expected 1 = 10x spike = high
        high_severity = [a for a in volume_alerts if a.severity == "high"]
        assert len(high_severity) >= 1

    def test_volume_spike_detection_medium_severity(self, mock_database, sample_config):
        """Test detection of medium severity volume spike."""
        mock_database.get_mention_counts.return_value = [
            {"company_ticker": "AAPL", "company_name": "Apple", "count": 10}
        ]

        # 6h count = 4, 7d = 28 (avg 4/day, expected_6h = 1)
        # spike ratio = 4 / 1 = 4x (between 3 and 5, so medium)
        volume_counts = [4, 8, 28]
        momentum_counts = [28, 28, 28, 28, 28, 28, 28]

        mock_database.get_article_count_for_company.side_effect = volume_counts + momentum_counts

        detector = PatternDetector(mock_database, sample_config)

        with patch.object(detector, "_get_company_articles", return_value=[]):
            alerts = detector.detect_all_patterns()

        volume_alerts = [a for a in alerts if a.pattern_type == "volume_spike"]
        assert len(volume_alerts) >= 1

        # Should be medium severity (3x <= spike < 5x)
        medium_severity = [a for a in volume_alerts if a.severity == "medium"]
        assert len(medium_severity) >= 1

    def test_no_volume_spike_below_threshold(self, mock_database, sample_config):
        """Test no alert when spike is below threshold."""
        mock_database.get_mention_counts.return_value = [
            {"company_ticker": "AAPL", "company_name": "Apple", "count": 10}
        ]

        # Normal volume - no spike (6h=1, expected ~1, ratio ~1)
        volume_counts = [1, 4, 28]
        momentum_counts = [28, 28, 28, 28, 28, 28, 28]

        mock_database.get_article_count_for_company.side_effect = volume_counts + momentum_counts

        detector = PatternDetector(mock_database, sample_config)

        with patch.object(detector, "_get_company_articles", return_value=[]):
            alerts = detector.detect_all_patterns()

        volume_alerts = [a for a in alerts if a.pattern_type == "volume_spike"]
        assert len(volume_alerts) == 0

    def test_volume_spike_burst_detection(self, mock_database, sample_config):
        """Test detection of 24h burst (50%+ of weekly articles)."""
        mock_database.get_mention_counts.return_value = [
            {"company_ticker": "AAPL", "company_name": "Apple", "count": 10}
        ]

        # Scenario: 6h count is low but 24h is >50% of 7d
        # 6h=2 (not enough for spike), 24h=6, 7d=10 (60% burst)
        volume_counts = [2, 6, 10]
        momentum_counts = [10, 10, 10, 10, 10, 10, 10]

        mock_database.get_article_count_for_company.side_effect = volume_counts + momentum_counts

        detector = PatternDetector(mock_database, sample_config)

        with patch.object(detector, "_get_company_articles", return_value=[]):
            alerts = detector.detect_all_patterns()

        volume_alerts = [a for a in alerts if a.pattern_type == "volume_spike"]
        # Should detect the burst pattern
        burst_alerts = [a for a in volume_alerts if "percentage" in str(a.details)]
        assert len(burst_alerts) >= 1

    def test_skip_company_below_min_articles(self, mock_database, sample_config):
        """Test that companies below min article threshold are skipped."""
        mock_database.get_mention_counts.return_value = [
            {
                "company_ticker": "AAPL",
                "company_name": "Apple",
                "count": 2,
            }  # Below min_articles_for_alert
        ]

        detector = PatternDetector(mock_database, sample_config)
        alerts = detector.detect_all_patterns()

        assert len(alerts) == 0


class TestPatternDetectorSentimentShift:
    """Tests for sentiment shift detection."""

    def test_positive_sentiment_shift(
        self, mock_database, sample_config, positive_articles, neutral_articles
    ):
        """Test detection of positive sentiment shift."""
        mock_database.get_mention_counts.return_value = [
            {"company_ticker": "AAPL", "company_name": "Apple", "count": 10}
        ]

        # No volume spike
        volume_counts = [1, 2, 28]
        momentum_counts = [28, 28, 28, 28, 28, 28, 28]
        mock_database.get_article_count_for_company.side_effect = volume_counts + momentum_counts

        detector = PatternDetector(mock_database, sample_config)

        # Mock _get_company_articles to return positive recent, neutral baseline
        def get_articles_side_effect(ticker, hours, exclude_hours=0):
            if exclude_hours > 0:
                return neutral_articles  # Baseline
            elif hours == sample_config["windows"]["short"]:
                return []  # For negative cluster
            else:
                return positive_articles  # Recent for sentiment

        with patch.object(detector, "_get_company_articles", side_effect=get_articles_side_effect):
            alerts = detector.detect_all_patterns()

        sentiment_alerts = [a for a in alerts if a.pattern_type == "sentiment_shift"]
        # Should detect positive shift
        positive_shifts = [a for a in sentiment_alerts if a.details.get("direction") == "positive"]
        assert len(positive_shifts) >= 1

    def test_negative_sentiment_shift(
        self, mock_database, sample_config, negative_articles, neutral_articles
    ):
        """Test detection of negative sentiment shift."""
        mock_database.get_mention_counts.return_value = [
            {"company_ticker": "AAPL", "company_name": "Apple", "count": 10}
        ]

        volume_counts = [1, 2, 28]
        momentum_counts = [28, 28, 28, 28, 28, 28, 28]
        mock_database.get_article_count_for_company.side_effect = volume_counts + momentum_counts

        detector = PatternDetector(mock_database, sample_config)

        def get_articles_side_effect(ticker, hours, exclude_hours=0):
            if exclude_hours > 0:
                return neutral_articles  # Baseline
            elif hours == sample_config["windows"]["short"]:
                return negative_articles  # For negative cluster
            else:
                return negative_articles  # Recent for sentiment

        with patch.object(detector, "_get_company_articles", side_effect=get_articles_side_effect):
            alerts = detector.detect_all_patterns()

        sentiment_alerts = [a for a in alerts if a.pattern_type == "sentiment_shift"]
        negative_shifts = [a for a in sentiment_alerts if a.details.get("direction") == "negative"]
        assert len(negative_shifts) >= 1

    def test_unusually_negative_coverage(self, mock_database, sample_config, negative_articles):
        """Test detection of unusually negative coverage without baseline."""
        mock_database.get_mention_counts.return_value = [
            {"company_ticker": "AAPL", "company_name": "Apple", "count": 10}
        ]

        volume_counts = [1, 2, 28]
        momentum_counts = [28, 28, 28, 28, 28, 28, 28]
        mock_database.get_article_count_for_company.side_effect = volume_counts + momentum_counts

        detector = PatternDetector(mock_database, sample_config)

        def get_articles_side_effect(ticker, hours, exclude_hours=0):
            if exclude_hours > 0:
                return []  # No baseline available
            elif hours == sample_config["windows"]["short"]:
                return negative_articles
            else:
                return negative_articles

        with patch.object(detector, "_get_company_articles", side_effect=get_articles_side_effect):
            alerts = detector.detect_all_patterns()

        sentiment_alerts = [a for a in alerts if a.pattern_type == "sentiment_shift"]
        # Should still detect unusually negative coverage
        negative_alerts = [
            a for a in sentiment_alerts if "negative" in a.details.get("direction", "")
        ]
        assert len(negative_alerts) >= 1

    def test_not_enough_articles_for_sentiment(self, mock_database, sample_config):
        """Test no sentiment alert when not enough articles."""
        mock_database.get_mention_counts.return_value = [
            {"company_ticker": "AAPL", "company_name": "Apple", "count": 10}
        ]

        volume_counts = [1, 2, 28]
        momentum_counts = [28, 28, 28, 28, 28, 28, 28]
        mock_database.get_article_count_for_company.side_effect = volume_counts + momentum_counts

        detector = PatternDetector(mock_database, sample_config)

        # Only 2 articles - below threshold of 3
        few_articles = [{"content": "text", "id": 1}, {"content": "text", "id": 2}]

        def get_articles_side_effect(ticker, hours, exclude_hours=0):
            if exclude_hours > 0:
                return []
            elif hours == sample_config["windows"]["short"]:
                return []
            else:
                return few_articles

        with patch.object(detector, "_get_company_articles", side_effect=get_articles_side_effect):
            alerts = detector.detect_all_patterns()

        sentiment_alerts = [a for a in alerts if a.pattern_type == "sentiment_shift"]
        assert len(sentiment_alerts) == 0


class TestPatternDetectorMomentum:
    """Tests for momentum building detection."""

    def test_increasing_momentum_detection(self, mock_database, sample_config):
        """Test detection of increasing article momentum."""
        mock_database.get_mention_counts.return_value = [
            {"company_ticker": "AAPL", "company_name": "Apple", "count": 20}
        ]

        # Volume counts (no spike)
        volume_counts = [1, 2, 20]

        # Cumulative counts showing increasing daily articles
        # Day 0 (most recent): 6 articles (cumulative)
        # Day 1: 10 cumulative -> daily = 10 - 6 = 4
        # Day 2: 12 cumulative -> daily = 12 - 10 = 2
        # Pattern: daily[0]=6, daily[1]=4, daily[2]=2 -> 6 > 4 > 2, so momentum!
        momentum_counts = [6, 10, 12, 14, 16, 18, 20]

        mock_database.get_article_count_for_company.side_effect = volume_counts + momentum_counts

        detector = PatternDetector(mock_database, sample_config)

        with patch.object(detector, "_get_company_articles", return_value=[]):
            alerts = detector.detect_all_patterns()

        momentum_alerts = [a for a in alerts if a.pattern_type == "momentum"]
        assert len(momentum_alerts) >= 1

    def test_no_momentum_when_decreasing(self, mock_database, sample_config):
        """Test no momentum alert when coverage is decreasing."""
        mock_database.get_mention_counts.return_value = [
            {"company_ticker": "AAPL", "company_name": "Apple", "count": 20}
        ]

        volume_counts = [1, 2, 36]

        # Decreasing pattern: daily = 2, 4, 6 (not increasing, it's decreasing)
        # cumulative day0=2, day1=6 (daily=4), day2=12 (daily=6)
        momentum_counts = [2, 6, 12, 18, 24, 30, 36]

        mock_database.get_article_count_for_company.side_effect = volume_counts + momentum_counts

        detector = PatternDetector(mock_database, sample_config)

        with patch.object(detector, "_get_company_articles", return_value=[]):
            alerts = detector.detect_all_patterns()

        momentum_alerts = [a for a in alerts if a.pattern_type == "momentum"]
        assert len(momentum_alerts) == 0


class TestPatternDetectorNegativeCluster:
    """Tests for negative cluster detection."""

    def test_negative_cluster_detection(self, mock_database, sample_config, negative_articles):
        """Test detection of negative article cluster."""
        mock_database.get_mention_counts.return_value = [
            {"company_ticker": "AAPL", "company_name": "Apple", "count": 10}
        ]

        volume_counts = [1, 2, 28]
        momentum_counts = [28, 28, 28, 28, 28, 28, 28]
        mock_database.get_article_count_for_company.side_effect = volume_counts + momentum_counts

        detector = PatternDetector(mock_database, sample_config)

        def get_articles_side_effect(ticker, hours, exclude_hours=0):
            if hours == sample_config["windows"]["short"]:
                return negative_articles  # For negative cluster
            return []  # Other calls

        with patch.object(detector, "_get_company_articles", side_effect=get_articles_side_effect):
            alerts = detector.detect_all_patterns()

        negative_cluster_alerts = [a for a in alerts if a.pattern_type == "negative_cluster"]
        assert len(negative_cluster_alerts) >= 1

    def test_negative_cluster_high_severity(self, mock_database, sample_config, negative_articles):
        """Test that negative cluster has high severity."""
        mock_database.get_mention_counts.return_value = [
            {"company_ticker": "AAPL", "company_name": "Apple", "count": 10}
        ]

        volume_counts = [1, 2, 28]
        momentum_counts = [28, 28, 28, 28, 28, 28, 28]
        mock_database.get_article_count_for_company.side_effect = volume_counts + momentum_counts

        detector = PatternDetector(mock_database, sample_config)

        def get_articles_side_effect(ticker, hours, exclude_hours=0):
            if hours == sample_config["windows"]["short"]:
                return negative_articles
            return []

        with patch.object(detector, "_get_company_articles", side_effect=get_articles_side_effect):
            alerts = detector.detect_all_patterns()

        negative_cluster_alerts = [a for a in alerts if a.pattern_type == "negative_cluster"]
        assert all(a.severity == "high" for a in negative_cluster_alerts)

    def test_negative_cluster_keyword_extraction(self, mock_database, sample_config):
        """Test that negative keywords are extracted in cluster detection."""
        mock_database.get_mention_counts.return_value = [
            {"company_ticker": "AAPL", "company_name": "Apple", "count": 10}
        ]

        volume_counts = [1, 2, 28]
        momentum_counts = [28, 28, 28, 28, 28, 28, 28]
        mock_database.get_article_count_for_company.side_effect = volume_counts + momentum_counts

        # Articles with specific negative keywords
        articles_with_keywords = [
            {"id": 1, "content": "Major investigation into company fraud leads to scandal."},
            {"id": 2, "content": "Stock crash after investigation reveals problems."},
            {"id": 3, "content": "Layoffs announced amid investigation concerns."},
        ]

        detector = PatternDetector(mock_database, sample_config)

        def get_articles_side_effect(ticker, hours, exclude_hours=0):
            if hours == sample_config["windows"]["short"]:
                return articles_with_keywords
            return []

        with patch.object(detector, "_get_company_articles", side_effect=get_articles_side_effect):
            alerts = detector.detect_all_patterns()

        negative_cluster_alerts = [a for a in alerts if a.pattern_type == "negative_cluster"]
        if negative_cluster_alerts:
            keywords = negative_cluster_alerts[0].details.get("keywords", [])
            # Should extract keywords like investigation, fraud, scandal, layoffs
            assert any(
                kw in keywords for kw in ["investigation", "fraud", "scandal", "layoffs", "crash"]
            )

    def test_no_negative_cluster_when_not_majority_negative(
        self, mock_database, sample_config, positive_articles
    ):
        """Test no negative cluster when articles are mostly positive."""
        mock_database.get_mention_counts.return_value = [
            {"company_ticker": "AAPL", "company_name": "Apple", "count": 10}
        ]

        volume_counts = [1, 2, 28]
        momentum_counts = [28, 28, 28, 28, 28, 28, 28]
        mock_database.get_article_count_for_company.side_effect = volume_counts + momentum_counts

        detector = PatternDetector(mock_database, sample_config)

        def get_articles_side_effect(ticker, hours, exclude_hours=0):
            if hours == sample_config["windows"]["short"]:
                return positive_articles
            return []

        with patch.object(detector, "_get_company_articles", side_effect=get_articles_side_effect):
            alerts = detector.detect_all_patterns()

        negative_cluster_alerts = [a for a in alerts if a.pattern_type == "negative_cluster"]
        assert len(negative_cluster_alerts) == 0

    def test_not_enough_articles_for_negative_cluster(self, mock_database, sample_config):
        """Test no negative cluster when less than 2 articles."""
        mock_database.get_mention_counts.return_value = [
            {"company_ticker": "AAPL", "company_name": "Apple", "count": 10}
        ]

        volume_counts = [1, 2, 28]
        momentum_counts = [28, 28, 28, 28, 28, 28, 28]
        mock_database.get_article_count_for_company.side_effect = volume_counts + momentum_counts

        # Only 1 article
        single_article = [{"id": 1, "content": "Investigation and scandal."}]

        detector = PatternDetector(mock_database, sample_config)

        def get_articles_side_effect(ticker, hours, exclude_hours=0):
            if hours == sample_config["windows"]["short"]:
                return single_article
            return []

        with patch.object(detector, "_get_company_articles", side_effect=get_articles_side_effect):
            alerts = detector.detect_all_patterns()

        negative_cluster_alerts = [a for a in alerts if a.pattern_type == "negative_cluster"]
        assert len(negative_cluster_alerts) == 0


class TestPatternDetectorConfiguration:
    """Tests for PatternDetector configuration handling."""

    @pytest.fixture
    def minimal_sentiment_config(self):
        """Minimal config with sentiment keywords to allow PatternDetector init."""
        return {
            "sentiment_keywords": {"positive": ["good", "growth"], "negative": ["bad", "decline"]}
        }

    def test_default_windows(self, mock_database, minimal_sentiment_config):
        """Test default time windows when not configured."""
        config = minimal_sentiment_config.copy()
        mock_database.get_mention_counts.return_value = []

        detector = PatternDetector(mock_database, config)

        assert detector.windows["short"] == 6
        assert detector.windows["medium"] == 24
        assert detector.windows["long"] == 168

    def test_custom_windows(self, mock_database, minimal_sentiment_config):
        """Test custom time windows."""
        config = minimal_sentiment_config.copy()
        config["windows"] = {"short": 3, "medium": 12, "long": 72}
        mock_database.get_mention_counts.return_value = []

        detector = PatternDetector(mock_database, config)

        assert detector.windows["short"] == 3
        assert detector.windows["medium"] == 12
        assert detector.windows["long"] == 72

    def test_custom_volume_threshold(self, mock_database, minimal_sentiment_config):
        """Test custom volume spike threshold."""
        config = minimal_sentiment_config.copy()
        config["volume_spike_threshold"] = 5.0
        mock_database.get_mention_counts.return_value = []

        detector = PatternDetector(mock_database, config)

        assert detector.volume_spike_threshold == 5.0

    def test_custom_min_articles(self, mock_database, minimal_sentiment_config):
        """Test custom minimum articles for alert."""
        config = minimal_sentiment_config.copy()
        config["min_articles_for_alert"] = 5
        mock_database.get_mention_counts.return_value = []

        detector = PatternDetector(mock_database, config)

        assert detector.min_articles_for_alert == 5


class TestPatternDetectorIntegration:
    """Integration tests for PatternDetector."""

    def test_detect_all_patterns_no_companies(self, mock_database, sample_config):
        """Test detection with no companies in database."""
        mock_database.get_mention_counts.return_value = []

        detector = PatternDetector(mock_database, sample_config)
        alerts = detector.detect_all_patterns()

        assert len(alerts) == 0

    def test_multiple_companies_multiple_alerts(
        self, mock_database, sample_config, negative_articles, positive_articles
    ):
        """Test detection across multiple companies."""
        mock_database.get_mention_counts.return_value = [
            {"company_ticker": "AAPL", "company_name": "Apple", "count": 10},
            {"company_ticker": "TSLA", "company_name": "Tesla", "count": 10},
        ]

        # Each company needs 10 calls: 3 volume + 7 momentum
        # AAPL: volume spike
        aapl_volume = [10, 15, 28]
        aapl_momentum = [28, 28, 28, 28, 28, 28, 28]
        # TSLA: no volume spike
        tsla_volume = [1, 2, 20]
        tsla_momentum = [20, 20, 20, 20, 20, 20, 20]

        mock_database.get_article_count_for_company.side_effect = (
            aapl_volume + aapl_momentum + tsla_volume + tsla_momentum
        )

        detector = PatternDetector(mock_database, sample_config)

        call_count = [0]

        def get_articles_side_effect(ticker, hours, exclude_hours=0):
            call_count[0] += 1
            if ticker == "AAPL" and hours == sample_config["windows"]["short"]:
                return negative_articles
            elif ticker == "TSLA" and hours == sample_config["windows"]["short"]:
                return positive_articles
            return []

        with patch.object(detector, "_get_company_articles", side_effect=get_articles_side_effect):
            alerts = detector.detect_all_patterns()

        # Should have alerts for both companies
        aapl_alerts = [a for a in alerts if a.ticker == "AAPL"]
        tsla_alerts = [a for a in alerts if a.ticker == "TSLA"]

        # AAPL should have volume spike alert
        assert len(aapl_alerts) >= 1
        aapl_volume_alerts = [a for a in aapl_alerts if a.pattern_type == "volume_spike"]
        assert len(aapl_volume_alerts) >= 1
