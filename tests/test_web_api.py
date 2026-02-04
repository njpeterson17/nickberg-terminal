"""
Tests for the web/app.py Flask API.

Tests API endpoints, authentication, and responses.
"""

import pytest
import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import sys

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_database():
    """Create a mock database for the Flask app."""
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
    }


@pytest.fixture
def app_and_db(mock_database, mock_config, tmp_path):
    """Create a Flask test application with mocked database."""
    # Create a temporary config file
    config_path = tmp_path / "config" / "settings.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)

    import yaml

    with open(config_path, "w") as f:
        yaml.dump(mock_config, f)

    # Clean up any existing module import
    if "web.app" in sys.modules:
        del sys.modules["web.app"]

    # Patch the config path and database before importing app
    with patch.dict(os.environ, {"NICKBERG_API_KEY": ""}, clear=False):
        with patch("web.app.CONFIG_PATH", config_path):
            with patch("web.app.Database") as MockDatabase:
                MockDatabase.return_value = mock_database

                from web.app import app as flask_app

                flask_app.config["TESTING"] = True

                # Also patch the 'db' global in the module
                with patch("web.app.db", mock_database):
                    yield flask_app, mock_database


@pytest.fixture
def client(app_and_db):
    """Create a test client without API key requirement."""
    app, _ = app_and_db
    return app.test_client()


@pytest.fixture
def client_and_db(app_and_db):
    """Create a test client and return both client and mock db."""
    app, mock_db = app_and_db
    return app.test_client(), mock_db


# =============================================================================
# Index Route Tests
# =============================================================================


class TestIndexRoute:
    """Tests for the index route."""

    def test_index_returns_html(self, client):
        """Test that index returns HTML."""
        with patch("web.app.render_template", return_value="<html></html>"):
            response = client.get("/")

        assert response.status_code == 200


# =============================================================================
# API Stats Endpoint Tests
# =============================================================================


class TestApiStats:
    """Tests for /api/stats endpoint."""

    def test_stats_returns_json(self, client_and_db):
        """Test that stats endpoint returns JSON."""
        client, mock_db = client_and_db

        with patch(
            "web.app.get_db_stats",
            return_value={
                "total_articles": 100,
                "total_mentions": 500,
                "total_alerts": 10,
                "articles_24h": 25,
            },
        ):
            response = client.get("/api/stats")

        assert response.status_code == 200
        assert response.content_type == "application/json"

        data = json.loads(response.data)
        assert "total_articles" in data
        assert "total_mentions" in data
        assert "total_alerts" in data
        assert "articles_24h" in data

    def test_stats_values(self, client_and_db):
        """Test stats endpoint returns correct values."""
        client, mock_db = client_and_db

        with patch(
            "web.app.get_db_stats",
            return_value={
                "total_articles": 150,
                "total_mentions": 750,
                "total_alerts": 25,
                "articles_24h": 40,
            },
        ):
            response = client.get("/api/stats")
            data = json.loads(response.data)

        assert data["total_articles"] == 150
        assert data["total_mentions"] == 750
        assert data["total_alerts"] == 25
        assert data["articles_24h"] == 40


# =============================================================================
# API Alerts Endpoint Tests
# =============================================================================


class TestApiAlerts:
    """Tests for /api/alerts endpoint."""

    def test_alerts_returns_empty_list(self, client_and_db):
        """Test alerts endpoint with no alerts."""
        client, mock_db = client_and_db

        with patch("web.app.get_recent_alerts", return_value=[]):
            response = client.get("/api/alerts")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data == []

    def test_alerts_returns_alert_list(self, client_and_db):
        """Test alerts endpoint returns formatted alerts."""
        client, mock_db = client_and_db

        mock_alerts = [
            {
                "id": 1,
                "type": "volume_spike",
                "ticker": "AAPL",
                "company": "Apple Inc",
                "severity": "high",
                "message": "Apple spike detected",
                "details": {"articles_6h": 10},
                "created_at": "2025-01-15T12:00:00",
            },
            {
                "id": 2,
                "type": "sentiment_shift",
                "ticker": "MSFT",
                "company": "Microsoft",
                "severity": "medium",
                "message": "Microsoft sentiment changed",
                "details": {},
                "created_at": "2025-01-15T11:00:00",
            },
        ]

        with patch("web.app.get_recent_alerts", return_value=mock_alerts):
            response = client.get("/api/alerts")

        assert response.status_code == 200
        data = json.loads(response.data)

        assert len(data) == 2
        assert data[0]["id"] == 1
        assert data[0]["type"] == "volume_spike"
        assert data[0]["ticker"] == "AAPL"
        assert data[0]["severity"] == "high"

    def test_alerts_accepts_limit_param(self, client_and_db):
        """Test alerts endpoint accepts limit parameter."""
        client, mock_db = client_and_db

        with patch("web.app.get_recent_alerts", return_value=[]) as mock_func:
            response = client.get("/api/alerts?limit=5")

        assert response.status_code == 200
        mock_func.assert_called_once_with(5)


