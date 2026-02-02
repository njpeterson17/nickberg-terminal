"""
Tests for the alerts module.

Tests AlertManager initialization, console/file alerts, retry logic,
Telegram/webhook alerts with mocked requests, and alert dispatching.
"""

import pytest
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock, call
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from alerts import AlertManager, DEFAULT_MAX_RETRIES, DEFAULT_INITIAL_DELAY, DEFAULT_TIMEOUT
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
def sample_pattern_alert():
    """Create a sample PatternAlert for testing."""
    return PatternAlert(
        pattern_type="volume_spike",
        ticker="AAPL",
        company_name="Apple Inc",
        severity="high",
        message="Apple Inc (AAPL): 10 articles in 6h (spike: 5.0x normal)",
        details={"articles_6h": 10, "articles_24h": 15, "spike_ratio": 5.0},
    )


@pytest.fixture
def base_config():
    """Base configuration for AlertManager."""
    return {
        "console": True,
        "file": {"enabled": False, "path": "logs/alerts.log"},
        "telegram": {"enabled": False, "bot_token": "", "chat_id": ""},
        "webhook": {"enabled": False, "url": ""},
    }


# =============================================================================
# AlertManager Initialization Tests
# =============================================================================


class TestAlertManagerInitialization:
    """Tests for AlertManager initialization."""

    def test_basic_initialization(self, mock_db, base_config):
        """Test basic AlertManager initialization."""
        manager = AlertManager(base_config, mock_db)

        assert manager.console_enabled is True
        assert manager.file_enabled is False
        assert manager.telegram_enabled is False
        assert manager.webhook_enabled is False

    def test_file_alert_enabled(self, mock_db, base_config, tmp_path):
        """Test initialization with file alerts enabled."""
        base_config["file"]["enabled"] = True
        base_config["file"]["path"] = str(tmp_path / "logs" / "alerts.log")

        manager = AlertManager(base_config, mock_db)

        assert manager.file_enabled is True
        assert manager.file_path == str(tmp_path / "logs" / "alerts.log")
        # Parent directory should be created
        assert (tmp_path / "logs").exists()

    def test_telegram_enabled_from_config(self, mock_db, base_config):
        """Test Telegram configuration from config dict."""
        base_config["telegram"]["enabled"] = True
        base_config["telegram"]["bot_token"] = "test_token_123"
        base_config["telegram"]["chat_id"] = "12345"

        # Clear env vars to test config values
        with patch.dict(os.environ, {}, clear=True):
            manager = AlertManager(base_config, mock_db)

        assert manager.telegram_enabled is True
        assert manager.telegram_token == "test_token_123"
        assert manager.telegram_chat_id == "12345"

    def test_telegram_enabled_from_env(self, mock_db, base_config):
        """Test Telegram configuration from environment variables."""
        base_config["telegram"]["enabled"] = True

        env_vars = {
            "NEWS_BOT_TELEGRAM_TOKEN": "env_token_456",
            "NEWS_BOT_TELEGRAM_CHAT_ID": "67890",
        }

        with patch.dict(os.environ, env_vars, clear=False):
            manager = AlertManager(base_config, mock_db)

        assert manager.telegram_enabled is True
        assert manager.telegram_token == "env_token_456"
        assert manager.telegram_chat_id == "67890"

    def test_webhook_enabled_from_config(self, mock_db, base_config):
        """Test webhook configuration from config dict."""
        base_config["webhook"]["enabled"] = True
        base_config["webhook"]["url"] = "https://example.com/webhook"

        with patch.dict(os.environ, {}, clear=True):
            manager = AlertManager(base_config, mock_db)

        assert manager.webhook_enabled is True
        assert manager.webhook_url == "https://example.com/webhook"

    def test_webhook_enabled_from_env(self, mock_db, base_config):
        """Test webhook configuration from environment variable."""
        base_config["webhook"]["enabled"] = True

        env_vars = {"NEWS_BOT_WEBHOOK_URL": "https://env.example.com/webhook"}

        with patch.dict(os.environ, env_vars, clear=False):
            manager = AlertManager(base_config, mock_db)

        assert manager.webhook_enabled is True
        assert manager.webhook_url == "https://env.example.com/webhook"


# =============================================================================
# Console Alert Tests
# =============================================================================


