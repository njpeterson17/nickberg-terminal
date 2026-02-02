"""
Backtesting system for News Sentinel Bot.

Allows replaying pattern detection against historical data to:
- Evaluate pattern detection performance
- Tune thresholds based on historical outcomes
- Generate reports on alert frequency and distribution
"""

import csv
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from database import Database
from pattern_detector import PatternDetector, PatternAlert
from company_extractor import SentimentAnalyzer
from logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class BacktestAlert:
    """Represents an alert generated during backtesting."""

    timestamp: datetime
    pattern_type: str
    ticker: str
    company_name: str
    severity: str
    message: str
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "pattern_type": self.pattern_type,
            "ticker": self.ticker,
            "company_name": self.company_name,
            "severity": self.severity,
            "message": self.message,
            "details": self.details,
        }


@dataclass
class BacktestReport:
    """Report generated from a backtest run."""

    start_date: datetime
    end_date: datetime
    total_alerts: int = 0
    alerts_by_type: dict[str, int] = field(default_factory=dict)
    alerts_by_company: dict[str, int] = field(default_factory=dict)
    alerts_by_severity: dict[str, int] = field(default_factory=dict)
    alerts_by_day: dict[str, int] = field(default_factory=dict)
    alerts: list[BacktestAlert] = field(default_factory=list)
    false_positive_analysis: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "period": {
                "start": self.start_date.isoformat(),
                "end": self.end_date.isoformat(),
            },
            "summary": {
                "total_alerts": self.total_alerts,
                "by_type": self.alerts_by_type,
                "by_company": self.alerts_by_company,
                "by_severity": self.alerts_by_severity,
                "by_day": self.alerts_by_day,
            },
            "false_positive_analysis": self.false_positive_analysis,
            "alerts": [alert.to_dict() for alert in self.alerts],
        }


