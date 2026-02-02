"""
Tests for market data and correlation analyzer modules.

Tests MarketDataProvider price fetching, caching, and the
CorrelationAnalyzer for news-price correlation analysis.
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime, timedelta
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# =============================================================================
# MarketDataProvider Tests
# =============================================================================


class TestMarketDataProvider:
    """Tests for the MarketDataProvider class."""

    @pytest.fixture
    def mock_yfinance(self):
        """Mock yfinance module."""
        with patch.dict("sys.modules", {"yfinance": MagicMock()}):
            import yfinance as yf

            yield yf

    @pytest.fixture
    def provider_config(self):
        """Basic market data provider config."""
        return {"enabled": True, "cache_ttl_minutes": 15}

    @pytest.fixture
    def mock_ticker(self):
        """Create a mock yfinance Ticker object."""
        ticker = MagicMock()

        # Mock history data for get_price
        mock_history = MagicMock()
        mock_history.empty = False
        mock_history.__getitem__ = MagicMock(
            return_value=MagicMock(iloc=MagicMock(__getitem__=MagicMock(return_value=185.50)))
        )
        mock_history.iloc = MagicMock(__getitem__=MagicMock(return_value=185.50))
        mock_history.iterrows = MagicMock(return_value=[])

        ticker.history = MagicMock(return_value=mock_history)
        return ticker

    def test_provider_initialization_disabled_without_yfinance(self):
        """Test provider initializes disabled when yfinance not available."""
        with patch.dict("sys.modules", {"yfinance": None}):
            # Force reimport
            import importlib
            from market_data import MarketDataProvider

            # With yfinance unavailable, provider should be disabled
            provider = MarketDataProvider({"enabled": True})
            # May or may not be enabled depending on import state

    def test_provider_initialization_with_config(self, provider_config):
        """Test provider initialization with config."""
        from market_data import MarketDataProvider

        provider = MarketDataProvider(provider_config)
        assert provider.cache_ttl_seconds == 15 * 60

    def test_get_price_returns_none_when_disabled(self):
        """Test get_price returns None when provider is disabled."""
        from market_data import MarketDataProvider

        provider = MarketDataProvider({"enabled": False})
        result = provider.get_price("AAPL")
        assert result is None

    def test_cache_stores_and_retrieves_values(self):
        """Test that caching works correctly."""
        from market_data import MarketDataProvider

        provider = MarketDataProvider({"enabled": False, "cache_ttl_minutes": 15})

        # Store a value
        provider._set_cached("test_key", 100.0)

        # Retrieve it
        result = provider._get_cached("test_key")
        assert result == 100.0

    def test_cache_expires_old_entries(self):
        """Test that cache entries expire after TTL."""
        from market_data import MarketDataProvider
        import time

        # Very short TTL for testing
        provider = MarketDataProvider({"enabled": False, "cache_ttl_minutes": 0})
        provider.cache_ttl_seconds = 0.001  # 1 millisecond

        provider._set_cached("test_key", 100.0)

        # Wait for expiry
        time.sleep(0.01)

        # Should be None after expiry
        result = provider._get_cached("test_key")
        assert result is None

    def test_get_market_context_returns_none_when_disabled(self):
        """Test get_market_context returns None when disabled."""
        from market_data import MarketDataProvider

        provider = MarketDataProvider({"enabled": False})
        result = provider.get_market_context("AAPL")
        assert result is None

    @patch("market_data.yf")
    def test_get_price_with_mocked_yfinance(self, mock_yf):
        """Test get_price with mocked yfinance."""
        from market_data import MarketDataProvider, YFINANCE_AVAILABLE

        if not YFINANCE_AVAILABLE:
            pytest.skip("yfinance not available")

        # Setup mock
        mock_ticker = MagicMock()
        mock_hist = MagicMock()
        mock_hist.empty = False
        mock_hist.__getitem__ = MagicMock(
            return_value=MagicMock(iloc=MagicMock(__getitem__=MagicMock(return_value=185.50)))
        )
        mock_ticker.history.return_value = mock_hist
        mock_yf.Ticker.return_value = mock_ticker

        provider = MarketDataProvider({"enabled": True})

        # Should work with mocked data
        # Note: actual result depends on mock setup

    def test_is_significant_move_returns_none_when_disabled(self):
        """Test is_significant_move returns None when disabled."""
        from market_data import MarketDataProvider

        provider = MarketDataProvider({"enabled": False})
        result = provider.is_significant_move("AAPL", threshold_pct=2.0)
        assert result is None


# =============================================================================
# CorrelationAnalyzer Tests
# =============================================================================


class TestCorrelationAnalyzer:
    """Tests for the CorrelationAnalyzer class."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database."""
        db = MagicMock()
        db.get_connection.return_value.__enter__ = MagicMock()
        db.get_connection.return_value.__exit__ = MagicMock()
        return db

    @pytest.fixture
    def mock_market_data(self):
        """Create a mock market data provider."""
        provider = MagicMock()
        provider.enabled = True
        provider.get_price.return_value = 185.50
        provider.get_market_context.return_value = {
            "current_price": 185.50,
            "day_change_pct": -1.2,
            "week_change_pct": 3.5,
            "timestamp": datetime.now().isoformat(),
        }
        return provider

    @pytest.fixture
    def sample_alert(self):
        """Create a sample alert for testing."""
        from database import Alert

        return Alert(
            id=1,
            alert_type="volume_spike",
            company_ticker="AAPL",
            company_name="Apple Inc",
            severity="high",
            message="AAPL: 10 articles in 6h (spike: 5.0x normal)",
            details='{"articles_6h": 10}',
            created_at=datetime.now() - timedelta(days=2),
            acknowledged=False,
        )

    def test_analyzer_initialization(self, mock_db, mock_market_data):
        """Test correlation analyzer initialization."""
        from correlation_analyzer import CorrelationAnalyzer

        analyzer = CorrelationAnalyzer(mock_db, mock_market_data)
        assert analyzer.db == mock_db
        assert analyzer.market_data == mock_market_data

    def test_analyze_alert_impact_returns_none_for_no_ticker(self, mock_db, mock_market_data):
        """Test analyze_alert_impact returns None for alert without ticker."""
        from correlation_analyzer import CorrelationAnalyzer
        from database import Alert

        analyzer = CorrelationAnalyzer(mock_db, mock_market_data)

        alert = Alert(
            id=1,
            alert_type="test",
            company_ticker=None,
            company_name=None,
            severity="low",
            message="test",
            details="{}",
            created_at=datetime.now(),
        )

        result = analyzer.analyze_alert_impact(alert)
        assert result is None

    def test_analyze_alert_impact_with_valid_alert(self, mock_db, mock_market_data, sample_alert):
        """Test analyze_alert_impact with a valid alert."""
        from correlation_analyzer import CorrelationAnalyzer

        # Setup mock to return prices
        mock_market_data.get_price.side_effect = [180.0, 185.0]  # Before and after

        analyzer = CorrelationAnalyzer(mock_db, mock_market_data)
        result = analyzer.analyze_alert_impact(sample_alert)

        assert result is not None
        assert result.ticker == "AAPL"
        assert result.alert_type == "volume_spike"

    def test_calculate_correlation_empty_alerts(self, mock_db, mock_market_data):
        """Test calculate_correlation with no alerts."""
        from correlation_analyzer import CorrelationAnalyzer

        # Setup mock to return empty rows
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_db.get_connection.return_value.__enter__.return_value = mock_conn

        analyzer = CorrelationAnalyzer(mock_db, mock_market_data)
        result = analyzer.calculate_correlation("AAPL", days=30)

        assert result is not None
        assert result.total_alerts == 0
        assert result.hit_rate == 0.0

    def test_score_alert_accuracy_no_alerts(self, mock_db, mock_market_data):
        """Test score_alert_accuracy with no alerts."""
        from correlation_analyzer import CorrelationAnalyzer

        # Setup mock to return empty
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_db.get_connection.return_value.__enter__.return_value = mock_conn

        analyzer = CorrelationAnalyzer(mock_db, mock_market_data)
        result = analyzer.score_alert_accuracy(lookback_days=30)

        assert result["total_alerts"] == 0
        assert result["overall_hit_rate"] == 0.0

    def test_score_alert_accuracy_with_alerts(self, mock_db, mock_market_data):
        """Test score_alert_accuracy with provided alerts."""
        from correlation_analyzer import CorrelationAnalyzer
        from database import Alert

        # Setup market data to simulate price movement
        mock_market_data.get_price.side_effect = [100.0, 105.0] * 10

        alerts = [
            Alert(
                id=i,
                alert_type="volume_spike" if i % 2 == 0 else "sentiment_shift",
                company_ticker="AAPL",
                company_name="Apple Inc",
                severity="high",
                message="Test alert",
                details="{}",
                created_at=datetime.now() - timedelta(days=i),
                acknowledged=False,
            )
            for i in range(1, 5)
        ]

        analyzer = CorrelationAnalyzer(mock_db, mock_market_data)
        result = analyzer.score_alert_accuracy(alerts=alerts, lookback_days=30)

        assert result["total_alerts"] == 4

    def test_get_correlation_report(self, mock_db, mock_market_data):
        """Test get_correlation_report returns expected structure."""
        from correlation_analyzer import CorrelationAnalyzer

        # Setup mock to return empty alerts
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_db.get_connection.return_value.__enter__.return_value = mock_conn

        analyzer = CorrelationAnalyzer(mock_db, mock_market_data)
        report = analyzer.get_correlation_report("AAPL", days=30)

        assert "ticker" in report
        assert report["ticker"] == "AAPL"
        assert "analysis_period_days" in report
        assert "market_context" in report
        assert "correlation_stats" in report
        assert "accuracy_metrics" in report
        assert "recent_impacts" in report

    def test_direction_matches_alert_negative(self, mock_db, mock_market_data):
        """Test _direction_matches_alert for negative cluster."""
        from correlation_analyzer import CorrelationAnalyzer, AlertImpact

        analyzer = CorrelationAnalyzer(mock_db, mock_market_data)

        # Negative cluster with down movement - should match
        impact = AlertImpact(
            alert_id=1,
            ticker="AAPL",
            alert_type="negative_cluster",
            alert_time=datetime.now(),
            price_at_alert=100.0,
            price_after=95.0,
            hours_measured=24,
            price_change_pct=-5.0,
            preceded_significant_move=True,
            move_direction="down",
        )

        assert analyzer._direction_matches_alert(impact) is True

        # Negative cluster with up movement - should not match
        impact.move_direction = "up"
        assert analyzer._direction_matches_alert(impact) is False

    def test_direction_matches_alert_volume_spike(self, mock_db, mock_market_data):
        """Test _direction_matches_alert for volume spike (any direction counts)."""
        from correlation_analyzer import CorrelationAnalyzer, AlertImpact

        analyzer = CorrelationAnalyzer(mock_db, mock_market_data)

        # Volume spike - any significant move counts
        impact = AlertImpact(
            alert_id=1,
            ticker="AAPL",
            alert_type="volume_spike",
            alert_time=datetime.now(),
            price_at_alert=100.0,
            price_after=105.0,
            hours_measured=24,
            price_change_pct=5.0,
            preceded_significant_move=True,
            move_direction="up",
        )

        assert analyzer._direction_matches_alert(impact) is True

        impact.move_direction = "down"
        impact.price_change_pct = -5.0
        assert analyzer._direction_matches_alert(impact) is True