class TestConsoleAlerts:
    """Tests for console alert formatting."""

    def test_console_alert_high_severity(self, mock_db, base_config, sample_pattern_alert, capsys):
        """Test console alert output for high severity."""
        manager = AlertManager(base_config, mock_db)

        manager._console_alert(sample_pattern_alert)

        captured = capsys.readouterr()
        assert "HIGH" in captured.out
        assert "volume_spike" in captured.out
        assert "Apple Inc" in captured.out or "AAPL" in captured.out

    def test_console_alert_medium_severity(self, mock_db, base_config, capsys):
        """Test console alert output for medium severity."""
        alert = PatternAlert(
            pattern_type="sentiment_shift",
            ticker="MSFT",
            company_name="Microsoft",
            severity="medium",
            message="Microsoft sentiment shifted",
            details={"direction": "positive"},
        )

        manager = AlertManager(base_config, mock_db)
        manager._console_alert(alert)

        captured = capsys.readouterr()
        assert "MEDIUM" in captured.out

    def test_console_alert_low_severity(self, mock_db, base_config, capsys):
        """Test console alert output for low severity."""
        alert = PatternAlert(
            pattern_type="momentum",
            ticker="GOOGL",
            company_name="Google",
            severity="low",
            message="Google momentum building",
            details={},
        )

        manager = AlertManager(base_config, mock_db)
        manager._console_alert(alert)

        captured = capsys.readouterr()
        assert "LOW" in captured.out

    def test_console_alert_with_details(self, mock_db, base_config, sample_pattern_alert, capsys):
        """Test that details are printed in console alert."""
        manager = AlertManager(base_config, mock_db)
        manager._console_alert(sample_pattern_alert)

        captured = capsys.readouterr()
        assert "Details" in captured.out
        assert "articles_6h" in captured.out or "10" in captured.out


# =============================================================================
# File Alert Tests
# =============================================================================


class TestFileAlerts:
    """Tests for file alert writing."""

    def test_file_alert_writes_json(self, mock_db, base_config, sample_pattern_alert, tmp_path):
        """Test that file alerts are written as JSON."""
        alert_file = tmp_path / "alerts.log"
        base_config["file"]["enabled"] = True
        base_config["file"]["path"] = str(alert_file)

        manager = AlertManager(base_config, mock_db)
        manager._file_alert(sample_pattern_alert)

        assert alert_file.exists()
        content = alert_file.read_text()
        entry = json.loads(content.strip())

        assert entry["severity"] == "high"
        assert entry["type"] == "volume_spike"
        assert entry["ticker"] == "AAPL"
        assert entry["message"] == sample_pattern_alert.message
        assert "timestamp" in entry

    def test_file_alert_appends(self, mock_db, base_config, tmp_path):
        """Test that multiple alerts are appended to file."""
        alert_file = tmp_path / "alerts.log"
        base_config["file"]["enabled"] = True
        base_config["file"]["path"] = str(alert_file)

        manager = AlertManager(base_config, mock_db)

        alert1 = PatternAlert(
            pattern_type="volume_spike",
            ticker="AAPL",
            company_name="Apple",
            severity="high",
            message="Alert 1",
            details={},
        )
        alert2 = PatternAlert(
            pattern_type="sentiment_shift",
            ticker="MSFT",
            company_name="Microsoft",
            severity="medium",
            message="Alert 2",
            details={},
        )

        manager._file_alert(alert1)
        manager._file_alert(alert2)

        lines = alert_file.read_text().strip().split("\n")
        assert len(lines) == 2

        entry1 = json.loads(lines[0])
        entry2 = json.loads(lines[1])

        assert entry1["ticker"] == "AAPL"
        assert entry2["ticker"] == "MSFT"

    def test_file_alert_handles_error(self, mock_db, base_config, sample_pattern_alert, tmp_path):
        """Test that file alert errors are handled gracefully."""
        alert_file = tmp_path / "alerts.log"
        base_config["file"]["enabled"] = True
        base_config["file"]["path"] = str(alert_file)

        manager = AlertManager(base_config, mock_db)

        # Make the file read-only directory to cause a write error
        alert_file.parent.chmod(0o444)

        try:
            # Should not raise exception even if write fails
            manager._file_alert(sample_pattern_alert)
        finally:
            # Restore permissions for cleanup
            alert_file.parent.chmod(0o755)


# =============================================================================
# Retry Logic Tests
# =============================================================================


