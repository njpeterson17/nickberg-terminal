"""
Configuration validation for News Sentinel Bot.

Validates config/settings.yaml on startup with clear, actionable error messages.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


@dataclass
class ValidationError:
    """Represents a single validation error."""

    path: str
    message: str
    value: Any = None

    def __str__(self) -> str:
        if self.value is not None:
            return f"settings.yaml:{self.path} {self.message}, got {type(self.value).__name__}: {self.value!r}"
        return f"settings.yaml:{self.path} {self.message}"


@dataclass
class ValidationResult:
    """Result of configuration validation."""

    errors: list[ValidationError] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def add_error(self, path: str, message: str, value: Any = None) -> None:
        self.errors.append(ValidationError(path, message, value))

    def __str__(self) -> str:
        if self.is_valid:
            return "Configuration is valid"
        lines = ["Configuration validation failed:"]
        for error in self.errors:
            lines.append(f"  - {error}")
        return "\n".join(lines)


class ConfigValidator:
    """Validates the settings.yaml configuration file."""

    # Regex for valid stock ticker (1-5 uppercase letters, optionally with dots for BRK.A style)
    TICKER_PATTERN = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")

    # Regex for Telegram bot token format (numbers:alphanumeric)
    TELEGRAM_TOKEN_PATTERN = re.compile(r"^\d+:[A-Za-z0-9_-]+$")

    def __init__(self, config_path: str = "config/settings.yaml"):
        self.config_path = Path(config_path)
        self.result = ValidationResult()

    def validate(self) -> ValidationResult:
        """
        Validate the configuration file.

        Returns:
            ValidationResult with any errors found.
        """
        self.result = ValidationResult()

        # Check file exists
        if not self.config_path.exists():
            self.result.add_error("", f"Configuration file not found: {self.config_path}")
            return self.result

        # Load YAML
        try:
            with open(self.config_path) as f:
                config = yaml.safe_load(f)
        except yaml.YAMLError as e:
            self.result.add_error("", f"Invalid YAML syntax: {e}")
            return self.result

        if config is None:
            self.result.add_error("", "Configuration file is empty")
            return self.result

        # Validate each section
        self._validate_scraping(config)
        self._validate_sources(config)
        self._validate_patterns(config)
        self._validate_companies(config)
        self._validate_alerts(config)
        self._validate_database(config)
        self._validate_schedule(config)

        return self.result

    def _validate_scraping(self, config: dict) -> None:
        """Validate the scraping section."""
        scraping = config.get("scraping")
        if scraping is None:
            self.result.add_error("scraping", "is required")
            return

        if not isinstance(scraping, dict):
            self.result.add_error("scraping", "must be a mapping", scraping)
            return

        # Validate delay_min
        delay_min = scraping.get("delay_min")
        if delay_min is not None:
            if not isinstance(delay_min, (int, float)):
                self.result.add_error("scraping.delay_min", "must be a number", delay_min)
            elif delay_min < 0:
                self.result.add_error("scraping.delay_min", "must be >= 0", delay_min)

        # Validate delay_max
        delay_max = scraping.get("delay_max")
        if delay_max is not None:
            if not isinstance(delay_max, (int, float)):
                self.result.add_error("scraping.delay_max", "must be a number", delay_max)
            elif delay_max < 0:
                self.result.add_error("scraping.delay_max", "must be >= 0", delay_max)

        # Validate delay_min <= delay_max
        if (
            delay_min is not None
            and delay_max is not None
            and isinstance(delay_min, (int, float))
            and isinstance(delay_max, (int, float))
            and delay_min > delay_max
        ):
            self.result.add_error(
                "scraping.delay_min",
                f"must be <= delay_max ({delay_max})",
                delay_min,
            )

        # Validate timeout
        timeout = scraping.get("timeout")
        if timeout is not None:
            if not isinstance(timeout, (int, float)):
                self.result.add_error("scraping.timeout", "must be a number", timeout)
            elif timeout <= 0:
                self.result.add_error("scraping.timeout", "must be > 0", timeout)

        # Validate max_retries
        max_retries = scraping.get("max_retries")
        if max_retries is not None:
            if not isinstance(max_retries, int):
                self.result.add_error("scraping.max_retries", "must be an integer", max_retries)
            elif max_retries < 0:
                self.result.add_error("scraping.max_retries", "must be >= 0", max_retries)

        # Validate user_agents
        user_agents = scraping.get("user_agents")
        if user_agents is not None:
            if not isinstance(user_agents, list):
                self.result.add_error("scraping.user_agents", "must be a list", user_agents)
            elif len(user_agents) == 0:
                self.result.add_error("scraping.user_agents", "must not be empty")
            else:
                for i, ua in enumerate(user_agents):
                    if not isinstance(ua, str):
                        self.result.add_error(f"scraping.user_agents[{i}]", "must be a string", ua)

        # Validate rate_limiting
        rate_limiting = scraping.get("rate_limiting")
        if rate_limiting is not None:
            if not isinstance(rate_limiting, dict):
                self.result.add_error("scraping.rate_limiting", "must be a mapping", rate_limiting)
            else:
                per_domain_delay = rate_limiting.get("per_domain_delay")
                if per_domain_delay is not None:
                    if not isinstance(per_domain_delay, (int, float)):
                        self.result.add_error(
                            "scraping.rate_limiting.per_domain_delay",
                            "must be a number",
                            per_domain_delay,
                        )
                    elif per_domain_delay < 0:
                        self.result.add_error(
                            "scraping.rate_limiting.per_domain_delay",
                            "must be >= 0",
                            per_domain_delay,
                        )

    def _validate_sources(self, config: dict) -> None:
        """Validate the sources section."""
        sources = config.get("sources")
        if sources is None:
            self.result.add_error("sources", "is required")
            return

        if not isinstance(sources, dict):
            self.result.add_error("sources", "must be a mapping", sources)
            return

        if len(sources) == 0:
            self.result.add_error("sources", "must contain at least one source")
            return

        # Check at least one source is enabled
        enabled_count = 0
        for source_name, source_config in sources.items():
            if not isinstance(source_config, dict):
                self.result.add_error(f"sources.{source_name}", "must be a mapping", source_config)
                continue

            # Validate enabled field
            enabled = source_config.get("enabled", True)
            if not isinstance(enabled, bool):
                self.result.add_error(
                    f"sources.{source_name}.enabled", "must be a boolean", enabled
                )
            elif enabled:
                enabled_count += 1

            # Validate name field
            name = source_config.get("name")
            if name is not None and not isinstance(name, str):
                self.result.add_error(f"sources.{source_name}.name", "must be a string", name)

            # Validate rss_feeds
            rss_feeds = source_config.get("rss_feeds")
            if rss_feeds is None:
                self.result.add_error(f"sources.{source_name}.rss_feeds", "is required")
            elif not isinstance(rss_feeds, list):
                self.result.add_error(
                    f"sources.{source_name}.rss_feeds", "must be a list", rss_feeds
                )
            elif len(rss_feeds) == 0:
                self.result.add_error(f"sources.{source_name}.rss_feeds", "must not be empty")
            else:
                for i, feed_url in enumerate(rss_feeds):
                    if not isinstance(feed_url, str):
                        self.result.add_error(
                            f"sources.{source_name}.rss_feeds[{i}]",
                            "must be a string",
                            feed_url,
                        )
                    elif not self._is_valid_url(feed_url):
                        self.result.add_error(
                            f"sources.{source_name}.rss_feeds[{i}]",
                            "must be a valid URL",
                            feed_url,
                        )

        if enabled_count == 0:
            self.result.add_error("sources", "at least one source must be enabled")

    def _validate_patterns(self, config: dict) -> None:
        """Validate the patterns section."""
        patterns = config.get("patterns")
        if patterns is None:
            self.result.add_error("patterns", "is required")
            return

        if not isinstance(patterns, dict):
            self.result.add_error("patterns", "must be a mapping", patterns)
            return

        # Validate volume_spike_threshold
        volume_spike = patterns.get("volume_spike_threshold")
        if volume_spike is not None:
            if not isinstance(volume_spike, (int, float)):
                self.result.add_error(
                    "patterns.volume_spike_threshold", "must be a number", volume_spike
                )
            elif volume_spike <= 1.0:
                self.result.add_error(
                    "patterns.volume_spike_threshold",
                    "must be > 1.0 (represents a multiplier)",
                    volume_spike,
                )

        # Validate min_articles_for_alert
        min_articles = patterns.get("min_articles_for_alert")
        if min_articles is not None:
            if not isinstance(min_articles, int):
                self.result.add_error(
                    "patterns.min_articles_for_alert", "must be an integer", min_articles
                )
            elif min_articles < 1:
                self.result.add_error(
                    "patterns.min_articles_for_alert", "must be >= 1", min_articles
                )

        # Validate windows
        windows = patterns.get("windows")
        if windows is not None:
            if not isinstance(windows, dict):
                self.result.add_error("patterns.windows", "must be a mapping", windows)
            else:
                for window_name in ["short", "medium", "long"]:
                    window_val = windows.get(window_name)
                    if window_val is not None:
                        if not isinstance(window_val, (int, float)):
                            self.result.add_error(
                                f"patterns.windows.{window_name}",
                                "must be a number",
                                window_val,
                            )
                        elif window_val <= 0:
                            self.result.add_error(
                                f"patterns.windows.{window_name}",
                                "must be > 0",
                                window_val,
                            )

        # Validate sentiment_keywords
        sentiment = patterns.get("sentiment_keywords")
        if sentiment is not None:
            if not isinstance(sentiment, dict):
                self.result.add_error("patterns.sentiment_keywords", "must be a mapping", sentiment)
            else:
                for category in ["positive", "negative"]:
                    keywords = sentiment.get(category)
                    if keywords is not None:
                        if not isinstance(keywords, list):
                            self.result.add_error(
                                f"patterns.sentiment_keywords.{category}",
                                "must be a list",
                                keywords,
                            )
                        else:
                            for i, kw in enumerate(keywords):
                                if not isinstance(kw, str):
                                    self.result.add_error(
                                        f"patterns.sentiment_keywords.{category}[{i}]",
                                        "must be a string",
                                        kw,
                                    )

    def _validate_companies(self, config: dict) -> None:
        """Validate the companies section."""
        companies = config.get("companies")
        if companies is None:
            self.result.add_error("companies", "is required")
            return

        if not isinstance(companies, dict):
            self.result.add_error("companies", "must be a mapping", companies)
            return

        # Validate watchlist
        watchlist = companies.get("watchlist")
        if watchlist is None:
            self.result.add_error("companies.watchlist", "is required")
        elif not isinstance(watchlist, dict):
            self.result.add_error("companies.watchlist", "must be a mapping", watchlist)
        elif len(watchlist) == 0:
            self.result.add_error("companies.watchlist", "must contain at least one company")
        else:
            for ticker, names in watchlist.items():
                # Validate ticker format
                if not self._is_valid_ticker(ticker):
                    self.result.add_error(
                        f"companies.watchlist.{ticker}",
                        "is not a valid stock ticker (expected 1-5 uppercase letters)",
                        ticker,
                    )

                # Validate names list
                if not isinstance(names, list):
                    self.result.add_error(
                        f"companies.watchlist.{ticker}",
                        "must be a list of name patterns",
                        names,
                    )
                elif len(names) == 0:
                    self.result.add_error(
                        f"companies.watchlist.{ticker}",
                        "must have at least one name pattern",
                    )
                else:
                    for i, name in enumerate(names):
                        if not isinstance(name, str):
                            self.result.add_error(
                                f"companies.watchlist.{ticker}[{i}]",
                                "must be a string",
                                name,
                            )

        # Validate auto_detect
        auto_detect = companies.get("auto_detect")
        if auto_detect is not None and not isinstance(auto_detect, bool):
            self.result.add_error("companies.auto_detect", "must be a boolean", auto_detect)

        # Validate auto_detect_threshold
        threshold = companies.get("auto_detect_threshold")
        if threshold is not None:
            if not isinstance(threshold, int):
                self.result.add_error(
                    "companies.auto_detect_threshold", "must be an integer", threshold
                )
            elif threshold < 1:
                self.result.add_error("companies.auto_detect_threshold", "must be >= 1", threshold)

    def _validate_alerts(self, config: dict) -> None:
        """Validate the alerts section."""
        alerts = config.get("alerts")
        if alerts is None:
            self.result.add_error("alerts", "is required")
            return

        if not isinstance(alerts, dict):
            self.result.add_error("alerts", "must be a mapping", alerts)
            return

        # Validate console
        console = alerts.get("console")
        if console is not None and not isinstance(console, bool):
            self.result.add_error("alerts.console", "must be a boolean", console)

        # Validate file alerts
        file_config = alerts.get("file")
        if file_config is not None:
            if not isinstance(file_config, dict):
                self.result.add_error("alerts.file", "must be a mapping", file_config)
            else:
                enabled = file_config.get("enabled")
                if enabled is not None and not isinstance(enabled, bool):
                    self.result.add_error("alerts.file.enabled", "must be a boolean", enabled)

                path = file_config.get("path")
                if path is not None and not isinstance(path, str):
                    self.result.add_error("alerts.file.path", "must be a string", path)

        # Validate telegram
        telegram = alerts.get("telegram")
        if telegram is not None:
            if not isinstance(telegram, dict):
                self.result.add_error("alerts.telegram", "must be a mapping", telegram)
            else:
                enabled = telegram.get("enabled")
                if enabled is not None and not isinstance(enabled, bool):
                    self.result.add_error("alerts.telegram.enabled", "must be a boolean", enabled)

                # Only validate token format if telegram is enabled and token is set
                if enabled is True:
                    bot_token = telegram.get("bot_token")
                    if bot_token is not None and isinstance(bot_token, str):
                        # Skip validation if it's an env var placeholder
                        if not bot_token.startswith("${") and not self._is_valid_telegram_token(
                            bot_token
                        ):
                            self.result.add_error(
                                "alerts.telegram.bot_token",
                                "has invalid format (expected: 123456789:ABCdefGHIjklMNOpqrsTUVwxyz)",
                                bot_token,
                            )

                    chat_id = telegram.get("chat_id")
                    if chat_id is not None:
                        # chat_id can be a string or integer, but should be provided
                        if not isinstance(chat_id, (str, int)):
                            self.result.add_error(
                                "alerts.telegram.chat_id",
                                "must be a string or integer",
                                chat_id,
                            )

        # Validate webhook
        webhook = alerts.get("webhook")
        if webhook is not None:
            if not isinstance(webhook, dict):
                self.result.add_error("alerts.webhook", "must be a mapping", webhook)
            else:
                enabled = webhook.get("enabled")
                if enabled is not None and not isinstance(enabled, bool):
                    self.result.add_error("alerts.webhook.enabled", "must be a boolean", enabled)

                url = webhook.get("url")
                if enabled is True and url is not None and isinstance(url, str):
                    # Skip validation if it's an env var placeholder
                    if not url.startswith("${") and not self._is_valid_url(url):
                        self.result.add_error("alerts.webhook.url", "must be a valid URL", url)

    def _validate_database(self, config: dict) -> None:
        """Validate the database section."""
        database = config.get("database")
        if database is None:
            self.result.add_error("database", "is required")
            return

        if not isinstance(database, dict):
            self.result.add_error("database", "must be a mapping", database)
            return

        # Validate path
        path = database.get("path")
        if path is None:
            self.result.add_error("database.path", "is required")
        elif not isinstance(path, str):
            self.result.add_error("database.path", "must be a string", path)

        # Validate retention_days
        retention = database.get("retention_days")
        if retention is not None:
            if not isinstance(retention, int):
                self.result.add_error("database.retention_days", "must be an integer", retention)
            elif retention < 1:
                self.result.add_error("database.retention_days", "must be >= 1", retention)

    def _validate_schedule(self, config: dict) -> None:
        """Validate the schedule section."""
        schedule = config.get("schedule")
        if schedule is None:
            # Schedule is optional
            return

        if not isinstance(schedule, dict):
            self.result.add_error("schedule", "must be a mapping", schedule)
            return

        # Validate interval_minutes
        interval = schedule.get("interval_minutes")
        if interval is not None:
            if not isinstance(interval, (int, float)):
                self.result.add_error("schedule.interval_minutes", "must be a number", interval)
            elif interval <= 0:
                self.result.add_error("schedule.interval_minutes", "must be > 0", interval)

    def _is_valid_url(self, url: str) -> bool:
        """Check if a string is a valid URL."""
        try:
            result = urlparse(url)
            return all([result.scheme in ("http", "https"), result.netloc])
        except Exception:
            return False

    def _is_valid_ticker(self, ticker: str) -> bool:
        """Check if a string is a valid stock ticker."""
        return bool(self.TICKER_PATTERN.match(ticker))

    def _is_valid_telegram_token(self, token: str) -> bool:
        """Check if a string matches the Telegram bot token format."""
        return bool(self.TELEGRAM_TOKEN_PATTERN.match(token))


def validate_config(config_path: str = "config/settings.yaml") -> ValidationResult:
    """
    Convenience function to validate a configuration file.

    Args:
        config_path: Path to the settings.yaml file.

    Returns:
        ValidationResult with any errors found.
    """
    validator = ConfigValidator(config_path)
    return validator.validate()


def validate_config_or_exit(config_path: str = "config/settings.yaml") -> dict:
    """
    Validate configuration and exit with error code 1 if invalid.

    Args:
        config_path: Path to the settings.yaml file.

    Returns:
        The loaded configuration dict if valid.

    Raises:
        SystemExit: If validation fails.
    """
    result = validate_config(config_path)

    if not result.is_valid:
        print(result, file=__import__("sys").stderr)
        raise SystemExit(1)

    # Load and return the config
    with open(config_path) as f:
        return yaml.safe_load(f)
