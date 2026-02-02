"""
Tests for alert aggregation and priority-based routing.

Tests AlertAggregator for grouping alerts by company,
time window expiration, and AlertManager routing by severity
and company-specific overrides.
"""

import pytest
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import sys
import time

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from alerts import (
    AlertManager,
    AlertAggregator,
    AggregatedAlert,
    DEFAULT_AGGREGATION_WINDOW,
)
from pattern_detector import PatternAlert
from database import Database, Alert


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_db():
    """Create a mock database."""
    db = MagicMock(spec=Database)
    db.save_alert.return_value = 1  # Return an alert ID
    db.get_unacknowledged_alerts.return_value = []
    db.get_connection.return_value.__enter__ = Mock()
    db.get_connection.return_value.__exit__ = Mock()
    return db


@pytest.fixture
def sample_alerts():
    """Create sample PatternAlerts for testing."""
    return [
        PatternAlert(
            pattern_type="volume_spike",
            ticker="AAPL",
            company_name="Apple Inc",
            severity="high",
            message="Apple Inc (AAPL): 10 articles in 6h (spike: 5.0x normal)",
            details={"articles_6h": 10, "spike_ratio": 5.0},
        ),
        PatternAlert(
            pattern_type="sentiment_shift",
            ticker="AAPL",
            company_name="Apple Inc",
            severity="medium",
            message="Apple Inc (AAPL): Positive sentiment shift",
            details={"direction": "positive", "shift": 0.6},
        ),
        PatternAlert(
            pattern_type="volume_spike",
            ticker="AAPL",
            company_name="Apple Inc",
            severity="medium",
            message="Apple Inc (AAPL): 8 articles in 6h",
            details={"articles_6h": 8},
        ),
        PatternAlert(
            pattern_type="momentum",
            ticker="MSFT",
            company_name="Microsoft",
            severity="low",
            message="Microsoft (MSFT): Building momentum",
            details={"daily_trend": [2, 3, 5]},
        ),
        PatternAlert(
            pattern_type="negative_cluster",
            ticker="TSLA",
            company_name="Tesla",
            severity="high",
            message="Tesla (TSLA): Negative news cluster",
            details={"negative_articles": 4, "total_articles": 5},
        ),
    ]


@pytest.fixture
def base_config():
    """Base configuration for AlertManager."""
    return {
        "console": True,
        "file": {"enabled": False, "path": "logs/alerts.log"},
        "telegram": {"enabled": False, "bot_token": "", "chat_id": ""},
        "webhook": {"enabled": False, "url": ""},
        "aggregation": {"enabled": False, "window_minutes": 30},
        "routing": {},
        "company_overrides": {},
    }


@pytest.fixture
def aggregation_config():
    """Configuration with aggregation enabled."""
    return {
        "console": True,
        "file": {"enabled": True, "path": "logs/alerts.log"},
        "telegram": {"enabled": False, "bot_token": "", "chat_id": ""},
        "webhook": {"enabled": False, "url": ""},
        "aggregation": {"enabled": True, "window_minutes": 30},
        "routing": {},
        "company_overrides": {},
    }


# =============================================================================
# AggregatedAlert Tests
# =============================================================================


