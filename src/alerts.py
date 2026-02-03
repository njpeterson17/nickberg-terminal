"""
Alert notification system with aggregation and priority-based routing
"""

import json
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from pathlib import Path

import requests
from requests.exceptions import RequestException, Timeout, ConnectionError, HTTPError

from database import Database, Alert
from pattern_detector import PatternAlert
from logging_config import get_logger

logger = get_logger(__name__)

# Retry configuration
DEFAULT_MAX_RETRIES = 3
DEFAULT_INITIAL_DELAY = 1.0  # seconds
DEFAULT_TIMEOUT = 30  # seconds

# Default aggregation window in minutes
DEFAULT_AGGREGATION_WINDOW = 30


class AggregatedAlert:
    """Represents a group of related alerts for a single company"""

    def __init__(self, ticker: str, company_name: str):
        self.ticker = ticker
        self.company_name = company_name
        self.alerts: list[PatternAlert] = []
        self.first_alert_time: datetime | None = None
        self.last_alert_time: datetime | None = None

    def add_alert(self, alert: PatternAlert) -> None:
        """Add an alert to this aggregated group"""
        self.alerts.append(alert)
        now = datetime.now()
        if self.first_alert_time is None:
            self.first_alert_time = now
        self.last_alert_time = now

    @property
    def count(self) -> int:
        """Total number of alerts in this group"""
        return len(self.alerts)

    @property
    def highest_severity(self) -> str:
        """Get the highest severity among all alerts"""
        severity_order = {"high": 3, "medium": 2, "low": 1}
        max_severity = max(self.alerts, key=lambda a: severity_order.get(a.severity, 0))
        return max_severity.severity

    def get_type_counts(self) -> dict[str, int]:
        """Get count of alerts by type"""
        counts: dict[str, int] = defaultdict(int)
        for alert in self.alerts:
            counts[alert.pattern_type] += 1
        return dict(counts)

    def to_summary_message(self) -> str:
        """Create a summary message for the aggregated alerts"""
        type_counts = self.get_type_counts()
        type_parts = []
        for alert_type, count in type_counts.items():
            # Make type name more readable
            readable_type = alert_type.replace("_", " ")
            type_parts.append(f"{count} {readable_type}")

        type_summary = ", ".join(type_parts)
        return f"{self.ticker}: {self.count} alerts ({type_summary})"

    def to_pattern_alert(self) -> PatternAlert:
        """Convert aggregated alerts to a single PatternAlert for sending"""
        # Collect all details from individual alerts
        all_details = []
        for alert in self.alerts:
            all_details.append(
                {
                    "type": alert.pattern_type,
                    "severity": alert.severity,
                    "message": alert.message,
                    "details": alert.details,
                }
            )

        return PatternAlert(
            pattern_type="aggregated",
            ticker=self.ticker,
            company_name=self.company_name,
            severity=self.highest_severity,
            message=self.to_summary_message(),
            details={
                "alert_count": self.count,
                "type_breakdown": self.get_type_counts(),
                "individual_alerts": all_details,
            },
        )