class HistoricalPatternDetector:
    """
    Pattern detector that operates on a specific point in time.

    This is a modified version of PatternDetector that uses historical data
    up to a specific timestamp, simulating what the detector would see
    if it were running at that point in time.
    """

    def __init__(self, db: Database, config: dict[str, Any], as_of_time: datetime):
        self.db = db
        self.config = config
        self.as_of_time = as_of_time
        self.windows = config.get("windows", {"short": 6, "medium": 24, "long": 168})

        # Thresholds
        self.volume_spike_threshold = config.get("volume_spike_threshold", 3.0)
        self.min_articles_for_alert = config.get("min_articles_for_alert", 3)
        self.sentiment_threshold = 0.5

        # Sentiment analyzer
        sentiment_config = config.get("sentiment_keywords", {})
        self.sentiment_analyzer = SentimentAnalyzer(
            positive_words=sentiment_config.get("positive", []),
            negative_words=sentiment_config.get("negative", []),
        )

    def _get_mention_counts_as_of(self, hours: int) -> list[dict[str, Any]]:
        """Get mention counts as of the specified time."""
        since = self.as_of_time - timedelta(hours=hours)

        with self.db.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    company_ticker,
                    company_name,
                    COUNT(*) as count,
                    MAX(mentioned_at) as last_mention
                FROM company_mentions
                WHERE mentioned_at > ? AND mentioned_at <= ?
                GROUP BY company_ticker
                ORDER BY count DESC
                """,
                (since, self.as_of_time),
            ).fetchall()

            return [dict(row) for row in rows]

    def _get_article_count_for_company_as_of(self, ticker: str, hours: int) -> int:
        """Get article count for a company as of the specified time."""
        since = self.as_of_time - timedelta(hours=hours)

        with self.db.get_connection() as conn:
            row = conn.execute(
                """
                SELECT COUNT(DISTINCT article_id) as count
                FROM company_mentions
                WHERE company_ticker = ?
                AND mentioned_at > ? AND mentioned_at <= ?
                """,
                (ticker, since, self.as_of_time),
            ).fetchone()

            return row["count"] if row else 0

    def _get_company_articles_as_of(
        self, ticker: str, hours: int, exclude_hours: int = 0
    ) -> list[dict]:
        """Get articles mentioning a company as of the specified time."""
        with self.db.get_connection() as conn:
            if exclude_hours > 0:
                start_time = self.as_of_time - timedelta(hours=exclude_hours)
                end_time = self.as_of_time - timedelta(hours=hours)
                rows = conn.execute(
                    """
                    SELECT a.* FROM articles a
                    JOIN company_mentions cm ON a.id = cm.article_id
                    WHERE cm.company_ticker = ?
                    AND a.published_at BETWEEN ? AND ?
                    ORDER BY a.published_at DESC
                    """,
                    (ticker, end_time, start_time),
                ).fetchall()
            else:
                since = self.as_of_time - timedelta(hours=hours)
                rows = conn.execute(
                    """
                    SELECT a.* FROM articles a
                    JOIN company_mentions cm ON a.id = cm.article_id
                    WHERE cm.company_ticker = ?
                    AND a.published_at > ? AND a.published_at <= ?
                    ORDER BY a.published_at DESC
                    """,
                    (ticker, since, self.as_of_time),
                ).fetchall()

            return [dict(row) for row in rows]

    def detect_all_patterns(self) -> list[PatternAlert]:
        """Run all pattern detection algorithms as of the specified time."""
        alerts = []

        company_counts = self._get_mention_counts_as_of(hours=self.windows["long"])

        for company in company_counts:
            ticker = company["company_ticker"]
            company_name = company["company_name"]

            if company["count"] < self.min_articles_for_alert:
                continue

            volume_alert = self._detect_volume_spike(ticker, company_name)
            if volume_alert:
                alerts.append(volume_alert)

            sentiment_alert = self._detect_sentiment_shift(ticker, company_name)
            if sentiment_alert:
                alerts.append(sentiment_alert)

            momentum_alert = self._detect_momentum_building(ticker, company_name)
            if momentum_alert:
                alerts.append(momentum_alert)

            negative_alert = self._detect_negative_cluster(ticker, company_name)
            if negative_alert:
                alerts.append(negative_alert)

        return alerts

    def _detect_volume_spike(self, ticker: str, company_name: str) -> PatternAlert | None:
        """Detect unusual volume of articles."""
        count_6h = self._get_article_count_for_company_as_of(ticker, hours=self.windows["short"])
        count_24h = self._get_article_count_for_company_as_of(ticker, hours=self.windows["medium"])
        count_7d = self._get_article_count_for_company_as_of(ticker, hours=self.windows["long"])

        avg_daily = count_7d / 7.0

        if count_6h >= self.min_articles_for_alert and avg_daily > 0:
            expected_6h = avg_daily * (6 / 24)

            if expected_6h > 0:
                spike_ratio = count_6h / expected_6h

                if spike_ratio >= self.volume_spike_threshold:
                    severity = "high" if spike_ratio >= 5 else "medium"

                    return PatternAlert(
                        pattern_type="volume_spike",
                        ticker=ticker,
                        company_name=company_name,
                        severity=severity,
                        message=f"{company_name} ({ticker}): {count_6h} articles in 6h "
                        f"(spike: {spike_ratio:.1f}x normal)",
                        details={
                            "articles_6h": count_6h,
                            "articles_24h": count_24h,
                            "articles_7d": count_7d,
                            "spike_ratio": round(spike_ratio, 2),
                            "expected_6h": round(expected_6h, 1),
                        },
                    )

        if count_24h >= 5 and count_7d > 0 and count_24h >= count_7d * 0.5:
            return PatternAlert(
                pattern_type="volume_spike",
                ticker=ticker,
                company_name=company_name,
                severity="medium",
                message=f"{company_name} ({ticker}): Burst of {count_24h} articles in 24h "
                f"({count_24h / count_7d * 100:.0f}% of week's coverage)",
                details={
                    "articles_24h": count_24h,
                    "articles_7d": count_7d,
                    "percentage": round(count_24h / count_7d * 100, 1),
                },
            )

        return None

    def _detect_sentiment_shift(self, ticker: str, company_name: str) -> PatternAlert | None:
        """Detect significant sentiment changes."""
        recent_articles = self._get_company_articles_as_of(ticker, hours=self.windows["medium"])

        if len(recent_articles) < 3:
            return None

        sentiments = [
            self.sentiment_analyzer.analyze(a.get("content", "") or "") for a in recent_articles
        ]
        avg_sentiment = sum(sentiments) / len(sentiments)

        older_articles = self._get_company_articles_as_of(
            ticker, hours=48, exclude_hours=self.windows["medium"]
        )

        if older_articles:
            older_sentiments = [
                self.sentiment_analyzer.analyze(a.get("content", "") or "") for a in older_articles
            ]
            baseline = sum(older_sentiments) / len(older_sentiments)

            shift = avg_sentiment - baseline

            if abs(shift) >= self.sentiment_threshold:
                direction = "positive" if shift > 0 else "negative"
                severity = "high" if abs(shift) >= 0.7 else "medium"

                return PatternAlert(
                    pattern_type="sentiment_shift",
                    ticker=ticker,
                    company_name=company_name,
                    severity=severity,
                    message=f"{company_name} ({ticker}): {direction.title()} sentiment shift "
                    f"(change: {shift:+.2f})",
                    details={
                        "direction": direction,
                        "shift": round(shift, 3),
                        "current_sentiment": round(avg_sentiment, 3),
                        "baseline_sentiment": round(baseline, 3),
                        "article_count": len(recent_articles),
                    },
                )

        if avg_sentiment >= 0.6:
            return PatternAlert(
                pattern_type="sentiment_shift",
                ticker=ticker,
                company_name=company_name,
                severity="medium",
                message=f"{company_name} ({ticker}): Unusually positive coverage "
                f"(score: {avg_sentiment:+.2f})",
                details={
                    "direction": "positive",
                    "current_sentiment": round(avg_sentiment, 3),
                    "article_count": len(recent_articles),
                },
            )
        elif avg_sentiment <= -0.6:
            return PatternAlert(
                pattern_type="sentiment_shift",
                ticker=ticker,
                company_name=company_name,
                severity="high",
                message=f"{company_name} ({ticker}): Unusually negative coverage "
                f"(score: {avg_sentiment:+.2f})",
                details={
                    "direction": "negative",
                    "current_sentiment": round(avg_sentiment, 3),
                    "article_count": len(recent_articles),
                },
            )

        return None

    def _detect_momentum_building(self, ticker: str, company_name: str) -> PatternAlert | None:
        """Detect increasing coverage momentum."""
        counts = []
        for i in range(7):
            hours_ago = (i + 1) * 24
            count = self._get_article_count_for_company_as_of(ticker, hours=hours_ago)
            if i > 0:
                daily = count - counts[-1]["cumulative"]
            else:
                daily = count
            counts.append({"day": i, "daily": daily, "cumulative": count})

        daily_counts = [c["daily"] for c in counts]

        if len(daily_counts) >= 3:
            recent = daily_counts[:3]
            if recent[0] > recent[1] > recent[2] and recent[0] >= 2:
                return PatternAlert(
                    pattern_type="momentum",
                    ticker=ticker,
                    company_name=company_name,
                    severity="medium",
                    message=f"{company_name} ({ticker}): Building momentum "
                    f"({recent[2]} -> {recent[1]} -> {recent[0]} articles/day)",
                    details={
                        "daily_trend": recent[::-1],
                        "total_7d": counts[0]["cumulative"],
                    },
                )

        return None

    def _detect_negative_cluster(self, ticker: str, company_name: str) -> PatternAlert | None:
        """Detect cluster of negative articles."""
        recent_articles = self._get_company_articles_as_of(ticker, hours=self.windows["short"])

        if len(recent_articles) < 2:
            return None

        negative_count = 0
        negative_keywords = []

        for article in recent_articles:
            content = (article.get("content") or "").lower()
            sentiment = self.sentiment_analyzer.analyze(content)

            if sentiment < -0.3:
                negative_count += 1
                for word in [
                    "investigation",
                    "lawsuit",
                    "layoffs",
                    "bankruptcy",
                    "crash",
                    "plunge",
                    "scandal",
                    "fraud",
                ]:
                    if word in content and word not in negative_keywords:
                        negative_keywords.append(word)

        if negative_count >= 2 and negative_count / len(recent_articles) >= 0.5:
            return PatternAlert(
                pattern_type="negative_cluster",
                ticker=ticker,
                company_name=company_name,
                severity="high",
                message=f"{company_name} ({ticker}): Negative news cluster "
                f"({negative_count}/{len(recent_articles)} recent articles negative)",
                details={
                    "negative_articles": negative_count,
                    "total_articles": len(recent_articles),
                    "keywords": negative_keywords[:5],
                },
            )

        return None


class Backtester:
    """
    Backtesting system for pattern detection.

    Replays historical articles chronologically and runs pattern detection
    at each point in time to see what alerts would have been generated.
    """

    def __init__(self, db: Database, config: dict[str, Any]):
        self.db = db
        self.config = config
        self.pattern_config = config.get("patterns", {})
        self.report: BacktestReport | None = None

    def run(
        self,
        start_date: datetime,
        end_date: datetime,
        interval_hours: int = 6,
    ) -> BacktestReport:
        """
        Run backtest over the specified date range.

        Args:
            start_date: Start of the backtest period
            end_date: End of the backtest period
            interval_hours: How often to run pattern detection (default: every 6 hours)

        Returns:
            BacktestReport containing all results
        """
        logger.info(
            "Starting backtest",
            extra={
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "interval_hours": interval_hours,
            },
        )

        self.report = BacktestReport(start_date=start_date, end_date=end_date)

        # Track alerts to avoid duplicates (same type+ticker within cooldown period)
        alert_cooldowns: dict[str, datetime] = {}
        cooldown_hours = 1

        # Step through the time range
        current_time = start_date
        checkpoints = 0

        while current_time <= end_date:
            checkpoints += 1

            # Create a pattern detector that sees data as of this time
            detector = HistoricalPatternDetector(
                self.db, self.pattern_config, as_of_time=current_time
            )

            # Run pattern detection
            pattern_alerts = detector.detect_all_patterns()

            # Process alerts
            for alert in pattern_alerts:
                # Create cooldown key
                cooldown_key = f"{alert.pattern_type}:{alert.ticker}"

                # Check if we're still in cooldown period
                if cooldown_key in alert_cooldowns:
                    last_alert_time = alert_cooldowns[cooldown_key]
                    if current_time - last_alert_time < timedelta(hours=cooldown_hours):
                        continue

                # Record the alert
                backtest_alert = BacktestAlert(
                    timestamp=current_time,
                    pattern_type=alert.pattern_type,
                    ticker=alert.ticker,
                    company_name=alert.company_name,
                    severity=alert.severity,
                    message=alert.message,
                    details=alert.details,
                )

                self.report.alerts.append(backtest_alert)
                self.report.total_alerts += 1

                # Update counters
                self.report.alerts_by_type[alert.pattern_type] = (
                    self.report.alerts_by_type.get(alert.pattern_type, 0) + 1
                )
                self.report.alerts_by_company[alert.ticker] = (
                    self.report.alerts_by_company.get(alert.ticker, 0) + 1
                )
                self.report.alerts_by_severity[alert.severity] = (
                    self.report.alerts_by_severity.get(alert.severity, 0) + 1
                )

                day_key = current_time.strftime("%Y-%m-%d")
                self.report.alerts_by_day[day_key] = self.report.alerts_by_day.get(day_key, 0) + 1

                # Update cooldown
                alert_cooldowns[cooldown_key] = current_time

            # Move to next checkpoint
            current_time += timedelta(hours=interval_hours)

        # Generate false positive analysis placeholder
        self._analyze_false_positives()

        logger.info(
            "Backtest complete",
            extra={
                "checkpoints": checkpoints,
                "total_alerts": self.report.total_alerts,
                "unique_companies": len(self.report.alerts_by_company),
            },
        )

        return self.report

    def _analyze_false_positives(self):
        """
        Analyze potential false positives.

        This is a placeholder for future integration with price data.
        Currently provides basic statistics on alert patterns.
        """
        if not self.report:
            return

        self.report.false_positive_analysis = {
            "note": "Price movement validation not yet implemented",
            "analysis_available": False,
            "statistics": {
                "avg_alerts_per_day": (
                    self.report.total_alerts / max(len(self.report.alerts_by_day), 1)
                ),
                "most_alerted_company": (
                    max(self.report.alerts_by_company.items(), key=lambda x: x[1])[0]
                    if self.report.alerts_by_company
                    else None
                ),
                "most_common_pattern": (
                    max(self.report.alerts_by_type.items(), key=lambda x: x[1])[0]
                    if self.report.alerts_by_type
                    else None
                ),
            },
        }

    def get_alerts_for_period(self, start: datetime, end: datetime) -> list[BacktestAlert]:
        """
        Get alerts that would have fired within a specific period.

        Args:
            start: Start of the period
            end: End of the period

        Returns:
            List of BacktestAlert objects within the period
        """
        if not self.report:
            return []

        return [alert for alert in self.report.alerts if start <= alert.timestamp <= end]

    def generate_report(self) -> dict[str, Any]:
        """
        Generate a summary report of the backtest.

        Returns:
            Dictionary containing the full report
        """
        if not self.report:
            return {"error": "No backtest has been run yet"}

        return self.report.to_dict()

    def export_results(self, filepath: str, format: str = "json") -> bool:
        """
        Export backtest results to a file.

        Args:
            filepath: Path to the output file
            format: Export format ('json' or 'csv')

        Returns:
            True if export was successful, False otherwise
        """
        if not self.report:
            logger.error("No backtest results to export")
            return False

        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)

        try:
            if format.lower() == "json":
                with open(filepath, "w") as f:
                    json.dump(self.report.to_dict(), f, indent=2)
            elif format.lower() == "csv":
                with open(filepath, "w", newline="") as f:
                    writer = csv.writer(f)
                    # Write header
                    writer.writerow(
                        [
                            "timestamp",
                            "pattern_type",
                            "ticker",
                            "company_name",
                            "severity",
                            "message",
                        ]
                    )
                    # Write alerts
                    for alert in self.report.alerts:
                        writer.writerow(
                            [
                                alert.timestamp.isoformat(),
                                alert.pattern_type,
                                alert.ticker,
                                alert.company_name,
                                alert.severity,
                                alert.message,
                            ]
                        )
            else:
                logger.error(f"Unsupported export format: {format}")
                return False

            logger.info(
                "Exported backtest results",
                extra={"filepath": str(filepath), "format": format},
            )
            return True

        except OSError as e:
            logger.error(
                "Failed to export backtest results",
                extra={"filepath": str(filepath), "error": str(e)},
            )
            return False

    def print_summary(self):
        """Print a human-readable summary of the backtest results."""
        if not self.report:
            print("No backtest results available.")
            return

        print("\n" + "=" * 60)
        print("BACKTEST REPORT")
        print("=" * 60)
        print(
            f"Period: {self.report.start_date.strftime('%Y-%m-%d')} to "
            f"{self.report.end_date.strftime('%Y-%m-%d')}"
        )
        print(f"Total Alerts: {self.report.total_alerts}")
        print()

        if self.report.alerts_by_type:
            print("Alerts by Type:")
            for alert_type, count in sorted(
                self.report.alerts_by_type.items(), key=lambda x: -x[1]
            ):
                print(f"  {alert_type}: {count}")
            print()

        if self.report.alerts_by_company:
            print("Top Companies by Alert Count:")
            sorted_companies = sorted(self.report.alerts_by_company.items(), key=lambda x: -x[1])[
                :10
            ]
            for ticker, count in sorted_companies:
                print(f"  {ticker}: {count}")
            print()

        if self.report.alerts_by_severity:
            print("Alerts by Severity:")
            for severity, count in sorted(
                self.report.alerts_by_severity.items(),
                key=lambda x: {"high": 0, "medium": 1, "low": 2}.get(x[0], 3),
            ):
                print(f"  {severity}: {count}")
            print()

        if self.report.alerts_by_day:
            print("Alert Frequency by Day:")
            for day, count in sorted(self.report.alerts_by_day.items())[-7:]:
                print(f"  {day}: {count}")

        print("=" * 60 + "\n")
