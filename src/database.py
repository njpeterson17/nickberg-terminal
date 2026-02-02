"""
Database module for News Sentinel Bot
Handles storage of articles, companies, and alerts
"""

import sqlite3
import json
import hashlib
import html
import re
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any
from collections.abc import Generator
from dataclasses import dataclass

from logging_config import get_logger

logger = get_logger(__name__)


class DatabaseTransactionError(Exception):
    """Raised when a database transaction fails and is rolled back."""

    pass


def sanitize_html(content: str) -> str:
    """
    Sanitize HTML content to prevent XSS attacks.

    This function:
    1. Escapes HTML special characters to prevent script injection
    2. Removes potentially dangerous patterns

    Args:
        content: Raw content that may contain HTML/scripts

    Returns:
        Sanitized content safe for storage and display
    """
    if not content:
        return content

    # First, escape HTML special characters
    sanitized = html.escape(content)

    # Remove any remaining script-like patterns (belt and suspenders approach)
    # These patterns catch attempts to bypass escaping
    dangerous_patterns = [
        r"javascript\s*:",  # javascript: URLs
        r"data\s*:",  # data: URLs (can contain scripts)
        r"vbscript\s*:",  # vbscript: URLs
        r"on\w+\s*=",  # Event handlers like onclick=, onerror=
    ]

    for pattern in dangerous_patterns:
        sanitized = re.sub(pattern, "", sanitized, flags=re.IGNORECASE)

    return sanitized


@dataclass
class Article:
    id: int | None
    url: str
    title: str
    content: str
    source: str
    published_at: datetime
    scraped_at: datetime
    sentiment_score: float | None = None
    mentions: str | None = None  # JSON string of mentioned companies
    content_hash: str | None = None  # SHA256 hash of title+content for deduplication

    @staticmethod
    def compute_content_hash(title: str, content: str) -> str:
        """Compute SHA256 hash of title+content for deduplication"""
        combined = f"{title.strip().lower()}|{content.strip().lower()}"
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "content": self.content[:500] + "..." if len(self.content) > 500 else self.content,
            "source": self.source,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "sentiment_score": self.sentiment_score,
            "mentions": json.loads(self.mentions) if self.mentions else [],
            "content_hash": self.content_hash,
        }


@dataclass
class CompanyMention:
    id: int | None
    company_ticker: str
    company_name: str
    article_id: int
    mentioned_at: datetime
    context: str  # Surrounding text


@dataclass
class Alert:
    id: int | None
    alert_type: str  # 'volume_spike', 'sentiment_shift', 'breaking'
    company_ticker: str
    company_name: str
    severity: str  # 'low', 'medium', 'high'
    message: str
    details: str  # JSON
    created_at: datetime
    acknowledged: bool = False