class AlertAggregator:
    """
    Groups related alerts by company ticker within a time window.

    Instead of sending 5 separate AAPL alerts, creates one summary:
    "AAPL: 5 alerts (2 volume spikes, 2 sentiment shifts, 1 momentum)"
    """

    def __init__(self, config: dict[str, Any]):
        aggregation_config = config.get("aggregation", {})
        self.enabled = aggregation_config.get("enabled", False)
        self.window_minutes = aggregation_config.get("window_minutes", DEFAULT_AGGREGATION_WINDOW)

        # Pending alerts grouped by ticker
        self._pending: dict[str, AggregatedAlert] = {}
        # Track when each group was started
        self._group_start_times: dict[str, datetime] = {}

    def add_alert(self, alert: PatternAlert) -> list[PatternAlert] | None:
        """
        Add an alert to the aggregator.

        Returns:
            List of PatternAlerts to send immediately if aggregation is disabled,
            or if a group's time window has expired. Returns None if the alert
            was added to a pending group.
        """
        if not self.enabled:
            # Aggregation disabled - return alert immediately
            return [alert]

        ticker = alert.ticker
        now = datetime.now()

        # Check if existing group has expired
        if ticker in self._group_start_times:
            group_start = self._group_start_times[ticker]
            if now - group_start >= timedelta(minutes=self.window_minutes):
                # Window expired - flush existing group and start new one
                expired_alerts = self.flush_ticker(ticker)
                # Start new group with current alert
                self._start_new_group(ticker, alert)
                return expired_alerts

        # Add to existing or new group
        if ticker not in self._pending:
            self._start_new_group(ticker, alert)
        else:
            self._pending[ticker].add_alert(alert)

        return None

    def _start_new_group(self, ticker: str, alert: PatternAlert) -> None:
        """Start a new aggregation group for a ticker"""
        self._pending[ticker] = AggregatedAlert(ticker=ticker, company_name=alert.company_name)
        self._pending[ticker].add_alert(alert)
        self._group_start_times[ticker] = datetime.now()

    def flush_ticker(self, ticker: str) -> list[PatternAlert]:
        """
        Flush all pending alerts for a specific ticker.

        Returns aggregated alert(s) for sending.
        """
        if ticker not in self._pending:
            return []

        group = self._pending.pop(ticker)
        self._group_start_times.pop(ticker, None)

        if group.count == 1:
            # Only one alert - return original
            return group.alerts
        else:
            # Multiple alerts - return aggregated
            return [group.to_pattern_alert()]

    def flush_all(self) -> list[PatternAlert]:
        """
        Flush all pending alerts from all tickers.

        Returns list of alerts to send (aggregated where applicable).
        """
        alerts_to_send = []
        tickers = list(self._pending.keys())

        for ticker in tickers:
            alerts_to_send.extend(self.flush_ticker(ticker))

        return alerts_to_send

    def flush_expired(self) -> list[PatternAlert]:
        """
        Flush only groups whose time window has expired.

        Returns list of alerts to send.
        """
        if not self.enabled:
            return []

        now = datetime.now()
        alerts_to_send = []
        expired_tickers = []

        for ticker, start_time in self._group_start_times.items():
            if now - start_time >= timedelta(minutes=self.window_minutes):
                expired_tickers.append(ticker)

        for ticker in expired_tickers:
            alerts_to_send.extend(self.flush_ticker(ticker))

        return alerts_to_send

    def get_pending_count(self) -> int:
        """Get total number of pending alerts across all groups"""
        return sum(group.count for group in self._pending.values())

    def get_pending_tickers(self) -> list[str]:
        """Get list of tickers with pending alerts"""
        return list(self._pending.keys())


