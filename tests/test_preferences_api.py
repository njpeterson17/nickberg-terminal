"""
Tests for the preferences API endpoints.

Tests GET/POST for preferences, watchlist, and alert-rules endpoints.
"""

import pytest
import json
import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_database():
    """Create a mock database with preference methods."""
    db = MagicMock()

    # Default return values
    db.get_stats.return_value = {
        "total_articles": 100,
        "total_mentions": 500,
        "total_alerts": 10,
        "articles_24h": 25,
    }

    db.get_unacknowledged_alerts.return_value = []
    db.get_recent_articles.return_value = []
    db.get_mention_counts.return_value = []

    # Preference methods
    db.get_all_preferences.return_value = {}
    db.get_preference.return_value = None
    db.save_preference.return_value = True
    db.delete_preference.return_value = True

    # Mock connection context manager
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchall.return_value = []
    db.get_connection.return_value.__enter__ = Mock(return_value=mock_conn)
    db.get_connection.return_value.__exit__ = Mock(return_value=False)

    return db


@pytest.fixture
def mock_config():
    """Create a mock configuration."""
    return {
        "database": {"path": "data/test.db"},
        "companies": {
            "watchlist": {"AAPL": ["Apple", "Apple Inc"], "GOOGL": ["Google", "Alphabet"]}
        },
        "sources": {"reuters": {"enabled": True}, "bloomberg": {"enabled": False}},
        "patterns": {"volume_spike_threshold": 3.0, "min_articles_for_alert": 3},
        "alerts": {
            "console": True,
            "file": {"enabled": True, "path": "logs/alerts.log"},
            "telegram": {"enabled": False},
            "webhook": {"enabled": False},
        },
    }


@pytest.fixture
def app_and_db(mock_database, mock_config, tmp_path):
    """Create a Flask test application with mocked database."""
    config_path = tmp_path / "config" / "settings.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)

    import yaml

    with open(config_path, "w") as f:
        yaml.dump(mock_config, f)

    # Clean up any existing module import
    if "web.app" in sys.modules:
        del sys.modules["web.app"]

    with patch.dict(os.environ, {"NICKBERG_API_KEY": ""}, clear=False):
        with patch("web.app.CONFIG_PATH", config_path):
            with patch("web.app.Database") as MockDatabase:
                MockDatabase.return_value = mock_database

                from web.app import app as flask_app

                flask_app.config["TESTING"] = True

                with patch("web.app.db", mock_database):
                    with patch("web.app.config", mock_config):
                        yield flask_app, mock_database


@pytest.fixture
def client(app_and_db):
    """Create a test client."""
    app, _ = app_and_db
    return app.test_client()


@pytest.fixture
def client_and_db(app_and_db):
    """Create a test client and return both client and mock db."""
    app, mock_db = app_and_db
    return app.test_client(), mock_db


# =============================================================================
# Preferences Endpoint Tests
# =============================================================================


class TestGetPreferences:
    """Tests for GET /api/preferences endpoint."""

    def test_get_preferences_returns_json(self, client_and_db):
        """Test that preferences endpoint returns JSON."""
        client, mock_db = client_and_db
        mock_db.get_all_preferences.return_value = {}

        response = client.get("/api/preferences")

        assert response.status_code == 200
        assert response.content_type == "application/json"

    def test_get_preferences_returns_defaults(self, client_and_db):
        """Test that preferences returns defaults when no stored preferences."""
        client, mock_db = client_and_db
        mock_db.get_all_preferences.return_value = {}

        response = client.get("/api/preferences")
        data = json.loads(response.data)

        assert "alert_channels" in data
        assert "severity_routing" in data
        assert "thresholds" in data
        assert "company_preferences" in data

    def test_get_preferences_merges_stored(self, client_and_db):
        """Test that stored preferences override defaults."""
        client, mock_db = client_and_db
        mock_db.get_all_preferences.return_value = {"thresholds": {"volume_spike": 5.0}}

        response = client.get("/api/preferences")
        data = json.loads(response.data)

        assert data["thresholds"]["volume_spike"] == 5.0