# =============================================================================
# API Articles Endpoint Tests
# =============================================================================


class TestApiArticles:
    """Tests for /api/articles endpoint."""

    def test_articles_returns_empty_list(self, client_and_db):
        """Test articles endpoint with no articles."""
        client, mock_db = client_and_db

        with patch("web.app.get_recent_articles", return_value=[]):
            response = client.get("/api/articles")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data == []

    def test_articles_returns_article_list(self, client_and_db):
        """Test articles endpoint returns formatted articles."""
        client, mock_db = client_and_db

        mock_articles = [
            {
                "id": 1,
                "url": "http://example.com/article1",
                "title": "Test Article 1",
                "source": "Reuters",
                "published_at": "2025-01-15T12:00:00",
                "scraped_at": "2025-01-15T12:05:00",
                "sentiment": 0.5,
                "mentions": ["AAPL"],
            },
            {
                "id": 2,
                "url": "http://example.com/article2",
                "title": "Test Article 2",
                "source": "Bloomberg",
                "published_at": "2025-01-15T11:00:00",
                "scraped_at": "2025-01-15T11:05:00",
                "sentiment": -0.2,
                "mentions": [],
            },
        ]

        with patch("web.app.get_recent_articles", return_value=mock_articles):
            response = client.get("/api/articles")

        assert response.status_code == 200
        data = json.loads(response.data)

        assert len(data) == 2
        assert data[0]["id"] == 1
        assert data[0]["title"] == "Test Article 1"
        assert data[0]["source"] == "Reuters"
        assert data[0]["sentiment"] == 0.5
        assert data[0]["mentions"] == ["AAPL"]

    def test_articles_accepts_limit_param(self, client_and_db):
        """Test articles endpoint accepts limit parameter."""
        client, mock_db = client_and_db

        with patch("web.app.get_recent_articles", return_value=[]) as mock_func:
            response = client.get("/api/articles?limit=25")

        assert response.status_code == 200
        mock_func.assert_called_once_with(25)


# =============================================================================
# API Authentication Tests
# =============================================================================