class TestAggregatedAlert:
    """Tests for AggregatedAlert class."""

    def test_create_aggregated_alert(self, sample_alerts):
        """Test creating an aggregated alert."""
        agg = AggregatedAlert(ticker="AAPL", company_name="Apple Inc")

        assert agg.ticker == "AAPL"
        assert agg.company_name == "Apple Inc"
        assert agg.count == 0
        assert agg.alerts == []

    def test_add_alerts(self, sample_alerts):
        """Test adding alerts to aggregated group."""
        agg = AggregatedAlert(ticker="AAPL", company_name="Apple Inc")

        # Add AAPL alerts
        for alert in sample_alerts[:3]:  # First 3 are AAPL
            agg.add_alert(alert)

        assert agg.count == 3
        assert len(agg.alerts) == 3
        assert agg.first_alert_time is not None
        assert agg.last_alert_time is not None

    def test_highest_severity(self, sample_alerts):
        """Test determining highest severity."""
        agg = AggregatedAlert(ticker="AAPL", company_name="Apple Inc")

        # Add alerts with different severities
        agg.add_alert(sample_alerts[1])  # medium
        assert agg.highest_severity == "medium"

        agg.add_alert(sample_alerts[0])  # high
        assert agg.highest_severity == "high"

        agg.add_alert(sample_alerts[2])  # medium
        assert agg.highest_severity == "high"

    def test_type_counts(self, sample_alerts):
        """Test getting alert type counts."""
        agg = AggregatedAlert(ticker="AAPL", company_name="Apple Inc")

        for alert in sample_alerts[:3]:
            agg.add_alert(alert)

        counts = agg.get_type_counts()

        assert counts["volume_spike"] == 2
        assert counts["sentiment_shift"] == 1

    def test_summary_message(self, sample_alerts):
        """Test generating summary message."""
        agg = AggregatedAlert(ticker="AAPL", company_name="Apple Inc")

        for alert in sample_alerts[:3]:
            agg.add_alert(alert)

        message = agg.to_summary_message()

        assert "AAPL:" in message
        assert "3 alerts" in message
        assert "volume spike" in message
        assert "sentiment shift" in message

    def test_to_pattern_alert(self, sample_alerts):
        """Test converting to PatternAlert."""
        agg = AggregatedAlert(ticker="AAPL", company_name="Apple Inc")

        for alert in sample_alerts[:3]:
            agg.add_alert(alert)

        pattern_alert = agg.to_pattern_alert()

        assert pattern_alert.pattern_type == "aggregated"
        assert pattern_alert.ticker == "AAPL"
        assert pattern_alert.company_name == "Apple Inc"
        assert pattern_alert.severity == "high"  # Highest among alerts
        assert pattern_alert.details["alert_count"] == 3
        assert "type_breakdown" in pattern_alert.details
        assert "individual_alerts" in pattern_alert.details


# =============================================================================
# AlertAggregator Tests
# =============================================================================