class TestSavePreferences:
    """Tests for POST /api/preferences endpoint."""

    def test_save_preferences_returns_success(self, client_and_db):
        """Test saving preferences returns success."""
        client, mock_db = client_and_db
        mock_db.save_preference.return_value = True

        response = client.post(
            "/api/preferences",
            data=json.dumps({"thresholds": {"volume_spike": 4.0}}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["success"] is True

    def test_save_preferences_no_data(self, client_and_db):
        """Test saving preferences with no data returns error."""
        client, mock_db = client_and_db

        response = client.post(
            "/api/preferences", data=json.dumps(None), content_type="application/json"
        )

        assert response.status_code == 400

    def test_save_preferences_validates_volume_spike(self, client_and_db):
        """Test that volume_spike threshold is validated."""
        client, mock_db = client_and_db

        # Test too low
        response = client.post(
            "/api/preferences",
            data=json.dumps({"thresholds": {"volume_spike": 0.5}}),
            content_type="application/json",
        )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert "volume_spike" in str(data.get("errors", []))

    def test_save_preferences_validates_min_articles(self, client_and_db):
        """Test that min_articles is validated."""
        client, mock_db = client_and_db

        # Test too high
        response = client.post(
            "/api/preferences",
            data=json.dumps({"thresholds": {"min_articles": 100}}),
            content_type="application/json",
        )

        assert response.status_code == 400

    def test_save_preferences_validates_sentiment_shift(self, client_and_db):
        """Test that sentiment_shift threshold is validated."""
        client, mock_db = client_and_db

        # Test too high
        response = client.post(
            "/api/preferences",
            data=json.dumps({"thresholds": {"sentiment_shift": 1.5}}),
            content_type="application/json",
        )

        assert response.status_code == 400


# =============================================================================
# Watchlist Endpoint Tests
# =============================================================================


class TestGetWatchlist:
    """Tests for GET /api/watchlist endpoint."""

    def test_get_watchlist_returns_json(self, client_and_db):
        """Test that watchlist endpoint returns JSON."""
        client, mock_db = client_and_db
        mock_db.get_preference.return_value = None

        response = client.get("/api/watchlist")

        assert response.status_code == 200
        assert response.content_type == "application/json"

    def test_get_watchlist_returns_db_watchlist(self, client_and_db):
        """Test that database watchlist takes precedence."""
        client, mock_db = client_and_db
        mock_db.get_preference.return_value = {"MSFT": ["Microsoft"]}

        response = client.get("/api/watchlist")
        data = json.loads(response.data)

        assert "MSFT" in data
        assert data["MSFT"] == ["Microsoft"]


class TestUpdateWatchlist:
    """Tests for POST /api/watchlist endpoint."""

    def test_add_ticker(self, client_and_db):
        """Test adding a ticker to watchlist."""
        client, mock_db = client_and_db
        mock_db.get_preference.return_value = {}
        mock_db.save_preference.return_value = True

        response = client.post(
            "/api/watchlist",
            data=json.dumps(
                {"action": "add", "ticker": "NVDA", "names": ["Nvidia", "NVIDIA Corporation"]}
            ),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["success"] is True
        assert "NVDA" in data["watchlist"]

    def test_add_ticker_validates_format(self, client_and_db):
        """Test that ticker format is validated."""
        client, mock_db = client_and_db

        # Test invalid ticker (numbers)
        response = client.post(
            "/api/watchlist",
            data=json.dumps({"action": "add", "ticker": "123", "names": ["Test"]}),
            content_type="application/json",
        )

        assert response.status_code == 400

    def test_add_ticker_requires_names(self, client_and_db):
        """Test that names are required."""
        client, mock_db = client_and_db

        response = client.post(
            "/api/watchlist",
            data=json.dumps({"action": "add", "ticker": "AAPL", "names": []}),
            content_type="application/json",
        )

        assert response.status_code == 400

    def test_remove_ticker(self, client_and_db):
        """Test removing a ticker from watchlist."""
        client, mock_db = client_and_db
        mock_db.get_preference.return_value = {"AAPL": ["Apple"], "GOOGL": ["Google"]}
        mock_db.save_preference.return_value = True

        response = client.post(
            "/api/watchlist",
            data=json.dumps({"action": "remove", "ticker": "AAPL"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["success"] is True
        assert "AAPL" not in data["watchlist"]

    def test_remove_nonexistent_ticker(self, client_and_db):
        """Test removing a ticker that doesn't exist."""
        client, mock_db = client_and_db
        mock_db.get_preference.return_value = {"AAPL": ["Apple"]}

        response = client.post(
            "/api/watchlist",
            data=json.dumps({"action": "remove", "ticker": "XXXX"}),
            content_type="application/json",
        )

        assert response.status_code == 404

    def test_replace_watchlist(self, client_and_db):
        """Test replacing entire watchlist."""
        client, mock_db = client_and_db
        mock_db.save_preference.return_value = True

        new_watchlist = {"TSLA": ["Tesla"], "AMZN": ["Amazon"]}

        response = client.post(
            "/api/watchlist",
            data=json.dumps({"action": "replace", "watchlist": new_watchlist}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["success"] is True
        assert data["watchlist"] == new_watchlist

    def test_invalid_action(self, client_and_db):
        """Test invalid action returns error."""
        client, mock_db = client_and_db

        response = client.post(
            "/api/watchlist",
            data=json.dumps({"action": "invalid"}),
            content_type="application/json",
        )

        assert response.status_code == 400


# =============================================================================
# Alert Rules Endpoint Tests
# =============================================================================


class TestGetAlertRules:
    """Tests for GET /api/alert-rules endpoint."""

    def test_get_alert_rules_returns_json(self, client_and_db):
        """Test that alert-rules endpoint returns JSON."""
        client, mock_db = client_and_db
        mock_db.get_preference.return_value = None

        response = client.get("/api/alert-rules")

        assert response.status_code == 200
        assert response.content_type == "application/json"

    def test_get_alert_rules_structure(self, client_and_db):
        """Test that alert rules has correct structure."""
        client, mock_db = client_and_db
        mock_db.get_preference.return_value = None

        response = client.get("/api/alert-rules")
        data = json.loads(response.data)

        assert "alert_channels" in data
        assert "severity_routing" in data
        assert "company_preferences" in data


class TestUpdateAlertRules:
    """Tests for POST /api/alert-rules endpoint."""

    def test_update_alert_channels(self, client_and_db):
        """Test updating alert channels."""
        client, mock_db = client_and_db
        mock_db.save_preference.return_value = True

        response = client.post(
            "/api/alert-rules",
            data=json.dumps(
                {
                    "alert_channels": {
                        "console": True,
                        "file": True,
                        "telegram": False,
                        "webhook": False,
                    }
                }
            ),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["success"] is True
        assert "alert_channels" in data["saved"]

    def test_update_severity_routing(self, client_and_db):
        """Test updating severity routing."""
        client, mock_db = client_and_db
        mock_db.save_preference.return_value = True

        response = client.post(
            "/api/alert-rules",
            data=json.dumps(
                {
                    "severity_routing": {
                        "high": ["telegram", "file", "console"],
                        "medium": ["file", "console"],
                        "low": ["file"],
                    }
                }
            ),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["success"] is True
        assert "severity_routing" in data["saved"]

    def test_update_company_preferences(self, client_and_db):
        """Test updating company preferences."""
        client, mock_db = client_and_db
        mock_db.save_preference.return_value = True

        response = client.post(
            "/api/alert-rules",
            data=json.dumps(
                {
                    "company_preferences": {
                        "AAPL": {"muted": False, "priority": "high"},
                        "MSFT": {"muted": True, "priority": "normal"},
                    }
                }
            ),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["success"] is True
        assert "company_preferences" in data["saved"]

    def test_invalid_channel(self, client_and_db):
        """Test that invalid channels are rejected."""
        client, mock_db = client_and_db

        response = client.post(
            "/api/alert-rules",
            data=json.dumps({"alert_channels": {"invalid_channel": True}}),
            content_type="application/json",
        )

        # Should still return 400 due to validation error
        data = json.loads(response.data)
        assert "errors" in data

    def test_invalid_severity(self, client_and_db):
        """Test that invalid severities are rejected."""
        client, mock_db = client_and_db

        response = client.post(
            "/api/alert-rules",
            data=json.dumps(
                {
                    "severity_routing": {
                        "critical": ["telegram"]  # Invalid severity
                    }
                }
            ),
            content_type="application/json",
        )

        data = json.loads(response.data)
        assert "errors" in data


# =============================================================================
# Database Preference Methods Tests
# =============================================================================


class TestDatabasePreferenceMethods:
    """Tests for database preference methods."""

    def test_save_preference_called(self, client_and_db):
        """Test that save_preference is called correctly."""
        client, mock_db = client_and_db
        mock_db.save_preference.return_value = True

        client.post(
            "/api/preferences",
            data=json.dumps({"thresholds": {"volume_spike": 4.0}}),
            content_type="application/json",
        )

        mock_db.save_preference.assert_called()

    def test_get_preference_called(self, client_and_db):
        """Test that get_preference is called correctly."""
        client, mock_db = client_and_db

        client.get("/api/watchlist")

        mock_db.get_preference.assert_called_with("watchlist")

    def test_get_all_preferences_called(self, client_and_db):
        """Test that get_all_preferences is called correctly."""
        client, mock_db = client_and_db

        client.get("/api/preferences")

        mock_db.get_all_preferences.assert_called()


# =============================================================================
# Edge Cases and Error Handling
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_request_body(self, client_and_db):
        """Test handling of empty request body."""
        client, mock_db = client_and_db

        response = client.post("/api/preferences", data="", content_type="application/json")

        # Empty body results in parse error - caught by exception handler
        assert response.status_code in [400, 500]

    def test_malformed_json(self, client_and_db):
        """Test handling of malformed JSON."""
        client, mock_db = client_and_db

        response = client.post(
            "/api/preferences", data="{invalid json}", content_type="application/json"
        )

        # Malformed JSON results in parse error - caught by exception handler
        assert response.status_code in [400, 500]

    def test_database_error_handling(self, client_and_db):
        """Test handling of database errors."""
        client, mock_db = client_and_db
        mock_db.save_preference.return_value = False

        response = client.post(
            "/api/preferences",
            data=json.dumps({"thresholds": {"volume_spike": 4.0}}),
            content_type="application/json",
        )

        data = json.loads(response.data)
        # Should report the failure
        assert "errors" in data or data.get("success") is False

    def test_ticker_uppercase_conversion(self, client_and_db):
        """Test that ticker is converted to uppercase."""
        client, mock_db = client_and_db
        mock_db.get_preference.return_value = {}
        mock_db.save_preference.return_value = True

        response = client.post(
            "/api/watchlist",
            data=json.dumps(
                {
                    "action": "add",
                    "ticker": "aapl",  # lowercase
                    "names": ["Apple"],
                }
            ),
            content_type="application/json",
        )

        data = json.loads(response.data)
        assert "AAPL" in data["watchlist"]  # Should be uppercase

    def test_whitespace_trimming(self, client_and_db):
        """Test that whitespace is trimmed from inputs."""
        client, mock_db = client_and_db
        mock_db.get_preference.return_value = {}
        mock_db.save_preference.return_value = True

        response = client.post(
            "/api/watchlist",
            data=json.dumps(
                {"action": "add", "ticker": "  AAPL  ", "names": ["  Apple  ", "  Apple Inc  "]}
            ),
            content_type="application/json",
        )

        data = json.loads(response.data)
        assert "AAPL" in data["watchlist"]