class TestApiAuthentication:
    """Tests for API key authentication."""

    def test_no_api_key_configured_allows_access(self, client):
        """Test that endpoints work without API key when not configured."""
        # client fixture has no API key configured
        with patch(
            "web.app.get_db_stats",
            return_value={
                "total_articles": 0,
                "total_mentions": 0,
                "total_alerts": 0,
                "articles_24h": 0,
            },
        ):
            response = client.get("/api/stats")
        assert response.status_code == 200

    def test_missing_api_key_returns_401(self, mock_database, mock_config, tmp_path):
        """Test that missing API key returns 401 when key is configured."""
        config_path = tmp_path / "config" / "settings.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)

        import yaml

        with open(config_path, "w") as f:
            yaml.dump(mock_config, f)

        if "web.app" in sys.modules:
            del sys.modules["web.app"]

        # Need to patch at the module level where API_KEY is defined
        with patch.dict(os.environ, {"NICKBERG_API_KEY": "test-key"}, clear=False):
            with patch("web.app.CONFIG_PATH", config_path):
                with patch("web.app.Database", return_value=mock_database):
                    with patch("web.app.API_KEY", "test-key"):
                        from web.app import app as flask_app

                        flask_app.config["TESTING"] = True
                        test_client = flask_app.test_client()

                        response = test_client.get("/api/stats")

                        assert response.status_code == 401
                        data = json.loads(response.data)
                        assert "error" in data
                        assert "API key required" in data["error"]

    def test_wrong_api_key_returns_403(self, mock_database, mock_config, tmp_path):
        """Test that wrong API key returns 403."""
        config_path = tmp_path / "config" / "settings.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)

        import yaml

        with open(config_path, "w") as f:
            yaml.dump(mock_config, f)

        if "web.app" in sys.modules:
            del sys.modules["web.app"]

        with patch.dict(os.environ, {"NICKBERG_API_KEY": "correct-key"}, clear=False):
            with patch("web.app.CONFIG_PATH", config_path):
                with patch("web.app.Database", return_value=mock_database):
                    with patch("web.app.API_KEY", "correct-key"):
                        from web.app import app as flask_app

                        flask_app.config["TESTING"] = True
                        test_client = flask_app.test_client()

                        response = test_client.get("/api/stats", headers={"X-API-Key": "wrong-key"})

                        assert response.status_code == 403
                        data = json.loads(response.data)
                        assert "error" in data
                        assert "Invalid API key" in data["error"]

    def test_correct_api_key_in_header(self, mock_database, mock_config, tmp_path):
        """Test that correct API key in header allows access."""
        config_path = tmp_path / "config" / "settings.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)

        import yaml

        with open(config_path, "w") as f:
            yaml.dump(mock_config, f)

        if "web.app" in sys.modules:
            del sys.modules["web.app"]

        with patch.dict(os.environ, {"NICKBERG_API_KEY": "valid-key"}, clear=False):
            with patch("web.app.CONFIG_PATH", config_path):
                with patch("web.app.Database", return_value=mock_database):
                    with patch("web.app.API_KEY", "valid-key"):
                        from web.app import app as flask_app

                        flask_app.config["TESTING"] = True
                        test_client = flask_app.test_client()

                        response = test_client.get("/api/stats", headers={"X-API-Key": "valid-key"})

                        assert response.status_code == 200

    def test_correct_api_key_in_query_param(self, mock_database, mock_config, tmp_path):
        """Test that correct API key in query parameter allows access."""
        config_path = tmp_path / "config" / "settings.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)

        import yaml

        with open(config_path, "w") as f:
            yaml.dump(mock_config, f)

        if "web.app" in sys.modules:
            del sys.modules["web.app"]

        with patch.dict(os.environ, {"NICKBERG_API_KEY": "query-key"}, clear=False):
            with patch("web.app.CONFIG_PATH", config_path):
                with patch("web.app.Database", return_value=mock_database):
                    with patch("web.app.API_KEY", "query-key"):
                        from web.app import app as flask_app

                        flask_app.config["TESTING"] = True
                        test_client = flask_app.test_client()

                        response = test_client.get("/api/stats?api_key=query-key")

                        assert response.status_code == 200


# =============================================================================
# API Timeline Endpoint Tests
# =============================================================================


class TestApiTimeline:
    """Tests for /api/timeline endpoint."""

    def test_timeline_returns_json(self, client_and_db):
        """Test timeline endpoint returns JSON."""
        client, mock_db = client_and_db

        with patch("web.app.get_mention_timeline", return_value={}):
            response = client.get("/api/timeline")

        assert response.status_code == 200
        assert response.content_type == "application/json"

    def test_timeline_accepts_hours_param(self, client_and_db):
        """Test timeline endpoint accepts hours parameter."""
        client, mock_db = client_and_db

        with patch("web.app.get_mention_timeline", return_value={}) as mock_func:
            response = client.get("/api/timeline?hours=48")

        assert response.status_code == 200
        mock_func.assert_called_once_with(48)


# =============================================================================
# API Top Companies Endpoint Tests
# =============================================================================


class TestApiTopCompanies:
    """Tests for /api/companies/top endpoint."""

    def test_top_companies_returns_json(self, client_and_db):
        """Test top companies endpoint returns JSON."""
        client, mock_db = client_and_db

        mock_companies = [
            {"company_ticker": "AAPL", "company_name": "Apple", "count": 50},
            {"company_ticker": "MSFT", "company_name": "Microsoft", "count": 30},
        ]

        with patch("web.app.get_top_companies", return_value=mock_companies):
            response = client.get("/api/companies/top")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert len(data) == 2

    def test_top_companies_accepts_limit_param(self, client_and_db):
        """Test top companies endpoint accepts limit parameter."""
        client, mock_db = client_and_db

        with patch("web.app.get_top_companies", return_value=[]) as mock_func:
            response = client.get("/api/companies/top?limit=5")

        assert response.status_code == 200
        mock_func.assert_called_once_with(5)


# =============================================================================
# API All Companies Endpoint Tests
# =============================================================================