class Database:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()
        self._run_migrations()
        self._create_indexes()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """
        Context manager for database transactions with automatic rollback on error.

        Usage:
            with db.transaction() as conn:
                conn.execute("INSERT INTO ...")
                conn.execute("INSERT INTO ...")
            # Commits automatically on success, rolls back on exception

        Raises:
            DatabaseTransactionError: If the transaction fails and is rolled back
        """
        conn = self.get_connection()
        try:
            yield conn
            conn.commit()
        except sqlite3.Error as e:
            conn.rollback()
            logger.error("Transaction rolled back due to database error", extra={"error": str(e)})
            raise DatabaseTransactionError(f"Transaction failed: {e}") from e
        except Exception as e:
            conn.rollback()
            logger.error("Transaction rolled back due to error", extra={"error": str(e)})
            raise DatabaseTransactionError(f"Transaction failed: {e}") from e
        finally:
            conn.close()

    def _run_migrations(self):
        """Run database migrations for schema updates"""
        with self.get_connection() as conn:
            # Check if content_hash column exists in articles table
            cursor = conn.execute("PRAGMA table_info(articles)")
            columns = [row["name"] for row in cursor.fetchall()]

            if "content_hash" not in columns:
                logger.info(
                    "Running migration",
                    extra={"migration": "Adding content_hash column to articles table"},
                )
                conn.execute("ALTER TABLE articles ADD COLUMN content_hash TEXT")

                # Create index for content_hash lookups
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_articles_content_hash ON articles(content_hash)"
                )

                # Backfill existing articles with content hashes
                cursor = conn.execute(
                    "SELECT id, title, content FROM articles WHERE content_hash IS NULL"
                )
                rows = cursor.fetchall()
                for row in rows:
                    content_hash = Article.compute_content_hash(
                        row["title"] or "", row["content"] or ""
                    )
                    conn.execute(
                        "UPDATE articles SET content_hash = ? WHERE id = ?",
                        (content_hash, row["id"]),
                    )

                conn.commit()
                logger.info(
                    "Migration complete",
                    extra={"migration": "content_hash", "articles_updated": len(rows)},
                )

    def init_db(self):
        """Initialize database tables"""
        with self.get_connection() as conn:
            # Articles table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS articles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT,
                    source TEXT NOT NULL,
                    published_at TIMESTAMP,
                    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    sentiment_score REAL,
                    mentions TEXT,  -- JSON array of company tickers
                    content_hash TEXT  -- SHA256 hash of title+content for deduplication
                )
            """)

            # Company mentions table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS company_mentions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_ticker TEXT NOT NULL,
                    company_name TEXT NOT NULL,
                    article_id INTEGER NOT NULL,
                    mentioned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    context TEXT,
                    FOREIGN KEY (article_id) REFERENCES articles (id)
                )
            """)

            # Alerts table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alert_type TEXT NOT NULL,
                    company_ticker TEXT NOT NULL,
                    company_name TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details TEXT,  -- JSON
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    acknowledged BOOLEAN DEFAULT FALSE
                )
            """)

            # Company stats table (for caching counts)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS company_stats (
                    company_ticker TEXT PRIMARY KEY,
                    company_name TEXT NOT NULL,
                    mention_count_6h INTEGER DEFAULT 0,
                    mention_count_24h INTEGER DEFAULT 0,
                    mention_count_7d INTEGER DEFAULT 0,
                    last_mention_at TIMESTAMP,
                    avg_sentiment_24h REAL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # User preferences table (for runtime configuration)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_preferences (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Indexes for performance (excluding content_hash which is created after migration)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_mentions_ticker ON company_mentions(company_ticker)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_mentions_time ON company_mentions(mentioned_at)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_time ON alerts(created_at)")

            conn.commit()
            logger.info("Database initialized", extra={"db_path": str(self.db_path)})

    def _create_indexes(self):
        """Create indexes that depend on columns added by migrations"""
        with self.get_connection() as conn:
            # content_hash index is created here after migration ensures column exists
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_articles_content_hash ON articles(content_hash)"
            )
            conn.commit()

    def save_article(self, article: Article) -> int | None:
        """Save an article, return its ID or None if duplicate (by URL or content hash)"""
        try:
            # Sanitize content to prevent XSS attacks
            sanitized_title = sanitize_html(article.title) if article.title else article.title
            sanitized_content = (
                sanitize_html(article.content) if article.content else article.content
            )

            # Compute content hash if not already set (use original content for hash to maintain dedup accuracy)
            content_hash = article.content_hash
            if not content_hash:
                content_hash = Article.compute_content_hash(article.title, article.content or "")

            with self.get_connection() as conn:
                # Check if content hash already exists (same content from different source/URL)
                existing = conn.execute(
                    "SELECT id, url FROM articles WHERE content_hash = ?", (content_hash,)
                ).fetchone()

                if existing:
                    logger.debug(
                        "Skipping duplicate article by content hash",
                        extra={"title": article.title[:50], "existing_url": existing["url"][:50]},
                    )
                    return None

                # Insert the article with sanitized content (URL uniqueness is still enforced by the table constraint)
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO articles
                    (url, title, content, source, published_at, sentiment_score, mentions, content_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        article.url,
                        sanitized_title,
                        sanitized_content,
                        article.source,
                        article.published_at,
                        article.sentiment_score,
                        article.mentions,
                        content_hash,
                    ),
                )
                if cursor.lastrowid:
                    logger.debug(
                        "Saved article",
                        extra={
                            "article_id": cursor.lastrowid,
                            "source": article.source,
                            "title": article.title[:60],
                        },
                    )
                    return cursor.lastrowid
                return None
        except sqlite3.Error as e:
            logger.error("Error saving article", extra={"error": str(e), "url": article.url})
            return None

    def save_company_mention(self, mention: CompanyMention) -> bool:
        """Save a company mention"""
        try:
            # Sanitize context to prevent XSS attacks
            sanitized_context = (
                sanitize_html(mention.context) if mention.context else mention.context
            )

            with self.get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO company_mentions
                    (company_ticker, company_name, article_id, context)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        mention.company_ticker,
                        mention.company_name,
                        mention.article_id,
                        sanitized_context,
                    ),
                )
                return True
        except sqlite3.Error as e:
            logger.error(
                "Error saving mention",
                extra={
                    "error": str(e),
                    "ticker": mention.company_ticker,
                    "article_id": mention.article_id,
                },
            )
            return False

    def save_article_with_mentions(
        self, article: Article, mentions: list[CompanyMention]
    ) -> int | None:
        """
        Save an article and its company mentions in a single transaction.

        If the article is saved but any mention fails to insert, the entire
        transaction is rolled back to maintain data consistency.

        Args:
            article: The article to save
            mentions: List of company mentions for this article

        Returns:
            The article ID if successful, None if duplicate or error

        Raises:
            DatabaseTransactionError: If the transaction fails
        """
        # Compute content hash if not already set
        content_hash = article.content_hash
        if not content_hash:
            content_hash = Article.compute_content_hash(article.title, article.content or "")

        try:
            with self.transaction() as conn:
                # Check if content hash already exists (same content from different source/URL)
                existing = conn.execute(
                    "SELECT id, url FROM articles WHERE content_hash = ?", (content_hash,)
                ).fetchone()

                if existing:
                    logger.debug(
                        "Skipping duplicate article by content hash",
                        extra={"title": article.title[:50], "existing_url": existing["url"][:50]},
                    )
                    return None

                # Insert the article
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO articles
                    (url, title, content, source, published_at, sentiment_score, mentions, content_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        article.url,
                        article.title,
                        article.content,
                        article.source,
                        article.published_at,
                        article.sentiment_score,
                        article.mentions,
                        content_hash,
                    ),
                )

                article_id = cursor.lastrowid
                if not article_id:
                    # Duplicate URL - INSERT OR IGNORE returned 0
                    logger.debug("Skipping duplicate article by URL", extra={"url": article.url})
                    return None

                # Insert all company mentions
                for mention in mentions:
                    conn.execute(
                        """
                        INSERT INTO company_mentions
                        (company_ticker, company_name, article_id, context)
                        VALUES (?, ?, ?, ?)
                        """,
                        (mention.company_ticker, mention.company_name, article_id, mention.context),
                    )

                logger.debug(
                    "Saved article with mentions",
                    extra={
                        "article_id": article_id,
                        "mentions": len(mentions),
                        "title": article.title[:60],
                    },
                )
                return article_id

        except DatabaseTransactionError:
            # Already logged in transaction() context manager
            return None
        except sqlite3.IntegrityError as e:
            logger.warning(
                "Integrity error saving article with mentions",
                extra={"error": str(e), "url": article.url},
            )
            return None
        except sqlite3.Error as e:
            logger.error(
                "Database error saving article with mentions",
                extra={"error": str(e), "url": article.url},
            )
            return None

    def save_alert(self, alert: Alert) -> int | None:
        """Save an alert, avoiding duplicates within 1 hour"""
        try:
            with self.get_connection() as conn:
                # Check for similar recent alert
                one_hour_ago = datetime.now() - timedelta(hours=1)
                existing = conn.execute(
                    """
                    SELECT id FROM alerts 
                    WHERE alert_type = ? 
                    AND company_ticker = ? 
                    AND created_at > ?
                    """,
                    (alert.alert_type, alert.company_ticker, one_hour_ago),
                ).fetchone()

                if existing:
                    logger.debug(
                        "Duplicate alert suppressed",
                        extra={"ticker": alert.company_ticker, "alert_type": alert.alert_type},
                    )
                    return None

                cursor = conn.execute(
                    """
                    INSERT INTO alerts 
                    (alert_type, company_ticker, company_name, severity, message, details)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        alert.alert_type,
                        alert.company_ticker,
                        alert.company_name,
                        alert.severity,
                        alert.message,
                        alert.details,
                    ),
                )
                logger.info(
                    "Created alert",
                    extra={
                        "alert_id": cursor.lastrowid,
                        "alert_type": alert.alert_type,
                        "ticker": alert.company_ticker,
                        "severity": alert.severity,
                    },
                )
                return cursor.lastrowid
        except sqlite3.Error as e:
            logger.error(
                "Error saving alert",
                extra={
                    "error": str(e),
                    "ticker": alert.company_ticker,
                    "alert_type": alert.alert_type,
                },
            )
            return None

    def get_mention_counts(self, hours: int = 24) -> list[dict[str, Any]]:
        """Get mention counts by company for the last N hours"""
        since = datetime.now() - timedelta(hours=hours)

        with self.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT 
                    company_ticker,
                    company_name,
                    COUNT(*) as count,
                    MAX(mentioned_at) as last_mention
                FROM company_mentions
                WHERE mentioned_at > ?
                GROUP BY company_ticker
                ORDER BY count DESC
                """,
                (since,),
            ).fetchall()

            return [dict(row) for row in rows]

    def get_article_count_for_company(self, ticker: str, hours: int = 24) -> int:
        """Get count of articles mentioning a company in time window"""
        since = datetime.now() - timedelta(hours=hours)

        with self.get_connection() as conn:
            row = conn.execute(
                """
                SELECT COUNT(DISTINCT article_id) as count
                FROM company_mentions
                WHERE company_ticker = ?
                AND mentioned_at > ?
                """,
                (ticker, since),
            ).fetchone()

            return row["count"] if row else 0

    def get_recent_articles(self, limit: int = 50, source: str | None = None) -> list[Article]:
        """Get recent articles"""
        with self.get_connection() as conn:
            if source:
                rows = conn.execute(
                    "SELECT * FROM articles WHERE source = ? ORDER BY scraped_at DESC LIMIT ?",
                    (source, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM articles ORDER BY scraped_at DESC LIMIT ?", (limit,)
                ).fetchall()

            return [
                Article(
                    id=row["id"],
                    url=row["url"],
                    title=row["title"],
                    content=row["content"],
                    source=row["source"],
                    published_at=row["published_at"],
                    scraped_at=row["scraped_at"],
                    sentiment_score=row["sentiment_score"],
                    mentions=row["mentions"],
                    content_hash=row["content_hash"] if "content_hash" in row.keys() else None,
                )
                for row in rows
            ]

    def get_unacknowledged_alerts(self, limit: int = 20) -> list[Alert]:
        """Get unacknowledged alerts"""
        with self.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM alerts 
                WHERE acknowledged = FALSE 
                ORDER BY created_at DESC 
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

            return [
                Alert(
                    id=row["id"],
                    alert_type=row["alert_type"],
                    company_ticker=row["company_ticker"],
                    company_name=row["company_name"],
                    severity=row["severity"],
                    message=row["message"],
                    details=row["details"],
                    created_at=row["created_at"],
                    acknowledged=row["acknowledged"],
                )
                for row in rows
            ]

    def cleanup_old_data(self, retention_days: int):
        """Remove old data beyond retention period"""
        cutoff = datetime.now() - timedelta(days=retention_days)

        with self.get_connection() as conn:
            # Delete old articles (will cascade to mentions)
            cursor = conn.execute("DELETE FROM articles WHERE scraped_at < ?", (cutoff,))
            deleted_articles = cursor.rowcount

            # Delete old alerts
            cursor = conn.execute(
                "DELETE FROM alerts WHERE created_at < ? AND acknowledged = TRUE", (cutoff,)
            )
            deleted_alerts = cursor.rowcount

            conn.commit()
            logger.info(
                "Cleaned up old data",
                extra={
                    "deleted_articles": deleted_articles,
                    "deleted_alerts": deleted_alerts,
                    "retention_days": retention_days,
                },
            )

    def get_stats(self) -> dict[str, Any]:
        """Get database statistics"""
        with self.get_connection() as conn:
            total_articles = conn.execute("SELECT COUNT(*) as count FROM articles").fetchone()[
                "count"
            ]

            total_mentions = conn.execute(
                "SELECT COUNT(*) as count FROM company_mentions"
            ).fetchone()["count"]

            total_alerts = conn.execute("SELECT COUNT(*) as count FROM alerts").fetchone()["count"]

            articles_24h = conn.execute(
                "SELECT COUNT(*) as count FROM articles WHERE scraped_at > datetime('now', '-1 day')"
            ).fetchone()["count"]

            return {
                "total_articles": total_articles,
                "total_mentions": total_mentions,
                "total_alerts": total_alerts,
                "articles_24h": articles_24h,
            }

    def save_preference(self, key: str, value: Any) -> bool:
        """
        Save a user preference (JSON encoded).

        Args:
            key: The preference key
            value: The preference value (will be JSON encoded)

        Returns:
            True if successful, False otherwise
        """
        try:
            json_value = json.dumps(value)
            with self.get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO user_preferences (key, value, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (key, json_value),
                )
                conn.commit()
                logger.debug("Saved preference", extra={"key": key})
                return True
        except (sqlite3.Error, json.JSONDecodeError) as e:
            logger.error("Error saving preference", extra={"key": key, "error": str(e)})
            return False

    def get_preference(self, key: str, default: Any = None) -> Any:
        """
        Get a user preference (JSON decoded).

        Args:
            key: The preference key
            default: Default value if key doesn't exist

        Returns:
            The preference value or default
        """
        try:
            with self.get_connection() as conn:
                row = conn.execute(
                    "SELECT value FROM user_preferences WHERE key = ?", (key,)
                ).fetchone()

                if row and row["value"]:
                    return json.loads(row["value"])
                return default
        except (sqlite3.Error, json.JSONDecodeError) as e:
            logger.error("Error getting preference", extra={"key": key, "error": str(e)})
            return default

    def get_all_preferences(self) -> dict[str, Any]:
        """
        Get all user preferences.

        Returns:
            Dictionary of all preferences (key -> value)
        """
        try:
            with self.get_connection() as conn:
                rows = conn.execute("SELECT key, value FROM user_preferences").fetchall()

                result = {}
                for row in rows:
                    try:
                        result[row["key"]] = json.loads(row["value"]) if row["value"] else None
                    except json.JSONDecodeError:
                        result[row["key"]] = row["value"]
                return result
        except sqlite3.Error as e:
            logger.error("Error getting all preferences", extra={"error": str(e)})
            return {}

    def delete_preference(self, key: str) -> bool:
        """
        Delete a user preference.

        Args:
            key: The preference key to delete

        Returns:
            True if successful, False otherwise
        """
        try:
            with self.get_connection() as conn:
                conn.execute("DELETE FROM user_preferences WHERE key = ?", (key,))
                conn.commit()
                logger.debug("Deleted preference", extra={"key": key})
                return True
        except sqlite3.Error as e:
            logger.error("Error deleting preference", extra={"key": key, "error": str(e)})
            return False