class TestAlertAggregator:
    """Tests for AlertAggregator class."""

    def test_aggregation_disabled(self, sample_alerts):
        """Test that alerts are returned immediately when aggregation is disabled."""
        config = {"aggregation": {"enabled": False}}
        aggregator = AlertAggregator(config)

        result = aggregator.add_alert(sample_alerts[0])

        assert result is not None
        assert len(result) == 1
        assert result[0] == sample_alerts[0]
        assert aggregator.get_pending_count() == 0

    def test_aggregation_enabled_groups_alerts(self, sample_alerts):
        """Test that alerts are grouped when aggregation is enabled."""
        config = {"aggregation": {"enabled": True, "window_minutes": 30}}
        aggregator = AlertAggregator(config)

        # Add first alert - should be pending
        result = aggregator.add_alert(sample_alerts[0])
        assert result is None
        assert aggregator.get_pending_count() == 1

        # Add second AAPL alert - should be pending
        result = aggregator.add_alert(sample_alerts[1])
        assert result is None
        assert aggregator.get_pending_count() == 2

    def test_aggregation_groups_by_ticker(self, sample_alerts):
        """Test that alerts are grouped by ticker."""
        config = {"aggregation": {"enabled": True, "window_minutes": 30}}
        aggregator = AlertAggregator(config)

        # Add AAPL alert
        aggregator.add_alert(sample_alerts[0])
        # Add MSFT alert
        aggregator.add_alert(sample_alerts[3])
        # Add TSLA alert
        aggregator.add_alert(sample_alerts[4])

        assert aggregator.get_pending_count() == 3
        assert set(aggregator.get_pending_tickers()) == {"AAPL", "MSFT", "TSLA"}

    def test_flush_ticker(self, sample_alerts):
        """Test flushing alerts for a specific ticker."""
        config = {"aggregation": {"enabled": True, "window_minutes": 30}}
        aggregator = AlertAggregator(config)

        # Add multiple AAPL alerts
        aggregator.add_alert(sample_alerts[0])
        aggregator.add_alert(sample_alerts[1])
        aggregator.add_alert(sample_alerts[2])

        # Add MSFT alert
        aggregator.add_alert(sample_alerts[3])

        # Flush AAPL
        flushed = aggregator.flush_ticker("AAPL")

        # Should return single aggregated alert
        assert len(flushed) == 1
        assert flushed[0].pattern_type == "aggregated"
        assert flushed[0].ticker == "AAPL"
        assert flushed[0].details["alert_count"] == 3

        # MSFT should still be pending
        assert aggregator.get_pending_count() == 1
        assert "MSFT" in aggregator.get_pending_tickers()

    def test_flush_single_alert_returns_original(self, sample_alerts):
        """Test that flushing a single alert returns original, not aggregated."""
        config = {"aggregation": {"enabled": True, "window_minutes": 30}}
        aggregator = AlertAggregator(config)

        # Add single alert
        aggregator.add_alert(sample_alerts[3])  # MSFT

        flushed = aggregator.flush_ticker("MSFT")

        # Should return original alert, not aggregated
        assert len(flushed) == 1
        assert flushed[0].pattern_type == "momentum"  # Original type
        assert flushed[0].ticker == "MSFT"

    def test_flush_all(self, sample_alerts):
        """Test flushing all pending alerts."""
        config = {"aggregation": {"enabled": True, "window_minutes": 30}}
        aggregator = AlertAggregator(config)

        # Add alerts for multiple companies
        aggregator.add_alert(sample_alerts[0])  # AAPL
        aggregator.add_alert(sample_alerts[1])  # AAPL
        aggregator.add_alert(sample_alerts[3])  # MSFT
        aggregator.add_alert(sample_alerts[4])  # TSLA

        flushed = aggregator.flush_all()

        # Should return 3 alerts (1 aggregated AAPL, 1 MSFT, 1 TSLA)
        assert len(flushed) == 3
        assert aggregator.get_pending_count() == 0

        # AAPL should be aggregated
        aapl_alert = next(a for a in flushed if a.ticker == "AAPL")
        assert aapl_alert.pattern_type == "aggregated"
        assert aapl_alert.details["alert_count"] == 2

    def test_flush_empty_returns_empty(self):
        """Test flushing when no alerts are pending."""
        config = {"aggregation": {"enabled": True, "window_minutes": 30}}
        aggregator = AlertAggregator(config)

        flushed = aggregator.flush_all()

        assert flushed == []

    def test_flush_nonexistent_ticker(self):
        """Test flushing a ticker that doesn't exist."""
        config = {"aggregation": {"enabled": True, "window_minutes": 30}}
        aggregator = AlertAggregator(config)

        flushed = aggregator.flush_ticker("NONEXISTENT")

        assert flushed == []

    def test_window_expiration(self, sample_alerts):
        """Test that expired windows trigger flush on next add."""
        config = {"aggregation": {"enabled": True, "window_minutes": 1}}  # 1 minute window
        aggregator = AlertAggregator(config)

        # Add first AAPL alert
        aggregator.add_alert(sample_alerts[0])
        assert aggregator.get_pending_count() == 1

        # Manually set the group start time to the past
        aggregator._group_start_times["AAPL"] = datetime.now() - timedelta(minutes=2)

        # Add another AAPL alert - should flush expired and start new group
        result = aggregator.add_alert(sample_alerts[1])

        # Should return the expired alert
        assert result is not None
        assert len(result) == 1
        assert result[0].ticker == "AAPL"

        # New alert should be pending
        assert aggregator.get_pending_count() == 1

    def test_flush_expired(self, sample_alerts):
        """Test flush_expired only flushes expired groups."""
        config = {"aggregation": {"enabled": True, "window_minutes": 1}}
        aggregator = AlertAggregator(config)

        # Add alerts for two companies
        aggregator.add_alert(sample_alerts[0])  # AAPL
        aggregator.add_alert(sample_alerts[3])  # MSFT

        # Make AAPL expired
        aggregator._group_start_times["AAPL"] = datetime.now() - timedelta(minutes=2)
        # MSFT is still fresh

        flushed = aggregator.flush_expired()

        # Only AAPL should be flushed
        assert len(flushed) == 1
        assert flushed[0].ticker == "AAPL"

        # MSFT should still be pending
        assert aggregator.get_pending_count() == 1
        assert "MSFT" in aggregator.get_pending_tickers()

    def test_default_window_minutes(self):
        """Test default aggregation window is used when not configured."""
        config = {"aggregation": {"enabled": True}}
        aggregator = AlertAggregator(config)

        assert aggregator.window_minutes == DEFAULT_AGGREGATION_WINDOW