class TestRetryLogic:
    """Tests for _retry_with_backoff method."""

    def test_retry_success_on_first_attempt(self, mock_db, base_config):
        """Test successful execution on first attempt."""
        manager = AlertManager(base_config, mock_db)

        func = Mock()
        result = manager._retry_with_backoff(func, "test operation")

        assert result is True
        func.assert_called_once()

    def test_retry_success_after_failures(self, mock_db, base_config):
        """Test successful execution after transient failures."""
        from requests.exceptions import Timeout

        manager = AlertManager(base_config, mock_db)

        # Fail twice, then succeed
        func = Mock(side_effect=[Timeout(), Timeout(), None])

        with patch("time.sleep"):  # Skip actual delays
            result = manager._retry_with_backoff(
                func, "test operation", max_retries=3, initial_delay=0.01
            )

        assert result is True
        assert func.call_count == 3

    def test_retry_exhausted(self, mock_db, base_config):
        """Test all retries exhausted."""
        from requests.exceptions import ConnectionError

        manager = AlertManager(base_config, mock_db)

        func = Mock(side_effect=ConnectionError("Connection failed"))

        with patch("time.sleep"):
            result = manager._retry_with_backoff(
                func, "test operation", max_retries=3, initial_delay=0.01
            )

        assert result is False
        assert func.call_count == 3

    def test_retry_no_retry_on_client_error(self, mock_db, base_config):
        """Test that 4xx client errors (except 429) are not retried."""
        from requests.exceptions import HTTPError

        manager = AlertManager(base_config, mock_db)

        response = Mock()
        response.status_code = 403
        error = HTTPError(response=response)

        func = Mock(side_effect=error)

        with patch("time.sleep"):
            result = manager._retry_with_backoff(func, "test operation", max_retries=3)

        assert result is False
        func.assert_called_once()  # Should not retry

    def test_retry_on_rate_limit(self, mock_db, base_config):
        """Test that 429 rate limit errors are retried."""
        from requests.exceptions import HTTPError

        manager = AlertManager(base_config, mock_db)

        response = Mock()
        response.status_code = 429

        # First call raises 429, second succeeds
        func = Mock(side_effect=[HTTPError(response=response), None])

        with patch("time.sleep"):
            result = manager._retry_with_backoff(
                func, "test operation", max_retries=3, initial_delay=0.01
            )

        assert result is True
        assert func.call_count == 2

    def test_retry_exponential_backoff_timing(self, mock_db, base_config):
        """Test that exponential backoff delays are applied correctly."""
        from requests.exceptions import Timeout

        manager = AlertManager(base_config, mock_db)

        func = Mock(side_effect=[Timeout(), Timeout(), Timeout()])

        with patch("time.sleep") as mock_sleep:
            manager._retry_with_backoff(func, "test operation", max_retries=3, initial_delay=1.0)

        # Should have slept with exponential backoff
        # First retry: 1.0s, Second retry: 2.0s
        sleep_calls = [call[0][0] for call in mock_sleep.call_args_list]
        assert len(sleep_calls) == 2
        assert sleep_calls[0] == 1.0
        assert sleep_calls[1] == 2.0


# =============================================================================
# Telegram Alert Tests
# =============================================================================


class TestTelegramAlerts:
    """Tests for Telegram alert sending."""

    @patch("requests.post")
    def test_telegram_alert_success(self, mock_post, mock_db, base_config, sample_pattern_alert):
        """Test successful Telegram alert."""
        base_config["telegram"]["enabled"] = True
        base_config["telegram"]["bot_token"] = "test_token"
        base_config["telegram"]["chat_id"] = "12345"

        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        with patch.dict(os.environ, {}, clear=True):
            manager = AlertManager(base_config, mock_db)
            manager._telegram_alert(sample_pattern_alert)

        mock_post.assert_called_once()
        call_args = mock_post.call_args

        # Verify URL contains bot token
        assert "test_token" in call_args[0][0]
        assert "sendMessage" in call_args[0][0]

        # Verify payload
        payload = call_args[1]["json"]
        assert payload["chat_id"] == "12345"
        assert "HIGH" in payload["text"]
        assert "Apple" in payload["text"] or "AAPL" in payload["text"]

    @patch("requests.post")
    def test_telegram_alert_with_retry(self, mock_post, mock_db, base_config, sample_pattern_alert):
        """Test Telegram alert with retry on failure."""
        from requests.exceptions import Timeout

        base_config["telegram"]["enabled"] = True
        base_config["telegram"]["bot_token"] = "test_token"
        base_config["telegram"]["chat_id"] = "12345"

        mock_response = Mock()
        mock_response.raise_for_status = Mock()

        # Fail first, succeed second
        mock_post.side_effect = [Timeout(), mock_response]

        with patch.dict(os.environ, {}, clear=True):
            with patch("time.sleep"):
                manager = AlertManager(base_config, mock_db)
                manager._telegram_alert(sample_pattern_alert)

        assert mock_post.call_count == 2

    @patch("requests.post")
    def test_telegram_alert_formats_details(
        self, mock_post, mock_db, base_config, sample_pattern_alert
    ):
        """Test that alert details are formatted in Telegram message."""
        base_config["telegram"]["enabled"] = True
        base_config["telegram"]["bot_token"] = "test_token"
        base_config["telegram"]["chat_id"] = "12345"

        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        with patch.dict(os.environ, {}, clear=True):
            manager = AlertManager(base_config, mock_db)
            manager._telegram_alert(sample_pattern_alert)

        payload = mock_post.call_args[1]["json"]
        message = payload["text"]

        assert "Details" in message
        assert "articles_6h" in message


