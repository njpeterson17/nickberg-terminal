"""
Tests for config_validator module.
"""

import tempfile
from pathlib import Path

import pytest
import yaml

import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from config_validator import ConfigValidator, ValidationResult, validate_config


class TestValidationResult:
    """Tests for ValidationResult class."""

    def test_empty_result_is_valid(self):
        """An empty result should be valid."""
        result = ValidationResult()
        assert result.is_valid is True
        assert len(result.errors) == 0

    def test_result_with_error_is_invalid(self):
        """A result with errors should be invalid."""
        result = ValidationResult()
        result.add_error("test.path", "is required")
        assert result.is_valid is False
        assert len(result.errors) == 1

    def test_error_string_formatting(self):
        """Error messages should be formatted correctly."""
        result = ValidationResult()
        result.add_error("scraping.timeout", "must be > 0", -5)
        error_str = str(result)
        assert "settings.yaml:scraping.timeout" in error_str
        assert "must be > 0" in error_str
        assert "-5" in error_str


class TestConfigValidatorBasics:
    """Basic validation tests."""

    def test_missing_config_file(self):
        """Should error when config file doesn't exist."""
        validator = ConfigValidator("/nonexistent/path/settings.yaml")
        result = validator.validate()
        assert not result.is_valid
        assert "Configuration file not found" in str(result)

    def test_empty_config_file(self):
        """Should error when config file is empty."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("")
            config_path = f.name

        try:
            result = validate_config(config_path)
            assert not result.is_valid
            assert "empty" in str(result).lower()
        finally:
            Path(config_path).unlink()

    def test_invalid_yaml_syntax(self):
        """Should error on invalid YAML syntax."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("invalid: yaml: syntax: [")
            config_path = f.name

        try:
            result = validate_config(config_path)
            assert not result.is_valid
            assert "YAML" in str(result) or "syntax" in str(result).lower()
        finally:
            Path(config_path).unlink()


class TestScrapingValidation:
    """Tests for scraping section validation."""

    def _create_config_with_scraping(self, scraping_config):
        """Helper to create a minimal valid config with custom scraping section."""
        return {
            "scraping": scraping_config,
            "sources": {
                "test": {
                    "enabled": True,
                    "name": "Test Source",
                    "rss_feeds": ["https://example.com/feed.xml"],
                }
            },
            "patterns": {
                "volume_spike_threshold": 3.0,
                "min_articles_for_alert": 3,
            },
            "companies": {
                "watchlist": {"AAPL": ["Apple"]},
            },
            "alerts": {"console": True},
            "database": {"path": "data/test.db"},
        }

    def test_missing_scraping_section(self):
        """Should error when scraping section is missing."""
        config = {
            "sources": {"test": {"enabled": True, "rss_feeds": ["https://example.com/feed"]}},
            "patterns": {},
            "companies": {"watchlist": {"AAPL": ["Apple"]}},
            "alerts": {},
            "database": {"path": "test.db"},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = f.name

        try:
            result = validate_config(config_path)
            assert not result.is_valid
            assert "scraping" in str(result).lower()
        finally:
            Path(config_path).unlink()

    def test_invalid_delay_min_type(self):
        """delay_min must be a number."""
        config = self._create_config_with_scraping({"delay_min": "not a number"})
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = f.name

        try:
            result = validate_config(config_path)
            assert not result.is_valid
            assert "delay_min" in str(result)
            assert "must be a number" in str(result)
        finally:
            Path(config_path).unlink()

    def test_negative_delay(self):
        """delay values must be >= 0."""
        config = self._create_config_with_scraping({"delay_min": -1})
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = f.name

        try:
            result = validate_config(config_path)
            assert not result.is_valid
            assert "delay_min" in str(result)
            assert ">=" in str(result)
        finally:
            Path(config_path).unlink()

    def test_delay_min_greater_than_max(self):
        """delay_min must be <= delay_max."""
        config = self._create_config_with_scraping({"delay_min": 5, "delay_max": 2})
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = f.name

        try:
            result = validate_config(config_path)
            assert not result.is_valid
            assert "delay_min" in str(result)
            assert "delay_max" in str(result)
        finally:
            Path(config_path).unlink()

    def test_invalid_timeout(self):
        """timeout must be > 0."""
        config = self._create_config_with_scraping({"timeout": 0})
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = f.name

        try:
            result = validate_config(config_path)
            assert not result.is_valid
            assert "timeout" in str(result)
            assert "> 0" in str(result)
        finally:
            Path(config_path).unlink()

    def test_empty_user_agents(self):
        """user_agents list must not be empty."""
        config = self._create_config_with_scraping({"user_agents": []})
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = f.name

        try:
            result = validate_config(config_path)
            assert not result.is_valid
            assert "user_agents" in str(result)
            assert "empty" in str(result).lower()
        finally:
            Path(config_path).unlink()


class TestSourcesValidation:
    """Tests for sources section validation."""

    def _create_config_with_sources(self, sources_config):
        """Helper to create a minimal valid config with custom sources section."""
        return {
            "scraping": {"timeout": 30},
            "sources": sources_config,
            "patterns": {"volume_spike_threshold": 3.0},
            "companies": {"watchlist": {"AAPL": ["Apple"]}},
            "alerts": {"console": True},
            "database": {"path": "data/test.db"},
        }

    def test_missing_sources_section(self):
        """Should error when sources section is missing."""
        config = {
            "scraping": {},
            "patterns": {},
            "companies": {"watchlist": {"AAPL": ["Apple"]}},
            "alerts": {},
            "database": {"path": "test.db"},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = f.name

        try:
            result = validate_config(config_path)
            assert not result.is_valid
            assert "sources" in str(result).lower()
        finally:
            Path(config_path).unlink()

    def test_empty_sources(self):
        """sources must contain at least one source."""
        config = self._create_config_with_sources({})
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = f.name

        try:
            result = validate_config(config_path)
            assert not result.is_valid
            assert "at least one source" in str(result).lower()
        finally:
            Path(config_path).unlink()

    def test_no_enabled_sources(self):
        """At least one source must be enabled."""
        config = self._create_config_with_sources(
            {
                "source1": {"enabled": False, "rss_feeds": ["https://example.com/feed"]},
                "source2": {"enabled": False, "rss_feeds": ["https://example.com/feed2"]},
            }
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = f.name

        try:
            result = validate_config(config_path)
            assert not result.is_valid
            assert "enabled" in str(result).lower()
        finally:
            Path(config_path).unlink()

    def test_missing_rss_feeds(self):
        """Each source must have rss_feeds."""
        config = self._create_config_with_sources({"test": {"enabled": True, "name": "Test"}})
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = f.name

        try:
            result = validate_config(config_path)
            assert not result.is_valid
            assert "rss_feeds" in str(result)
            assert "required" in str(result).lower()
        finally:
            Path(config_path).unlink()

    def test_invalid_rss_url(self):
        """RSS feed URLs must be valid."""
        config = self._create_config_with_sources(
            {"test": {"enabled": True, "rss_feeds": ["not-a-valid-url"]}}
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = f.name

        try:
            result = validate_config(config_path)
            assert not result.is_valid
            assert "valid URL" in str(result)
        finally:
            Path(config_path).unlink()


class TestPatternsValidation:
    """Tests for patterns section validation."""

    def _create_config_with_patterns(self, patterns_config):
        """Helper to create a minimal valid config with custom patterns section."""
        return {
            "scraping": {"timeout": 30},
            "sources": {"test": {"enabled": True, "rss_feeds": ["https://example.com/feed"]}},
            "patterns": patterns_config,
            "companies": {"watchlist": {"AAPL": ["Apple"]}},
            "alerts": {"console": True},
            "database": {"path": "data/test.db"},
        }

    def test_volume_spike_threshold_too_low(self):
        """volume_spike_threshold must be > 1.0."""
        config = self._create_config_with_patterns({"volume_spike_threshold": 0.5})
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = f.name

        try:
            result = validate_config(config_path)
            assert not result.is_valid
            assert "volume_spike_threshold" in str(result)
            assert "> 1.0" in str(result)
        finally:
            Path(config_path).unlink()

    def test_volume_spike_threshold_equals_one(self):
        """volume_spike_threshold must be > 1.0 (not equal)."""
        config = self._create_config_with_patterns({"volume_spike_threshold": 1.0})
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = f.name

        try:
            result = validate_config(config_path)
            assert not result.is_valid
            assert "volume_spike_threshold" in str(result)
        finally:
            Path(config_path).unlink()

    def test_invalid_min_articles(self):
        """min_articles_for_alert must be >= 1."""
        config = self._create_config_with_patterns({"min_articles_for_alert": 0})
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = f.name

        try:
            result = validate_config(config_path)
            assert not result.is_valid
            assert "min_articles_for_alert" in str(result)
        finally:
            Path(config_path).unlink()

    def test_invalid_window_value(self):
        """Window values must be > 0."""
        config = self._create_config_with_patterns({"windows": {"short": -1}})
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = f.name

        try:
            result = validate_config(config_path)
            assert not result.is_valid
            assert "windows.short" in str(result)
        finally:
            Path(config_path).unlink()


class TestCompaniesValidation:
    """Tests for companies section validation."""

    def _create_config_with_companies(self, companies_config):
        """Helper to create a minimal valid config with custom companies section."""
        return {
            "scraping": {"timeout": 30},
            "sources": {"test": {"enabled": True, "rss_feeds": ["https://example.com/feed"]}},
            "patterns": {"volume_spike_threshold": 3.0},
            "companies": companies_config,
            "alerts": {"console": True},
            "database": {"path": "data/test.db"},
        }

    def test_missing_watchlist(self):
        """companies.watchlist is required."""
        config = self._create_config_with_companies({})
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = f.name

        try:
            result = validate_config(config_path)
            assert not result.is_valid
            assert "watchlist" in str(result)
            assert "required" in str(result).lower()
        finally:
            Path(config_path).unlink()

    def test_empty_watchlist(self):
        """watchlist must contain at least one company."""
        config = self._create_config_with_companies({"watchlist": {}})
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = f.name

        try:
            result = validate_config(config_path)
            assert not result.is_valid
            assert "watchlist" in str(result)
            assert "at least one" in str(result).lower()
        finally:
            Path(config_path).unlink()

    def test_invalid_ticker_format(self):
        """Ticker must be 1-5 uppercase letters."""
        config = self._create_config_with_companies(
            {"watchlist": {"invalid_ticker": ["Company Name"]}}
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = f.name

        try:
            result = validate_config(config_path)
            assert not result.is_valid
            assert "valid stock ticker" in str(result)
        finally:
            Path(config_path).unlink()

    def test_empty_name_list(self):
        """Each ticker must have at least one name pattern."""
        config = self._create_config_with_companies({"watchlist": {"AAPL": []}})
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = f.name

        try:
            result = validate_config(config_path)
            assert not result.is_valid
            assert "at least one name pattern" in str(result)
        finally:
            Path(config_path).unlink()

    def test_names_must_be_list(self):
        """Ticker names must be a list."""
        config = self._create_config_with_companies(
            {
                "watchlist": {"AAPL": "Apple"}  # String instead of list
            }
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = f.name

        try:
            result = validate_config(config_path)
            assert not result.is_valid
            assert "must be a list" in str(result)
        finally:
            Path(config_path).unlink()

    def test_valid_ticker_with_dot(self):
        """Tickers like BRK.A should be valid."""
        config = self._create_config_with_companies(
            {"watchlist": {"BRK.A": ["Berkshire Hathaway"]}}
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = f.name

        try:
            result = validate_config(config_path)
            # Should not have ticker-related errors
            ticker_errors = [e for e in result.errors if "BRK.A" in str(e)]
            assert len(ticker_errors) == 0
        finally:
            Path(config_path).unlink()


class TestAlertsValidation:
    """Tests for alerts section validation."""

    def _create_config_with_alerts(self, alerts_config):
        """Helper to create a minimal valid config with custom alerts section."""
        return {
            "scraping": {"timeout": 30},
            "sources": {"test": {"enabled": True, "rss_feeds": ["https://example.com/feed"]}},
            "patterns": {"volume_spike_threshold": 3.0},
            "companies": {"watchlist": {"AAPL": ["Apple"]}},
            "alerts": alerts_config,
            "database": {"path": "data/test.db"},
        }

    def test_invalid_console_type(self):
        """console must be a boolean."""
        config = self._create_config_with_alerts({"console": "yes"})
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = f.name

        try:
            result = validate_config(config_path)
            assert not result.is_valid
            assert "console" in str(result)
            assert "boolean" in str(result)
        finally:
            Path(config_path).unlink()

    def test_invalid_telegram_token_format(self):
        """Telegram token must match expected format when enabled."""
        config = self._create_config_with_alerts(
            {
                "telegram": {
                    "enabled": True,
                    "bot_token": "invalid-token-format",
                    "chat_id": "123456",
                }
            }
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = f.name

        try:
            result = validate_config(config_path)
            assert not result.is_valid
            assert "bot_token" in str(result)
            assert "invalid format" in str(result)
        finally:
            Path(config_path).unlink()

    def test_telegram_env_var_placeholder_allowed(self):
        """Telegram token with ${ENV_VAR} placeholder should be allowed."""
        config = self._create_config_with_alerts(
            {
                "telegram": {
                    "enabled": True,
                    "bot_token": "${TELEGRAM_BOT_TOKEN}",
                    "chat_id": "${TELEGRAM_CHAT_ID}",
                }
            }
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = f.name

        try:
            result = validate_config(config_path)
            # Should not have telegram token format errors
            token_errors = [
                e for e in result.errors if "bot_token" in str(e) and "format" in str(e)
            ]
            assert len(token_errors) == 0
        finally:
            Path(config_path).unlink()

    def test_invalid_webhook_url(self):
        """Webhook URL must be valid when enabled."""
        config = self._create_config_with_alerts({"webhook": {"enabled": True, "url": "not-a-url"}})
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = f.name

        try:
            result = validate_config(config_path)
            assert not result.is_valid
            assert "webhook" in str(result).lower()
            assert "url" in str(result).lower()
        finally:
            Path(config_path).unlink()


class TestDatabaseValidation:
    """Tests for database section validation."""

    def _create_config_with_database(self, database_config):
        """Helper to create a minimal valid config with custom database section."""
        return {
            "scraping": {"timeout": 30},
            "sources": {"test": {"enabled": True, "rss_feeds": ["https://example.com/feed"]}},
            "patterns": {"volume_spike_threshold": 3.0},
            "companies": {"watchlist": {"AAPL": ["Apple"]}},
            "alerts": {"console": True},
            "database": database_config,
        }

    def test_missing_database_path(self):
        """database.path is required."""
        config = self._create_config_with_database({})
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = f.name

        try:
            result = validate_config(config_path)
            assert not result.is_valid
            assert "database.path" in str(result)
            assert "required" in str(result).lower()
        finally:
            Path(config_path).unlink()

    def test_invalid_retention_days(self):
        """retention_days must be >= 1."""
        config = self._create_config_with_database({"path": "data/test.db", "retention_days": 0})
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = f.name

        try:
            result = validate_config(config_path)
            assert not result.is_valid
            assert "retention_days" in str(result)
        finally:
            Path(config_path).unlink()


class TestValidConfigPasses:
    """Tests that valid configurations pass validation."""

    def test_minimal_valid_config(self):
        """A minimal valid configuration should pass."""
        config = {
            "scraping": {
                "delay_min": 0.1,
                "delay_max": 0.5,
                "timeout": 30,
            },
            "sources": {
                "test_source": {
                    "enabled": True,
                    "name": "Test Source",
                    "rss_feeds": ["https://example.com/feed.xml"],
                }
            },
            "patterns": {
                "volume_spike_threshold": 3.0,
                "min_articles_for_alert": 3,
            },
            "companies": {
                "watchlist": {
                    "AAPL": ["Apple", "AAPL"],
                    "MSFT": ["Microsoft"],
                }
            },
            "alerts": {
                "console": True,
            },
            "database": {
                "path": "data/news.db",
                "retention_days": 30,
            },
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = f.name

        try:
            result = validate_config(config_path)
            assert result.is_valid, f"Expected valid config but got errors: {result}"
        finally:
            Path(config_path).unlink()

    def test_actual_settings_file(self):
        """The actual settings.yaml file should be valid."""
        config_path = Path(__file__).parent.parent / "config" / "settings.yaml"
        if config_path.exists():
            result = validate_config(str(config_path))
            assert result.is_valid, f"settings.yaml has validation errors: {result}"


class TestMultipleErrors:
    """Tests for handling multiple validation errors."""

    def test_multiple_errors_reported(self):
        """Multiple errors should all be reported."""
        config = {
            "scraping": {"timeout": -1},  # Error 1
            "sources": {},  # Error 2
            "patterns": {"volume_spike_threshold": 0.5},  # Error 3
            "companies": {"watchlist": {}},  # Error 4
            "alerts": {"console": "not a bool"},  # Error 5
            "database": {},  # Error 6: missing path
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = f.name

        try:
            result = validate_config(config_path)
            assert not result.is_valid
            # Should have multiple errors
            assert len(result.errors) >= 4
        finally:
            Path(config_path).unlink()