# =============================================================================
# AlertManager Routing Tests
# =============================================================================


class TestAlertRouting:
    """Tests for AlertManager priority-based routing."""

    def test_default_routing_all_enabled_channels(self, mock_db, base_config, sample_alerts):
        """Test default routing returns all enabled channels."""
        base_config["console"] = True
        base_config["file"]["enabled"] = True
        base_config["telegram"]["enabled"] = True
        base_config["telegram"]["bot_token"] = "token"
        base_config["telegram"]["chat_id"] = "chat"
        base_config["webhook"]["enabled"] = True
        base_config["webhook"]["url"] = "http://example.com"

        with patch.dict(os.environ, {}, clear=True):
            manager = AlertManager(base_config, mock_db)

        channels = manager.get_channels_for_alert(sample_alerts[0])

        assert set(channels) == {"console", "file", "telegram", "webhook"}

    def test_severity_routing_high(self, mock_db, base_config, sample_alerts):
        """Test routing high severity alerts."""
        base_config["routing"] = {
            "high_severity": ["telegram", "webhook"],
            "medium_severity": ["webhook", "file"],
            "low_severity": ["file"],
        }

        manager = AlertManager(base_config, mock_db)

        # High severity alert
        channels = manager.get_channels_for_alert(sample_alerts[0])  # high

        assert channels == ["telegram", "webhook"]

    def test_severity_routing_medium(self, mock_db, base_config, sample_alerts):
        """Test routing medium severity alerts."""
        base_config["routing"] = {
            "high_severity": ["telegram", "webhook"],
            "medium_severity": ["webhook", "file"],
            "low_severity": ["file"],
        }

        manager = AlertManager(base_config, mock_db)

        # Medium severity alert
        channels = manager.get_channels_for_alert(sample_alerts[1])  # medium

        assert channels == ["webhook", "file"]

    def test_severity_routing_low(self, mock_db, base_config, sample_alerts):
        """Test routing low severity alerts."""
        base_config["routing"] = {
            "high_severity": ["telegram", "webhook"],
            "medium_severity": ["webhook", "file"],
            "low_severity": ["file"],
        }

        manager = AlertManager(base_config, mock_db)

        # Low severity alert
        channels = manager.get_channels_for_alert(sample_alerts[3])  # low

        assert channels == ["file"]

    def test_company_override_takes_precedence(self, mock_db, base_config, sample_alerts):
        """Test that company overrides take precedence over severity routing."""
        base_config["routing"] = {
            "high_severity": ["telegram", "webhook"],
            "medium_severity": ["webhook", "file"],
        }
        base_config["company_overrides"] = {
            "AAPL": {"channels": ["console", "file"]},
        }

        manager = AlertManager(base_config, mock_db)

        # AAPL high severity alert - should use company override
        channels = manager.get_channels_for_alert(sample_alerts[0])

        assert channels == ["console", "file"]

    def test_company_override_does_not_affect_other_companies(
        self, mock_db, base_config, sample_alerts
    ):
        """Test that company overrides only affect specified companies."""
        base_config["routing"] = {
            "high_severity": ["telegram", "webhook"],
            "low_severity": ["file"],
        }
        base_config["company_overrides"] = {
            "AAPL": {"channels": ["console"]},
        }

        manager = AlertManager(base_config, mock_db)

        # MSFT alert should use severity routing, not AAPL override
        channels = manager.get_channels_for_alert(sample_alerts[3])  # MSFT low

        assert channels == ["file"]

    def test_partial_routing_config(self, mock_db, base_config, sample_alerts):
        """Test that missing severity in routing falls back to enabled channels."""
        base_config["console"] = True
        base_config["file"]["enabled"] = True
        base_config["routing"] = {
            "high_severity": ["telegram"],
            # medium and low not configured
        }

        manager = AlertManager(base_config, mock_db)

        # High severity - use routing
        high_channels = manager.get_channels_for_alert(sample_alerts[0])
        assert high_channels == ["telegram"]

        # Low severity - fall back to enabled channels
        low_channels = manager.get_channels_for_alert(sample_alerts[3])
        assert "console" in low_channels
        assert "file" in low_channels


