"""
Structured JSON logging configuration for News Sentinel Bot.

Supports both JSON (for production) and human-readable (for development) formats.
Configure via environment variables:
- LOG_FORMAT: 'json' or 'text' (default: 'text')
- LOG_LEVEL: DEBUG, INFO, WARNING, ERROR (default: 'INFO')
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone, UTC
from pathlib import Path
from typing import Any, Dict, Optional


class JSONFormatter(logging.Formatter):
    """
    Custom JSON formatter for structured logging.

    Outputs log records as single-line JSON objects with:
    - timestamp (ISO 8601 format with timezone)
    - level (log level name)
    - logger (logger name)
    - message (log message)
    - Any extra context fields passed to the log call
    """

    def format(self, record: logging.LogRecord) -> str:
        log_data: dict[str, Any] = {
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add extra context fields (skip standard LogRecord attributes)
        standard_attrs = {
            "name",
            "msg",
            "args",
            "created",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "module",
            "msecs",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "exc_info",
            "exc_text",
            "thread",
            "threadName",
            "taskName",
            "message",
        }

        for key, value in record.__dict__.items():
            if key not in standard_attrs and not key.startswith("_"):
                # Handle non-serializable objects
                try:
                    json.dumps(value)
                    log_data[key] = value
                except (TypeError, ValueError):
                    log_data[key] = str(value)

        return json.dumps(log_data)


class TextFormatter(logging.Formatter):
    """
    Human-readable formatter for development.

    Format: timestamp - logger - level - message [context_key=value ...]
    """

    def format(self, record: logging.LogRecord) -> str:
        # Build base message
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        base_msg = f"{timestamp} - {record.name} - {record.levelname} - {record.getMessage()}"

        # Collect extra context fields
        standard_attrs = {
            "name",
            "msg",
            "args",
            "created",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "module",
            "msecs",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "exc_info",
            "exc_text",
            "thread",
            "threadName",
            "taskName",
            "message",
        }

        extra_parts = []
        for key, value in record.__dict__.items():
            if key not in standard_attrs and not key.startswith("_"):
                extra_parts.append(f"{key}={value}")

        if extra_parts:
            base_msg += f" [{', '.join(extra_parts)}]"

        # Add exception info if present
        if record.exc_info:
            base_msg += f"\n{self.formatException(record.exc_info)}"

        return base_msg


def get_log_level() -> int:
    """Get log level from environment variable."""
    level_str = os.environ.get("LOG_LEVEL", "INFO").upper()
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    return level_map.get(level_str, logging.INFO)


def get_log_format() -> str:
    """Get log format from environment variable."""
    return os.environ.get("LOG_FORMAT", "text").lower()


def setup_logging(
    log_dir: str = "logs",
    verbose: bool = False,
    log_format: str | None = None,
    log_level: int | None = None,
) -> None:
    """
    Setup logging configuration with support for JSON or text format.

    Args:
        log_dir: Directory for log files (created if doesn't exist)
        verbose: If True, sets level to DEBUG (overrides LOG_LEVEL env var)
        log_format: 'json' or 'text' (overrides LOG_FORMAT env var)
        log_level: Logging level (overrides LOG_LEVEL env var and verbose flag)
    """
    Path(log_dir).mkdir(exist_ok=True)

    # Determine log level
    if log_level is not None:
        level = log_level
    elif verbose:
        level = logging.DEBUG
    else:
        level = get_log_level()

    # Determine log format
    fmt = log_format if log_format is not None else get_log_format()

    # Create formatter based on format type
    if fmt == "json":
        formatter = JSONFormatter()
    else:
        formatter = TextFormatter()

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # File handler
    file_handler = logging.FileHandler(f"{log_dir}/bot.log")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # Reduce noise from external libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance with the specified name.

    This is a convenience function that returns a standard logger.
    Use extra={'key': 'value'} when logging to add context fields.

    Example:
        logger = get_logger(__name__)
        logger.info("Fetched feed", extra={'source': 'reuters', 'articles': 15})

    Args:
        name: Logger name (typically __name__)

    Returns:
        Logger instance
    """
    return logging.getLogger(name)