class TestApiAllCompanies:
    """Tests for /api/companies/all endpoint."""

    def test_all_companies_returns_json(self, client_and_db):
        """Test all companies endpoint returns JSON."""
        client, mock_db = client_and_db
        mock_db.get_mention_counts.return_value = []

        response = client.get("/api/companies/all")

        assert response.status_code == 200
        assert response.content_type == "application/json"


# =============================================================================
# API Sentiment Endpoint Tests
# =============================================================================


class TestApiSentiment:
    """Tests for /api/sentiment endpoint."""

    def test_sentiment_returns_distribution(self, client_and_db):
        """Test sentiment endpoint returns distribution."""
        client, mock_db = client_and_db

        with patch(
            "web.app.get_sentiment_distribution",
            return_value={"positive": 10, "negative": 5, "neutral": 15, "total": 30},
        ):
            response = client.get("/api/sentiment")

        assert response.status_code == 200
        data = json.loads(response.data)

        assert "positive" in data
        assert "negative" in data
        assert "neutral" in data
        assert "total" in data


# =============================================================================
# API Sources Endpoint Tests
# =============================================================================


class TestApiSources:
    """Tests for /api/sources endpoint."""

    def test_sources_returns_distribution(self, client_and_db):
        """Test sources endpoint returns distribution."""
        client, mock_db = client_and_db

        with patch(
            "web.app.get_source_distribution",
            return_value=[{"source": "Reuters", "count": 50}, {"source": "Bloomberg", "count": 30}],
        ):
            response = client.get("/api/sources")

        assert response.status_code == 200
        data = json.loads(response.data)

        assert len(data) == 2
        assert data[0]["source"] == "Reuters"
        assert data[0]["count"] == 50


# =============================================================================
# API Config Endpoint Tests
# =============================================================================


class TestApiConfig:
    """Tests for /api/config endpoint."""

    def test_config_returns_json(self, client):
        """Test config endpoint returns JSON."""
        response = client.get("/api/config")

        assert response.status_code == 200
        assert response.content_type == "application/json"

    def test_config_includes_watchlist(self, client):
        """Test config includes watchlist."""
        response = client.get("/api/config")
        data = json.loads(response.data)

        assert "watchlist" in data

    def test_config_includes_sources(self, client):
        """Test config includes enabled sources."""
        response = client.get("/api/config")
        data = json.loads(response.data)

        assert "sources" in data

    def test_config_includes_patterns(self, client):
        """Test config includes pattern settings."""
        response = client.get("/api/config")
        data = json.loads(response.data)

        assert "patterns" in data
        assert "volume_spike_threshold" in data["patterns"]


# =============================================================================
# API Alert Acknowledgment Tests
# =============================================================================


class TestApiAckAlert:
    """Tests for /api/alerts/<id>/ack endpoint."""

    def test_ack_alert_returns_success(self, client_and_db):
        """Test acknowledging alert returns success."""
        client, mock_db = client_and_db

        mock_conn = MagicMock()
        mock_db.get_connection.return_value.__enter__.return_value = mock_conn

        response = client.post("/api/alerts/123/ack")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["success"] is True

    def test_ack_alert_updates_database(self, client_and_db):
        """Test that acknowledge updates the database."""
        client, mock_db = client_and_db

        mock_conn = MagicMock()
        mock_db.get_connection.return_value.__enter__.return_value = mock_conn

        response = client.post("/api/alerts/456/ack")

        assert response.status_code == 200
        mock_conn.execute.assert_called()
        mock_conn.commit.assert_called()


# =============================================================================
# API Run Bot Endpoint Tests
# =============================================================================


class TestApiRunBot:
    """Tests for /api/run endpoint."""

    @patch("subprocess.run")
    def test_run_bot_success(self, mock_subprocess, client):
        """Test successful bot run."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Bot executed successfully"
        mock_result.stderr = ""
        mock_subprocess.return_value = mock_result

        response = client.post("/api/run")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["success"] is True
        assert "Bot executed successfully" in data["output"]

    @patch("subprocess.run")
    def test_run_bot_failure(self, mock_subprocess, client):
        """Test failed bot run."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Error occurred"
        mock_subprocess.return_value = mock_result

        response = client.post("/api/run")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["success"] is False

    @patch("subprocess.run")
    def test_run_bot_exception(self, mock_subprocess, client):
        """Test bot run with exception."""
        mock_subprocess.side_effect = Exception("Subprocess error")

        response = client.post("/api/run")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["success"] is False
        assert "error" in data
