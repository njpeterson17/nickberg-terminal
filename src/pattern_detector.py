"""
Pattern detection engine for identifying volume spikes, sentiment shifts, etc.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from collections import defaultdict

from database import Database
from company_extractor import SentimentAnalyzer

logger = logging.getLogger(__name__)


@dataclass
class PatternAlert:
    pattern_type: str  # 'volume_spike', 'sentiment_shift', 'negative_cluster', 'momentum'
    ticker: str
    company_name: str
    severity: str  # 'low', 'medium', 'high'
    message: str
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_type": self.pattern_type,
            "ticker": self.ticker,
            "company_name": self.company_name,
            "severity": self.severity,
            "message": self.message,
            "details": self.details,
            "timestamp": datetime.now().isoformat(),
        }


class PatternDetector:
    """Detect patterns in news coverage"""

    def __init__(self, db: Database, config: dict[str, Any]):
        self.db = db
        self.config = config
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

    def detect_all_patterns(self) -> list[PatternAlert]:
        """Run all pattern detection algorithms"""
        alerts = []

        # Get company mention stats
        company_counts = self.db.get_mention_counts(hours=self.windows["long"])

        for company in company_counts:
            ticker = company["company_ticker"]
            company_name = company["company_name"]

            # Skip if not enough mentions
            if company["count"] < self.min_articles_for_alert:
                continue

            # Run detection algorithms
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

        logger.info(f"Pattern detection found {len(alerts)} alerts")
        return alerts

    def _detect_volume_spike(self, ticker: str, company_name: str) -> PatternAlert | None:
        """Detect unusual volume of articles (spike in coverage)"""
        # Get counts for different time windows
        count_6h = self.db.get_article_count_for_company(ticker, hours=self.windows["short"])
        count_24h = self.db.get_article_count_for_company(ticker, hours=self.windows["medium"])
        count_7d = self.db.get_article_count_for_company(ticker, hours=self.windows["long"])

        # Calculate 7-day average per day
        avg_daily = count_7d / 7.0

        # Check for spike in last 6 hours
        if count_6h >= self.min_articles_for_alert and avg_daily > 0:
            # Expected articles in 6 hours based on average
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

        # Check for concentrated burst (many articles in short window)
        if count_24h >= 5 and count_24h >= count_7d * 0.5:
            # More than 50% of week's articles in last 24 hours
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
        """Detect significant sentiment changes"""
        # Get recent articles mentioning this company
        recent_articles = self._get_company_articles(ticker, hours=self.windows["medium"])

        if len(recent_articles) < 3:
            return None

        # Analyze sentiment of each article
        sentiments = [self.sentiment_analyzer.analyze(a["content"]) for a in recent_articles]
        avg_sentiment = sum(sentiments) / len(sentiments)

        # Get older baseline (24-48h ago)
        older_articles = self._get_company_articles(
            ticker, hours=48, exclude_hours=self.windows["medium"]
        )

        if older_articles:
            older_sentiments = [
                self.sentiment_analyzer.analyze(a["content"]) for a in older_articles
            ]
            baseline = sum(older_sentiments) / len(older_sentiments)

            # Detect significant shift
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

        # Check for extremely positive/negative coverage even without baseline
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
        """Detect increasing coverage momentum"""
        # Get counts for successive time windows
        counts = []
        for i in range(7):  # Last 7 days
            hours_ago = (i + 1) * 24
            count = self.db.get_article_count_for_company(ticker, hours=hours_ago)
            # Subtract previous day's count to get daily
            if i > 0:
                daily = count - counts[-1]["cumulative"]
            else:
                daily = count
            counts.append({"day": i, "daily": daily, "cumulative": count})

        # Check for increasing trend
        daily_counts = [c["daily"] for c in counts]

        if len(daily_counts) >= 3:
            # Check if last 3 days show increasing pattern
            recent = daily_counts[:3]
            if recent[0] > recent[1] > recent[2] and recent[0] >= 2:
                return PatternAlert(
                    pattern_type="momentum",
                    ticker=ticker,
                    company_name=company_name,
                    severity="medium",
                    message=f"{company_name} ({ticker}): Building momentum "
                    f"({recent[2]} → {recent[1]} → {recent[0]} articles/day)",
                    details={
                        "daily_trend": recent[::-1],  # Oldest to newest
                        "total_7d": counts[0]["cumulative"],
                    },
                )

        return None

    def _detect_negative_cluster(self, ticker: str, company_name: str) -> PatternAlert | None:
        """Detect cluster of negative articles"""
        recent_articles = self._get_company_articles(ticker, hours=self.windows["short"])

        if len(recent_articles) < 2:
            return None

        # Analyze sentiment
        negative_count = 0
        negative_keywords = []

        for article in recent_articles:
            content = article.get("content", "").lower()
            sentiment = self.sentiment_analyzer.analyze(content)

            if sentiment < -0.3:
                negative_count += 1
                # Extract negative keywords
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

        # If majority of recent articles are negative
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

    def _get_company_articles(self, ticker: str, hours: int, exclude_hours: int = 0) -> list[dict]:
        """Get articles mentioning a company in time window"""
        from datetime import datetime

        with self.db.get_connection() as conn:
            if exclude_hours > 0:
                # Get articles between (now - exclude_hours) and (now - hours)
                start_time = datetime.now() - timedelta(hours=exclude_hours)
                end_time = datetime.now() - timedelta(hours=hours)
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
                # Get articles from last N hours
                since = datetime.now() - timedelta(hours=hours)
                rows = conn.execute(
                    """
                    SELECT a.* FROM articles a
                    JOIN company_mentions cm ON a.id = cm.article_id
                    WHERE cm.company_ticker = ?
                    AND a.published_at > ?
                    ORDER BY a.published_at DESC
                    """,
                    (ticker, since),
                ).fetchall()

            return [dict(row) for row in rows]