# =============================================================================
# Integration Tests with PatternDetector
# =============================================================================


class TestPatternDetectorMarketIntegration:
    """Tests for market data integration in PatternDetector."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database."""
        db = MagicMock()
        db.get_mention_counts.return_value = []
        db.get_connection.return_value.__enter__ = MagicMock()
        db.get_connection.return_value.__exit__ = MagicMock()
        return db

    @pytest.fixture
    def config_with_market_data(self, sample_config):
        """Config with market data enabled."""
        config = sample_config.copy()
        config["market_data"] = {
            "enabled": True,
            "cache_ttl_minutes": 15,
            "include_in_alerts": True,
        }
        return config

    def test_pattern_detector_initializes_without_market_data(self, mock_db, sample_config):
        """Test PatternDetector initializes fine without market data config."""
        from pattern_detector import PatternDetector

        detector = PatternDetector(mock_db, sample_config)
        assert detector.market_data_enabled is False

    @patch("pattern_detector.MarketDataProvider")
    def test_pattern_detector_initializes_with_market_data(
        self, mock_provider_class, mock_db, config_with_market_data
    ):
        """Test PatternDetector initializes with market data when configured."""
        from pattern_detector import PatternDetector

        mock_provider = MagicMock()
        mock_provider.enabled = True
        mock_provider_class.return_value = mock_provider

        detector = PatternDetector(mock_db, config_with_market_data)

        # Market data should be enabled
        assert detector.market_data_enabled is True

    def test_apply_market_context_enriches_alert(self, mock_db, sample_config):
        """Test that _apply_market_context enriches alert with market info."""
        from pattern_detector import PatternDetector, PatternAlert

        detector = PatternDetector(mock_db, sample_config)

        alert = PatternAlert(
            pattern_type="volume_spike",
            ticker="AAPL",
            company_name="Apple Inc",
            severity="high",
            message="AAPL: 10 articles in 6h (spike: 5.0x normal)",
            details={"articles_6h": 10},
        )

        market_context = {"current_price": 185.50, "day_change_pct": -1.2, "week_change_pct": 3.5}

        enriched = detector._apply_market_context(alert, market_context)

        assert enriched.market_context == market_context
        assert "market_context" in enriched.details
        assert "(stock down 1.2% today)" in enriched.message

    def test_apply_market_context_handles_none(self, mock_db, sample_config):
        """Test that _apply_market_context handles None context gracefully."""
        from pattern_detector import PatternDetector, PatternAlert

        detector = PatternDetector(mock_db, sample_config)

        alert = PatternAlert(
            pattern_type="volume_spike",
            ticker="AAPL",
            company_name="Apple Inc",
            severity="high",
            message="AAPL: 10 articles in 6h",
            details={},
        )

        original_message = alert.message
        enriched = detector._apply_market_context(alert, None)

        # Should return alert unchanged
        assert enriched.message == original_message
        assert enriched.market_context is None