# =============================================================================
# Webhook Alert Tests
# =============================================================================


class TestWebhookAlerts:
    """Tests for webhook alert sending."""

    @patch("requests.post")
    def test_webhook_alert_success(self, mock_post, mock_db, base_config, sample_pattern_alert):
        """Test successful webhook alert."""
        base_config["webhook"]["enabled"] = True
        base_config["webhook"]["url"] = "https://example.com/webhook"

        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        with patch.dict(os.environ, {}, clear=True):
            manager = AlertManager(base_config, mock_db)
            manager._webhook_alert(sample_pattern_alert)

        mock_post.assert_called_once()
        call_args = mock_post.call_args

        # Verify URL
        assert call_args[0][0] == "https://example.com/webhook"

        # Verify payload
        payload = call_args[1]["json"]
        assert "timestamp" in payload
        assert "alert" in payload
        assert payload["alert"]["pattern_type"] == "volume_spike"
        assert payload["alert"]["ticker"] == "AAPL"

    @patch("requests.post")
    def test_webhook_alert_with_retry(self, mock_post, mock_db, base_config, sample_pattern_alert):
        """Test webhook alert with retry on failure."""
        from requests.exceptions import ConnectionError

        base_config["webhook"]["enabled"] = True
        base_config["webhook"]["url"] = "https://example.com/webhook"

        mock_response = Mock()
        mock_response.raise_for_status = Mock()

        # Fail twice, succeed third
        mock_post.side_effect = [
            ConnectionError("Connection failed"),
            ConnectionError("Connection failed"),
            mock_response,
        ]

        with patch.dict(os.environ, {}, clear=True):
            with patch("time.sleep"):
                manager = AlertManager(base_config, mock_db)
                manager._webhook_alert(sample_pattern_alert)

        assert mock_post.call_count == 3

    @patch("requests.post")
    def test_webhook_alert_includes_headers(
        self, mock_post, mock_db, base_config, sample_pattern_alert
    ):
        """Test that webhook includes proper headers."""
        base_config["webhook"]["enabled"] = True
        base_config["webhook"]["url"] = "https://example.com/webhook"

        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        with patch.dict(os.environ, {}, clear=True):
            manager = AlertManager(base_config, mock_db)
            manager._webhook_alert(sample_pattern_alert)

        call_args = mock_post.call_args
        headers = call_args[1]["headers"]

        assert headers["Content-Type"] == "application/json"


# =============================================================================
# Alert Dispatching Tests
# =============================================================================


