"""
Alert notification system
"""

import json
import os
import time
from datetime import datetime
from typing import List, Dict, Any
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


class AlertManager:
    """Manage alert notifications"""

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

    def send_alerts(self, pattern_alerts: list[PatternAlert]):
        """Send all pattern alerts through configured channels"""
        for alert in pattern_alerts:
            self._send_alert(alert)

    def _send_alert(self, alert: PatternAlert):
        """Send a single alert through all channels"""
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

        # Console output
        if self.console_enabled:
            self._console_alert(alert)

        # File output
        if self.file_enabled:
            self._file_alert(alert)

        # Telegram
        if self.telegram_enabled and self.telegram_token and self.telegram_chat_id:
            self._telegram_alert(alert)

        # Webhook
        if self.webhook_enabled and self.webhook_url:
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