class AlertManager:
    """Manage alert notifications with aggregation and priority-based routing"""

    def __init__(self, config: dict[str, Any], db: Database):
        self.config = config
        self.db = db
        self.console_enabled = config.get("console", True)
        self.file_enabled = config.get("file", {}).get("enabled", False)
        self.file_path = config.get("file", {}).get("path", "logs/alerts.log")

        # Telegram config
        self.telegram_enabled = config.get("telegram", {}).get("enabled", False)
        self.telegram_token = os.getenv(
            "NEWS_BOT_TELEGRAM_TOKEN", config.get("telegram", {}).get("bot_token", "")
        )
        self.telegram_chat_id = os.getenv(
            "NEWS_BOT_TELEGRAM_CHAT_ID", config.get("telegram", {}).get("chat_id", "")
        )

        # Webhook config
        self.webhook_enabled = config.get("webhook", {}).get("enabled", False)
        self.webhook_url = os.getenv(
            "NEWS_BOT_WEBHOOK_URL", config.get("webhook", {}).get("url", "")
        )

        # Routing configuration
        self.routing_config = config.get("routing", {}) or {}
        self.company_overrides = config.get("company_overrides", {}) or {}

        # Alert aggregator
        self.aggregator = AlertAggregator(config)

        if self.file_enabled:
            Path(self.file_path).parent.mkdir(parents=True, exist_ok=True)

    def _retry_with_backoff(
        self,
        func,
        description: str,
        max_retries: int = DEFAULT_MAX_RETRIES,
        initial_delay: float = DEFAULT_INITIAL_DELAY,
    ) -> bool:
        """
        Execute a function with exponential backoff retry logic.

        Args:
            func: Callable that may raise exceptions
            description: Human-readable description for logging
            max_retries: Maximum number of retry attempts
            initial_delay: Initial delay in seconds (doubles each retry)

        Returns:
            True if successful, False if all retries exhausted
        """
        last_exception = None

        for attempt in range(max_retries):
            try:
                func()
                return True
            except Timeout as e:
                last_exception = e
                logger.warning(
                    "Alert request timed out",
                    extra={
                        "description": description,
                        "attempt": attempt + 1,
                        "max_attempts": max_retries,
                    },
                )
            except ConnectionError as e:
                last_exception = e
                logger.warning(
                    "Alert connection failed",
                    extra={
                        "description": description,
                        "attempt": attempt + 1,
                        "max_attempts": max_retries,
                        "error": str(e),
                    },
                )
            except HTTPError as e:
                last_exception = e
                # Don't retry on 4xx client errors (except 429 rate limit)
                if e.response is not None and 400 <= e.response.status_code < 500:
                    if e.response.status_code != 429:
                        logger.error(
                            "Alert failed with client error",
                            extra={
                                "description": description,
                                "status_code": e.response.status_code,
                                "error": str(e),
                            },
                        )
                        return False
                logger.warning(
                    "Alert HTTP error",
                    extra={
                        "description": description,
                        "attempt": attempt + 1,
                        "max_attempts": max_retries,
                        "error": str(e),
                    },
                )
            except RequestException as e:
                last_exception = e
                logger.warning(
                    "Alert request failed",
                    extra={
                        "description": description,
                        "attempt": attempt + 1,
                        "max_attempts": max_retries,
                        "error": str(e),
                    },
                )

            # Calculate delay with exponential backoff
            if attempt < max_retries - 1:
                delay = initial_delay * (2**attempt)
                logger.debug(
                    "Retrying alert",
                    extra={"description": description, "delay_seconds": round(delay, 1)},
                )
                time.sleep(delay)

        # All retries exhausted
        logger.error(
            "Alert failed after all retries",
            extra={
                "description": description,
                "attempts": max_retries,
                "last_error": str(last_exception),
            },
        )
        return False

    def send_alerts(self, pattern_alerts: list[PatternAlert], flush: bool = True):
        """
        Send all pattern alerts through configured channels.

        Args:
            pattern_alerts: List of alerts to send
            flush: If True, flush all pending aggregated alerts after processing.
                   Set to False if you want to manually control flushing.
        """
        alerts_to_send = []

        for alert in pattern_alerts:
            # Add to aggregator - may return alerts to send immediately
            immediate = self.aggregator.add_alert(alert)
            if immediate:
                alerts_to_send.extend(immediate)

        # Send any alerts that need to go out now
        for alert in alerts_to_send:
            self._send_alert(alert)

        # Optionally flush all remaining aggregated alerts
        if flush:
            flushed = self.aggregator.flush_all()
            for alert in flushed:
                self._send_alert(alert)

    def flush_aggregated_alerts(self) -> int:
        """
        Flush all pending aggregated alerts.

        Returns the number of alerts sent.
        """
        flushed = self.aggregator.flush_all()
        for alert in flushed:
            self._send_alert(alert)
        return len(flushed)

    def flush_expired_alerts(self) -> int:
        """
        Flush only aggregated alerts whose time window has expired.

        Returns the number of alerts sent.
        """
        flushed = self.aggregator.flush_expired()
        for alert in flushed:
            self._send_alert(alert)
        return len(flushed)

    def get_channels_for_alert(self, alert: PatternAlert) -> list[str]:
        """
        Determine which channels should receive this alert based on routing rules.

        Priority order:
        1. Company-specific overrides (if configured)
        2. Severity-based routing (if configured)
        3. All enabled channels (default/backwards compatible)

        Returns a list of channel names: 'console', 'file', 'telegram', 'webhook'
        """
        ticker = alert.ticker
        severity = alert.severity

        # Check for company-specific override first
        if ticker in self.company_overrides:
            company_config = self.company_overrides[ticker]
            if "channels" in company_config:
                return company_config["channels"]

        # Check severity-based routing
        severity_key = f"{severity}_severity"
        if severity_key in self.routing_config:
            return self.routing_config[severity_key]

        # Default: return all enabled channels (backwards compatible)
        channels = []
        if self.console_enabled:
            channels.append("console")
        if self.file_enabled:
            channels.append("file")
        if self.telegram_enabled and self.telegram_token and self.telegram_chat_id:
            channels.append("telegram")
        if self.webhook_enabled and self.webhook_url:
            channels.append("webhook")

        return channels

    def _send_alert(self, alert: PatternAlert):
        """Send a single alert through routed channels"""
        # Save to database first
        db_alert = Alert(
            id=None,
            alert_type=alert.pattern_type,
            company_ticker=alert.ticker,
            company_name=alert.company_name,
            severity=alert.severity,
            message=alert.message,
            details=json.dumps(alert.details),
            created_at=datetime.now(),
        )

        alert_id = self.db.save_alert(db_alert)
        if alert_id is None:
            # Duplicate or error
            return

        # Get channels for this alert based on routing rules
        channels = self.get_channels_for_alert(alert)

        # Console output
        if "console" in channels and self.console_enabled:
            self._console_alert(alert)

        # File output
        if "file" in channels and self.file_enabled:
            self._file_alert(alert)

        # Telegram
        if (
            "telegram" in channels
            and self.telegram_enabled
            and self.telegram_token
            and self.telegram_chat_id
        ):
            self._telegram_alert(alert)

        # Webhook
        if "webhook" in channels and self.webhook_enabled and self.webhook_url:
            self._webhook_alert(alert)

    def _console_alert(self, alert: PatternAlert):
        """Print alert to console"""
        emoji_map = {"high": "🚨", "medium": "⚠️", "low": "ℹ️"}

        type_emoji = {
            "volume_spike": "📈",
            "sentiment_shift": "🎭",
            "momentum": "🚀",
            "negative_cluster": "⚡",
        }

        emoji = emoji_map.get(alert.severity, "•")
        type_icon = type_emoji.get(alert.pattern_type, "📰")

        print(f"\n{emoji} {type_icon} [{alert.severity.upper()}] {alert.pattern_type}")
        print(f"   {alert.message}")

        if alert.details:
            print(f"   Details: {json.dumps(alert.details, indent=2)}")
        print()

    def _file_alert(self, alert: PatternAlert):
        """Write alert to file"""
        try:
            with open(self.file_path, "a") as f:
                entry = {
                    "timestamp": datetime.now().isoformat(),
                    "severity": alert.severity,
                    "type": alert.pattern_type,
                    "ticker": alert.ticker,
                    "message": alert.message,
                    "details": alert.details,
                }
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.error(
                "Failed to write alert to file",
                extra={"alert_type": alert.pattern_type, "ticker": alert.ticker, "error": str(e)},
            )

    def _telegram_alert(self, alert: PatternAlert):
        """Send alert via Telegram with retry logic"""
        emoji_map = {"high": "🚨", "medium": "⚠️", "low": "ℹ️"}

        emoji = emoji_map.get(alert.severity, "•")

        message = f"{emoji} *{alert.severity.upper()}* - {alert.pattern_type.replace('_', ' ').title()}\n\n"
        message += f"{alert.message}\n\n"

        if alert.details:
            message += "*Details:*\n"
            for key, value in alert.details.items():
                message += f"• {key}: {value}\n"

        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        payload = {"chat_id": self.telegram_chat_id, "text": message, "parse_mode": "Markdown"}

        def send_request():
            response = requests.post(url, json=payload, timeout=DEFAULT_TIMEOUT)
            response.raise_for_status()

        success = self._retry_with_backoff(
            send_request, description=f"Telegram alert for {alert.ticker}"
        )

        if success:
            logger.info(
                "Telegram alert sent",
                extra={
                    "alert_type": "telegram",
                    "ticker": alert.ticker,
                    "severity": alert.severity,
                },
            )

    def _webhook_alert(self, alert: PatternAlert):
        """Send alert to webhook with retry logic"""
        payload = {"timestamp": datetime.now().isoformat(), "alert": alert.to_dict()}

        def send_request():
            response = requests.post(
                self.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=DEFAULT_TIMEOUT,
            )
            response.raise_for_status()

        success = self._retry_with_backoff(
            send_request, description=f"Webhook alert for {alert.ticker}"
        )

        if success:
            logger.info(
                "Webhook alert sent",
                extra={"alert_type": "webhook", "ticker": alert.ticker, "severity": alert.severity},
            )

    def get_recent_alerts(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get recent unacknowledged alerts"""
        alerts = self.db.get_unacknowledged_alerts(limit)
        return [
            {
                "id": a.id,
                "type": a.alert_type,
                "ticker": a.company_ticker,
                "company": a.company_name,
                "severity": a.severity,
                "message": a.message,
                "created_at": a.created_at.isoformat(),
            }
            for a in alerts
        ]

    def acknowledge_alert(self, alert_id: int):
        """Mark alert as acknowledged"""
        with self.db.get_connection() as conn:
            conn.execute("UPDATE alerts SET acknowledged = TRUE WHERE id = ?", (alert_id,))
            conn.commit()