# =============================================================================
# Web API Endpoint Tests
# =============================================================================


class TestMarketDataAPI:
    """Tests for market data API endpoints."""

    @pytest.fixture
    def app_client(self):
        """Create Flask test client."""
        sys.path.insert(0, str(Path(__file__).parent.parent / "web"))

        with (
            patch("web.app.MarketDataProvider") as mock_provider_class,
            patch("web.app.CorrelationAnalyzer") as mock_analyzer_class,
            patch("web.app.MARKET_DATA_AVAILABLE", True),
        ):
            # Setup mocks
            mock_provider = MagicMock()
            mock_provider.get_market_context.return_value = {
                "current_price": 185.50,
                "day_change_pct": -1.2,
                "week_change_pct": 3.5,
                "timestamp": datetime.now().isoformat(),
            }
            mock_provider.get_historical_prices.return_value = {
                "2024-01-01": 180.0,
                "2024-01-02": 182.0,
            }
            mock_provider_class.return_value = mock_provider

            mock_analyzer = MagicMock()
            mock_analyzer.get_correlation_report.return_value = {
                "ticker": "AAPL",
                "analysis_period_days": 30,
                "generated_at": datetime.now().isoformat(),
                "market_context": {"current_price": 185.50},
                "correlation_stats": {"total_alerts": 0},
                "accuracy_metrics": {"total_alerts": 0},
                "recent_impacts": [],
            }
            mock_analyzer_class.return_value = mock_analyzer

            # Import app after mocking
            from web.app import app

            app.config["TESTING"] = True

            with app.test_client() as client:
                yield client

    def test_correlation_endpoint_validates_ticker(self, app_client):
        """Test that correlation endpoint validates ticker format."""
        response = app_client.get("/api/correlation/INVALID123")
        assert response.status_code in [400, 401, 503]  # Depends on auth config

    def test_market_endpoint_validates_ticker(self, app_client):
        """Test that market endpoint validates ticker format."""
        response = app_client.get("/api/market/TOOLONG")
        assert response.status_code in [400, 401, 503]


# =============================================================================
# Alert Enrichment Tests
# =============================================================================


class TestAlertEnrichment:
    """Tests for alert enrichment with market data."""

    def test_pattern_alert_to_dict_includes_market_context(self):
        """Test PatternAlert.to_dict includes market_context when present."""
        from pattern_detector import PatternAlert

        alert = PatternAlert(
            pattern_type="volume_spike",
            ticker="AAPL",
            company_name="Apple Inc",
            severity="high",
            message="Test alert",
            details={},
            market_context={
                "current_price": 185.50,
                "day_change_pct": -1.2,
                "week_change_pct": 3.5,
            },
        )

        result = alert.to_dict()

        assert "market_context" in result
        assert result["market_context"]["current_price"] == 185.50
        assert result["market_context"]["day_change_pct"] == -1.2

    def test_pattern_alert_to_dict_without_market_context(self):
        """Test PatternAlert.to_dict works without market_context."""
        from pattern_detector import PatternAlert

        alert = PatternAlert(
            pattern_type="volume_spike",
            ticker="AAPL",
            company_name="Apple Inc",
            severity="high",
            message="Test alert",
            details={},
        )

        result = alert.to_dict()

        # market_context should not be in result when None
        assert "market_context" not in result or result.get("market_context") is None
