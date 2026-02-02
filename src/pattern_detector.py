"""
Pattern detection engine for identifying volume spikes, sentiment shifts, etc.

Combines rule-based detection with optional ML-based anomaly detection
for enhanced pattern recognition.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from collections import defaultdict

from database import Database
from company_extractor import SentimentAnalyzer

logger = logging.getLogger(__name__)

# Import ML detector with fallback
try:
    from ml_detector import MLPatternDetector, SKLEARN_AVAILABLE
except ImportError:
    MLPatternDetector = None
    SKLEARN_AVAILABLE = False
    logger.debug("ML detector not available")

# Import market data provider with fallback
try:
    from market_data import MarketDataProvider, YFINANCE_AVAILABLE
except ImportError:
    MarketDataProvider = None
    YFINANCE_AVAILABLE = False
    logger.debug("Market data provider not available")


@dataclass
class PatternAlert:
    pattern_type: str  # 'volume_spike', 'sentiment_shift', 'negative_cluster', 'momentum'
    ticker: str
    company_name: str
    severity: str  # 'low', 'medium', 'high'
    message: str
    details: dict[str, Any]
    ml_score: Optional[float] = None  # ML confidence score when ML detection is used
    market_context: Optional[Dict[str, Any]] = None  # Stock price context when available

    def to_dict(self) -> dict[str, Any]:
        result = {
            "pattern_type": self.pattern_type,
            "ticker": self.ticker,
            "company_name": self.company_name,
            "severity": self.severity,
            "message": self.message,
            "details": self.details,
            "timestamp": datetime.now().isoformat(),
        }
        if self.ml_score is not None:
            result["ml_score"] = self.ml_score
        if self.market_context is not None:
            result["market_context"] = self.market_context
        return result


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

        # ML detection (optional)
        self.ml_detector: Optional[Any] = None
        self.ml_enabled = False
        ml_config = config.get("ml_detection", {})
        if ml_config.get("enabled", False) and MLPatternDetector is not None:
            try:
                self.ml_detector = MLPatternDetector(config)
                self.ml_enabled = self.ml_detector.enabled
                # Try to load existing model
                if self.ml_enabled:
                    self.ml_detector.load_model()
                logger.info(
                    "ML pattern detection initialized",
                    extra={"enabled": self.ml_enabled, "trained": self.ml_detector.is_trained},
                )
            except Exception as e:
                logger.warning(f"Failed to initialize ML detector: {e}")
                self.ml_detector = None
                self.ml_enabled = False

        # Market data provider (optional)
        self.market_data: Optional[Any] = None
        self.market_data_enabled = False
        market_config = config.get("market_data", {})
        if market_config.get("enabled", False) and MarketDataProvider is not None:
            try:
                self.market_data = MarketDataProvider(market_config)
                self.market_data_enabled = self.market_data.enabled
                self.include_market_in_alerts = market_config.get("include_in_alerts", True)
                logger.info(
                    "Market data provider initialized",
                    extra={"enabled": self.market_data_enabled},
                )
            except Exception as e:
                logger.warning(f"Failed to initialize market data provider: {e}")
                self.market_data = None
                self.market_data_enabled = False

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

            # Get ML score for this company if ML detection is enabled
            ml_result = None
            if self.ml_enabled and self.ml_detector and self.ml_detector.is_trained:
                company_data = self._prepare_company_data_for_ml(ticker)
                ml_result = self.ml_detector.get_ml_score(company_data)

            # Get market context if enabled
            market_context = None
            if (
                self.market_data_enabled
                and self.market_data
                and getattr(self, "include_market_in_alerts", True)
            ):
                market_context = self._get_market_context(ticker)

            # Run detection algorithms
            volume_alert = self._detect_volume_spike(ticker, company_name)
            if volume_alert:
                volume_alert = self._apply_ml_score(volume_alert, ml_result)
                volume_alert = self._apply_market_context(volume_alert, market_context)
                alerts.append(volume_alert)

            sentiment_alert = self._detect_sentiment_shift(ticker, company_name)
            if sentiment_alert:
                sentiment_alert = self._apply_ml_score(sentiment_alert, ml_result)
                sentiment_alert = self._apply_market_context(sentiment_alert, market_context)
                alerts.append(sentiment_alert)

            momentum_alert = self._detect_momentum_building(ticker, company_name)
            if momentum_alert:
                momentum_alert = self._apply_ml_score(momentum_alert, ml_result)
                momentum_alert = self._apply_market_context(momentum_alert, market_context)
                alerts.append(momentum_alert)

            negative_alert = self._detect_negative_cluster(ticker, company_name)
            if negative_alert:
                negative_alert = self._apply_ml_score(negative_alert, ml_result)
                negative_alert = self._apply_market_context(negative_alert, market_context)
                alerts.append(negative_alert)

            # Check for ML-only anomalies (patterns rule-based might miss)
            if ml_result and not any(
                a.ticker == ticker
                for a in [volume_alert, sentiment_alert, momentum_alert, negative_alert]
                if a
            ):
                ml_alert = self._check_ml_only_anomaly(ticker, company_name, ml_result)
                if ml_alert:
                    ml_alert = self._apply_market_context(ml_alert, market_context)
                    alerts.append(ml_alert)

        logger.info(f"Pattern detection found {len(alerts)} alerts")
        return alerts

    def _prepare_company_data_for_ml(self, ticker: str) -> dict[str, Any]:
        """Prepare company data dictionary for ML feature extraction."""
        count_1h = self.db.get_article_count_for_company(ticker, hours=1)
        count_6h = self.db.get_article_count_for_company(ticker, hours=6)
        count_24h = self.db.get_article_count_for_company(ticker, hours=24)
        count_7d = self.db.get_article_count_for_company(ticker, hours=168)

        # Get sentiment stats from recent articles
        recent_articles = self._get_company_articles(ticker, hours=24)
        sentiments = [
            self.sentiment_analyzer.analyze(a.get("content", "")) for a in recent_articles
        ]

        sentiment_mean = sum(sentiments) / len(sentiments) if sentiments else 0
        sentiment_std = (
            (sum((s - sentiment_mean) ** 2 for s in sentiments) / len(sentiments)) ** 0.5
            if len(sentiments) > 1
            else 0
        )

        # Get older sentiment for comparison
        older_articles = self._get_company_articles(ticker, hours=168, exclude_hours=24)
        older_sentiments = [
            self.sentiment_analyzer.analyze(a.get("content", "")) for a in older_articles
        ]
        sentiment_mean_24h = (
            sum(older_sentiments) / len(older_sentiments) if older_sentiments else sentiment_mean
        )
        sentiment_std_24h = (
            (sum((s - sentiment_mean_24h) ** 2 for s in older_sentiments) / len(older_sentiments))
            ** 0.5
            if len(older_sentiments) > 1
            else sentiment_std
        )

        return {
            "ticker": ticker,
            "count_1h": count_1h,
            "count_6h": count_6h,
            "count_24h": count_24h,
            "count_7d": count_7d,
            "sentiment_mean": sentiment_mean,
            "sentiment_std": sentiment_std,
            "sentiment_mean_24h": sentiment_mean_24h,
            "sentiment_std_24h": sentiment_std_24h,
        }

    def _apply_ml_score(
        self, alert: PatternAlert, ml_result: Optional[dict[str, Any]]
    ) -> PatternAlert:
        """Apply ML score to an alert, potentially adjusting confidence."""
        if not ml_result or "ml_score" not in ml_result:
            return alert

        ml_score = ml_result["ml_score"]
        alert.ml_score = ml_score

        # Add ML details to alert
        alert.details["ml_score"] = round(ml_score, 3)
        alert.details["ml_anomaly"] = ml_result.get("anomaly_result", {}).get("is_anomaly", False)

        # Boost severity if ML strongly agrees
        if ml_score > 0.8 and alert.severity == "medium":
            alert.severity = "high"
            alert.message += " [ML: High confidence]"
        # Reduce severity if ML disagrees
        elif ml_score < 0.3 and alert.severity == "high":
            alert.severity = "medium"
            alert.message += " [ML: Lower confidence]"

        return alert

    def _check_ml_only_anomaly(
        self, ticker: str, company_name: str, ml_result: dict[str, Any]
    ) -> Optional[PatternAlert]:
        """Check if ML detected an anomaly that rule-based detection missed."""
        if not ml_result:
            return None

        anomaly_result = ml_result.get("anomaly_result", {})
        pattern_result = ml_result.get("pattern_result", {})
        ml_score = ml_result.get("ml_score", 0)

        # Only create alert if both anomaly detection and pattern prediction agree
        # and the scores are high enough
        if (
            anomaly_result.get("is_anomaly", False)
            and pattern_result.get("is_significant", False)
            and ml_score > 0.7
        ):
            return PatternAlert(
                pattern_type="ml_anomaly",
                ticker=ticker,
                company_name=company_name,
                severity="medium",
                message=f"{company_name} ({ticker}): ML detected unusual pattern",
                details={
                    "ml_score": round(ml_score, 3),
                    "anomaly_score": round(anomaly_result.get("normalized_score", 0), 3),
                    "pattern_confidence": round(pattern_result.get("confidence", 0), 3),
                },
                ml_score=ml_score,
            )

        return None

    def auto_train_ml(self, force: bool = False) -> bool:
        """
        Trigger auto-training of ML models if conditions are met.

        Args:
            force: Force retraining even if recently trained

        Returns:
            True if training occurred, False otherwise
        """
        if not self.ml_enabled or not self.ml_detector:
            return False

        return self.ml_detector.auto_train_if_ready(self.db, force=force)

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

    def _get_market_context(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Get market context for a ticker.

        Returns dict with current_price, day_change_pct, week_change_pct
        or None if market data is not available.
        """
        if not self.market_data_enabled or not self.market_data:
            return None

        try:
            return self.market_data.get_market_context(ticker)
        except Exception as e:
            logger.debug(f"Failed to get market context for {ticker}: {e}")
            return None

    def _apply_market_context(
        self, alert: PatternAlert, market_context: Optional[Dict[str, Any]]
    ) -> PatternAlert:
        """
        Apply market context to an alert, enriching the message and details.

        Args:
            alert: The PatternAlert to enrich
            market_context: Market context dict or None

        Returns:
            The enriched PatternAlert
        """
        if not market_context:
            return alert

        # Store market context
        alert.market_context = market_context

        # Add market info to details
        alert.details["market_context"] = market_context

        # Enrich the message with stock movement info
        day_change = market_context.get("day_change_pct")
        if day_change is not None:
            direction = "up" if day_change > 0 else "down"
            alert.message += f" (stock {direction} {abs(day_change):.1f}% today)"

        return alert