# =============================================================================
# AlertManager with Aggregation Tests
# =============================================================================


class TestAlertManagerAggregation:
    """Tests for AlertManager with aggregation enabled."""

    def test_send_alerts_with_aggregation_disabled(
        self, mock_db, base_config, sample_alerts, capsys
    ):
        """Test that alerts are sent immediately when aggregation is disabled."""
        base_config["console"] = True

        manager = AlertManager(base_config, mock_db)

        # Send 3 AAPL alerts
        manager.send_alerts(sample_alerts[:3])

        # All 3 should be saved to database
        assert mock_db.save_alert.call_count == 3

        # Check console output
        captured = capsys.readouterr()
        assert "volume_spike" in captured.out

    def test_send_alerts_with_aggregation_enabled(
        self, mock_db, aggregation_config, sample_alerts, capsys, tmp_path
    ):
        """Test that alerts are aggregated when enabled."""
        aggregation_config["file"]["path"] = str(tmp_path / "alerts.log")

        manager = AlertManager(aggregation_config, mock_db)

        # Send 3 AAPL alerts with flush=True
        manager.send_alerts(sample_alerts[:3], flush=True)

        # Only 1 aggregated alert should be saved to database
        assert mock_db.save_alert.call_count == 1

        # The saved alert should be aggregated
        saved_alert = mock_db.save_alert.call_args[0][0]
        assert saved_alert.alert_type == "aggregated"
        details = json.loads(saved_alert.details)
        assert details["alert_count"] == 3

    def test_send_alerts_no_flush(self, mock_db, aggregation_config, sample_alerts):
        """Test that alerts remain pending when flush=False."""
        manager = AlertManager(aggregation_config, mock_db)

        # Send alerts without flushing
        manager.send_alerts(sample_alerts[:2], flush=False)

        # Nothing should be saved yet
        assert mock_db.save_alert.call_count == 0

        # Alerts should be pending
        assert manager.aggregator.get_pending_count() == 2

    def test_flush_aggregated_alerts(self, mock_db, aggregation_config, sample_alerts):
        """Test manual flushing of aggregated alerts."""
        manager = AlertManager(aggregation_config, mock_db)

        # Add alerts without flushing
        manager.send_alerts(sample_alerts[:2], flush=False)

        assert mock_db.save_alert.call_count == 0

        # Manual flush
        count = manager.flush_aggregated_alerts()

        assert count == 1  # One aggregated alert
        assert mock_db.save_alert.call_count == 1

    def test_routing_with_aggregated_alert(
        self, mock_db, aggregation_config, sample_alerts, capsys, tmp_path
    ):
        """Test that routing works correctly with aggregated alerts."""
        aggregation_config["file"]["path"] = str(tmp_path / "alerts.log")
        aggregation_config["routing"] = {
            "high_severity": ["console", "file"],
            "medium_severity": ["file"],
        }

        manager = AlertManager(aggregation_config, mock_db)

        # Send AAPL alerts (mix of high and medium) - aggregated alert should be high
        manager.send_alerts(sample_alerts[:3], flush=True)

        # Check that console was used (high severity routing)
        captured = capsys.readouterr()
        assert "AAPL" in captured.out

    def test_multiple_companies_aggregation(self, mock_db, aggregation_config, sample_alerts):
        """Test aggregation with alerts from multiple companies."""
        manager = AlertManager(aggregation_config, mock_db)

        # Send all sample alerts
        manager.send_alerts(sample_alerts, flush=True)

        # Should have 3 alerts saved: AAPL (aggregated), MSFT, TSLA
        assert mock_db.save_alert.call_count == 3

        # Verify AAPL was aggregated
        calls = mock_db.save_alert.call_args_list
        aapl_call = next(c for c in calls if c[0][0].company_ticker == "AAPL")
        aapl_alert = aapl_call[0][0]
        assert aapl_alert.alert_type == "aggregated"