class TestAlertDispatching:
    """Tests for alert dispatching to multiple channels."""

    def test_send_alert_saves_to_database(self, mock_db, base_config, sample_pattern_alert):
        """Test that alerts are saved to database first."""
        base_config["console"] = False  # Disable console to simplify test

        manager = AlertManager(base_config, mock_db)
        manager._send_alert(sample_pattern_alert)

        mock_db.save_alert.assert_called_once()
        saved_alert = mock_db.save_alert.call_args[0][0]

        assert saved_alert.alert_type == "volume_spike"
        assert saved_alert.company_ticker == "AAPL"
        assert saved_alert.severity == "high"

    def test_send_alert_skips_duplicate(self, mock_db, base_config, sample_pattern_alert):
        """Test that duplicate alerts are not sent to channels."""
        mock_db.save_alert.return_value = None  # Indicates duplicate

        manager = AlertManager(base_config, mock_db)

        with patch.object(manager, "_console_alert") as mock_console:
            manager._send_alert(sample_pattern_alert)

        mock_console.assert_not_called()

    def test_send_alerts_processes_multiple(self, mock_db, base_config):
        """Test sending multiple alerts."""
        alerts = [
            PatternAlert(
                pattern_type="volume_spike",
                ticker="AAPL",
                company_name="Apple",
                severity="high",
                message="Alert 1",
                details={},
            ),
            PatternAlert(
                pattern_type="sentiment_shift",
                ticker="MSFT",
                company_name="Microsoft",
                severity="medium",
                message="Alert 2",
                details={},
            ),
        ]

        manager = AlertManager(base_config, mock_db)

        with patch.object(manager, "_send_alert") as mock_send:
            manager.send_alerts(alerts)

        assert mock_send.call_count == 2

    @patch("requests.post")
    def test_send_alert_to_all_channels(
        self, mock_post, mock_db, base_config, sample_pattern_alert, tmp_path, capsys
    ):
        """Test that alert is sent to all enabled channels."""
        # Enable all channels
        base_config["console"] = True
        base_config["file"]["enabled"] = True
        base_config["file"]["path"] = str(tmp_path / "alerts.log")
        base_config["telegram"]["enabled"] = True
        base_config["telegram"]["bot_token"] = "token"
        base_config["telegram"]["chat_id"] = "chat"
        base_config["webhook"]["enabled"] = True
        base_config["webhook"]["url"] = "https://example.com/webhook"

        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        with patch.dict(os.environ, {}, clear=True):
            manager = AlertManager(base_config, mock_db)
            manager._send_alert(sample_pattern_alert)

        # Verify console output
        captured = capsys.readouterr()
        assert "HIGH" in captured.out

        # Verify file written
        assert (tmp_path / "alerts.log").exists()

        # Verify Telegram and webhook called (2 POST requests)
        assert mock_post.call_count == 2


# =============================================================================
# Get Recent Alerts Tests
# =============================================================================


class TestGetRecentAlerts:
    """Tests for get_recent_alerts method."""

    def test_get_recent_alerts(self, mock_db, base_config):
        """Test getting recent alerts."""
        mock_alerts = [
            Alert(
                id=1,
                alert_type="volume_spike",
                company_ticker="AAPL",
                company_name="Apple",
                severity="high",
                message="Test alert",
                details="{}",
                created_at=datetime.now(),
                acknowledged=False,
            )
        ]
        mock_db.get_unacknowledged_alerts.return_value = mock_alerts

        manager = AlertManager(base_config, mock_db)
        alerts = manager.get_recent_alerts(limit=10)

        mock_db.get_unacknowledged_alerts.assert_called_once_with(10)
        assert len(alerts) == 1
        assert alerts[0]["ticker"] == "AAPL"
        assert alerts[0]["type"] == "volume_spike"

    def test_get_recent_alerts_formats_datetime(self, mock_db, base_config):
        """Test that datetime is formatted as ISO string."""
        test_time = datetime(2025, 1, 15, 12, 30, 0)
        mock_alerts = [
            Alert(
                id=1,
                alert_type="volume_spike",
                company_ticker="AAPL",
                company_name="Apple",
                severity="high",
                message="Test alert",
                details="{}",
                created_at=test_time,
                acknowledged=False,
            )
        ]
        mock_db.get_unacknowledged_alerts.return_value = mock_alerts

        manager = AlertManager(base_config, mock_db)
        alerts = manager.get_recent_alerts()

        assert alerts[0]["created_at"] == "2025-01-15T12:30:00"


# =============================================================================
# Acknowledge Alert Tests
# =============================================================================


class TestAcknowledgeAlert:
    """Tests for acknowledge_alert method."""

    def test_acknowledge_alert(self, mock_db, base_config):
        """Test acknowledging an alert."""
        mock_conn = MagicMock()
        mock_db.get_connection.return_value.__enter__ = Mock(return_value=mock_conn)
        mock_db.get_connection.return_value.__exit__ = Mock(return_value=False)

        manager = AlertManager(base_config, mock_db)
        manager.acknowledge_alert(123)

        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args
        assert "UPDATE alerts SET acknowledged = TRUE" in call_args[0][0]
        assert call_args[0][1] == (123,)
        mock_conn.commit.assert_called_once()