# =============================================================================
# Integration Tests
# =============================================================================


class TestIntegration:
    """Integration tests combining aggregation and routing."""

    @patch("requests.post")
    def test_full_pipeline_with_routing_and_aggregation(
        self, mock_post, mock_db, sample_alerts, tmp_path
    ):
        """Test full alert pipeline with aggregation and routing."""
        config = {
            "console": False,
            "file": {"enabled": True, "path": str(tmp_path / "alerts.log")},
            "telegram": {"enabled": True, "bot_token": "token", "chat_id": "chat"},
            "webhook": {"enabled": True, "url": "http://example.com/webhook"},
            "aggregation": {"enabled": True, "window_minutes": 30},
            "routing": {
                "high_severity": ["telegram", "webhook", "file"],
                "medium_severity": ["webhook", "file"],
                "low_severity": ["file"],
            },
            "company_overrides": {
                "TSLA": {"channels": ["telegram"]},  # TSLA only to telegram
            },
        }

        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        with patch.dict(os.environ, {}, clear=True):
            manager = AlertManager(config, mock_db)

        # Send all alerts
        manager.send_alerts(sample_alerts, flush=True)

        # Verify database saves
        assert mock_db.save_alert.call_count == 3

        # Verify webhook calls - should have AAPL (high) and possibly others
        webhook_calls = [c for c in mock_post.call_args_list if "webhook" in str(c)]

        # Verify file was written
        assert (tmp_path / "alerts.log").exists()

    def test_backwards_compatibility_no_new_config(self, mock_db, sample_alerts, capsys):
        """Test that everything works when new config options are not present."""
        # Minimal config without aggregation or routing
        config = {
            "console": True,
            "file": {"enabled": False},
            "telegram": {"enabled": False},
            "webhook": {"enabled": False},
        }

        manager = AlertManager(config, mock_db)

        # Send alerts - should work normally
        manager.send_alerts(sample_alerts[:2])

        # Both alerts should be processed
        assert mock_db.save_alert.call_count == 2

        # Console should have output
        captured = capsys.readouterr()
        assert "AAPL" in captured.out or "volume_spike" in captured.out

    def test_empty_routing_falls_back_to_enabled(self, mock_db, sample_alerts, capsys):
        """Test that empty routing config falls back to enabled channels."""
        config = {
            "console": True,
            "file": {"enabled": False},
            "telegram": {"enabled": False},
            "webhook": {"enabled": False},
            "routing": {},  # Empty routing
            "aggregation": {"enabled": False},
        }

        manager = AlertManager(config, mock_db)

        channels = manager.get_channels_for_alert(sample_alerts[0])

        # Should return console (the only enabled channel)
        assert channels == ["console"]
