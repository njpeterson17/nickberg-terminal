"""
ML-based pattern detection for news analysis.

This module provides machine learning capabilities to augment the rule-based
pattern detection with anomaly detection and trend classification.
"""

import logging
import pickle
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

# Try to import sklearn, but allow graceful fallback
try:
    from sklearn.ensemble import IsolationForest, RandomForestClassifier
    from sklearn.preprocessing import StandardScaler

    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False
    logger.warning("scikit-learn not available. ML detection features disabled.")


class MLPatternDetector:
    """
    Machine learning pattern detector for news analysis.

    Uses Isolation Forest for anomaly detection on article volume patterns
    and a Random Forest classifier for trend significance prediction.
    """

    def __init__(self, config: dict[str, Any]):
        """
        Initialize the ML pattern detector.

        Args:
            config: Configuration dictionary with ml_detection settings
        """
        self.config = config
        self.ml_config = config.get("ml_detection", {})
        self.enabled = self.ml_config.get("enabled", False) and SKLEARN_AVAILABLE
        self.model_path = self.ml_config.get("model_path", "data/ml_model.pkl")
        self.min_training_articles = self.ml_config.get("min_training_articles", 100)
        self.anomaly_threshold = self.ml_config.get("anomaly_threshold", 0.7)

        # Models
        self.anomaly_detector: Optional[Any] = None
        self.trend_classifier: Optional[Any] = None
        self.scaler: Optional[Any] = None

        # Training state
        self.is_trained = False
        self.last_training_time: Optional[datetime] = None
        self.training_sample_count = 0

        if self.enabled:
            self._initialize_models()

    def _initialize_models(self):
        """Initialize the ML models."""
        if not SKLEARN_AVAILABLE:
            logger.warning("Cannot initialize ML models: scikit-learn not available")
            return

        # Isolation Forest for anomaly detection
        # Contamination is the expected proportion of outliers
        self.anomaly_detector = IsolationForest(
            n_estimators=100,
            contamination=0.1,  # Expect ~10% anomalies
            random_state=42,
            n_jobs=-1,
        )

        # Random Forest for trend classification
        self.trend_classifier = RandomForestClassifier(
            n_estimators=50,
            max_depth=10,
            random_state=42,
            n_jobs=-1,
        )

        # Standard scaler for feature normalization
        self.scaler = StandardScaler()

        logger.info("ML models initialized")

    def extract_features(self, company_data: dict[str, Any]) -> np.ndarray:
        """
        Extract ML features from company news data.

        Features extracted:
        - Article count in last 1h, 6h, 24h, 7d
        - Sentiment mean and std in recent windows
        - Day of week, hour of day
        - Rolling averages and ratios

        Args:
            company_data: Dictionary containing company news statistics

        Returns:
            Feature vector as numpy array
        """
        # Article counts
        count_1h = company_data.get("count_1h", 0)
        count_6h = company_data.get("count_6h", 0)
        count_24h = company_data.get("count_24h", 0)
        count_7d = company_data.get("count_7d", 0)

        # Sentiment statistics
        sentiment_mean = company_data.get("sentiment_mean", 0.0)
        sentiment_std = company_data.get("sentiment_std", 0.0)
        sentiment_mean_24h = company_data.get("sentiment_mean_24h", 0.0)
        sentiment_std_24h = company_data.get("sentiment_std_24h", 0.0)

        # Time features
        now = datetime.now()
        hour_of_day = now.hour
        day_of_week = now.weekday()

        # Derived features - rolling averages
        avg_daily = count_7d / 7.0 if count_7d > 0 else 0
        avg_hourly = count_24h / 24.0 if count_24h > 0 else 0

        # Ratios (with safety for division by zero)
        ratio_1h_to_daily = count_1h / avg_hourly if avg_hourly > 0 else 0
        ratio_6h_to_24h = count_6h / count_24h if count_24h > 0 else 0
        ratio_24h_to_7d = count_24h / count_7d if count_7d > 0 else 0

        # Velocity (rate of change)
        velocity_6h = count_6h - (count_24h - count_6h) / 3 if count_24h > 0 else 0

        # Sentiment change
        sentiment_change = sentiment_mean - sentiment_mean_24h

        # Create feature vector
        features = np.array(
            [
                count_1h,
                count_6h,
                count_24h,
                count_7d,
                sentiment_mean,
                sentiment_std,
                sentiment_mean_24h,
                sentiment_std_24h,
                hour_of_day,
                day_of_week,
                avg_daily,
                avg_hourly,
                ratio_1h_to_daily,
                ratio_6h_to_24h,
                ratio_24h_to_7d,
                velocity_6h,
                sentiment_change,
            ]
        )

        return features.reshape(1, -1)

    def train(self, historical_data: list[dict[str, Any]]) -> bool:
        """
        Train ML models on historical pattern data.

        Args:
            historical_data: List of historical company data dictionaries

        Returns:
            True if training successful, False otherwise
        """
        if not self.enabled:
            logger.debug("ML detection not enabled, skipping training")
            return False

        if not SKLEARN_AVAILABLE:
            logger.warning("Cannot train: scikit-learn not available")
            return False

        if len(historical_data) < self.min_training_articles:
            logger.info(
                "Not enough data for training",
                extra={
                    "available": len(historical_data),
                    "required": self.min_training_articles,
                },
            )
            return False

        try:
            # Extract features from all historical data
            features_list = []
            labels = []

            for data in historical_data:
                features = self.extract_features(data)
                features_list.append(features.flatten())

                # Generate label based on rule-based detection results
                # 1 = significant pattern, 0 = normal
                label = 1 if data.get("had_alert", False) else 0
                labels.append(label)

            X = np.array(features_list)
            y = np.array(labels)

            # Fit the scaler
            X_scaled = self.scaler.fit_transform(X)

            # Train anomaly detector (unsupervised)
            self.anomaly_detector.fit(X_scaled)

            # Train trend classifier (supervised) if we have labeled data
            if sum(labels) > 0:  # At least some positive examples
                self.trend_classifier.fit(X_scaled, y)
                logger.info(
                    "Trend classifier trained",
                    extra={
                        "positive_samples": sum(labels),
                        "total_samples": len(labels),
                    },
                )

            self.is_trained = True
            self.last_training_time = datetime.now()
            self.training_sample_count = len(historical_data)

            logger.info(
                "ML models trained successfully",
                extra={
                    "samples": len(historical_data),
                    "training_time": self.last_training_time.isoformat(),
                },
            )

            return True

        except Exception as e:
            logger.error("Failed to train ML models", extra={"error": str(e)})
            return False

    def detect_anomalies(self, company_data: dict[str, Any]) -> dict[str, Any]:
        """
        Detect anomalies in company news patterns using Isolation Forest.

        Args:
            company_data: Dictionary containing company news statistics

        Returns:
            Dictionary with anomaly detection results:
            - is_anomaly: Boolean indicating if pattern is anomalous
            - anomaly_score: Score from -1 (anomaly) to 1 (normal)
            - normalized_score: Score normalized to 0-1 range (higher = more anomalous)
        """
        if not self.enabled or not self.is_trained:
            return {
                "is_anomaly": False,
                "anomaly_score": 0.0,
                "normalized_score": 0.0,
                "error": "ML detection not enabled or not trained",
            }

        try:
            features = self.extract_features(company_data)
            features_scaled = self.scaler.transform(features)

            # Get anomaly prediction (-1 = anomaly, 1 = normal)
            prediction = self.anomaly_detector.predict(features_scaled)[0]

            # Get anomaly score (more negative = more anomalous)
            # decision_function returns the anomaly score
            raw_score = self.anomaly_detector.decision_function(features_scaled)[0]

            # Normalize score to 0-1 range (higher = more anomalous)
            # Raw scores typically range from about -0.5 to 0.5
            normalized_score = max(0, min(1, (0.5 - raw_score)))

            is_anomaly = prediction == -1 and normalized_score >= self.anomaly_threshold

            return {
                "is_anomaly": is_anomaly,
                "anomaly_score": float(raw_score),
                "normalized_score": float(normalized_score),
            }

        except Exception as e:
            logger.error("Anomaly detection failed", extra={"error": str(e)})
            return {
                "is_anomaly": False,
                "anomaly_score": 0.0,
                "normalized_score": 0.0,
                "error": str(e),
            }

    def predict_pattern(self, company_data: dict[str, Any]) -> dict[str, Any]:
        """
        Predict if a pattern is significant using trained classifier.

        Args:
            company_data: Dictionary containing company news statistics

        Returns:
            Dictionary with prediction results:
            - is_significant: Boolean indicating if pattern is significant
            - confidence: Probability score (0-1)
            - prediction_class: 0 or 1
        """
        if not self.enabled or not self.is_trained:
            return {
                "is_significant": False,
                "confidence": 0.0,
                "prediction_class": 0,
                "error": "ML detection not enabled or not trained",
            }

        try:
            features = self.extract_features(company_data)
            features_scaled = self.scaler.transform(features)

            # Get prediction
            prediction = self.trend_classifier.predict(features_scaled)[0]

            # Get probability
            probabilities = self.trend_classifier.predict_proba(features_scaled)[0]
            confidence = probabilities[1] if len(probabilities) > 1 else probabilities[0]

            return {
                "is_significant": bool(prediction == 1),
                "confidence": float(confidence),
                "prediction_class": int(prediction),
            }

        except Exception as e:
            logger.error("Pattern prediction failed", extra={"error": str(e)})
            return {
                "is_significant": False,
                "confidence": 0.0,
                "prediction_class": 0,
                "error": str(e),
            }

    def get_ml_score(self, company_data: dict[str, Any]) -> dict[str, Any]:
        """
        Get combined ML score for a company's news pattern.

        Combines anomaly detection and pattern prediction into a single score.

        Args:
            company_data: Dictionary containing company news statistics

        Returns:
            Dictionary with combined ML analysis:
            - ml_score: Combined score (0-1, higher = more significant)
            - anomaly_result: Anomaly detection results
            - pattern_result: Pattern prediction results
        """
        anomaly_result = self.detect_anomalies(company_data)
        pattern_result = self.predict_pattern(company_data)

        # Combine scores with weighted average
        # Anomaly detection weight: 0.4, Pattern classification weight: 0.6
        anomaly_weight = 0.4
        pattern_weight = 0.6

        combined_score = (
            anomaly_result.get("normalized_score", 0) * anomaly_weight
            + pattern_result.get("confidence", 0) * pattern_weight
        )

        return {
            "ml_score": float(combined_score),
            "anomaly_result": anomaly_result,
            "pattern_result": pattern_result,
            "is_trained": self.is_trained,
            "training_samples": self.training_sample_count,
        }

    def save_model(self, path: Optional[str] = None) -> bool:
        """
        Save trained models to disk.

        Args:
            path: Optional path override (defaults to config model_path)

        Returns:
            True if save successful, False otherwise
        """
        if not self.is_trained:
            logger.warning("Cannot save untrained models")
            return False

        save_path = Path(path or self.model_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            model_data = {
                "anomaly_detector": self.anomaly_detector,
                "trend_classifier": self.trend_classifier,
                "scaler": self.scaler,
                "is_trained": self.is_trained,
                "last_training_time": self.last_training_time,
                "training_sample_count": self.training_sample_count,
                "config": self.ml_config,
            }

            with open(save_path, "wb") as f:
                pickle.dump(model_data, f)

            logger.info("ML models saved", extra={"path": str(save_path)})
            return True

        except Exception as e:
            logger.error("Failed to save ML models", extra={"error": str(e)})
            return False

    def load_model(self, path: Optional[str] = None) -> bool:
        """
        Load trained models from disk.

        Args:
            path: Optional path override (defaults to config model_path)

        Returns:
            True if load successful, False otherwise
        """
        if not SKLEARN_AVAILABLE:
            logger.warning("Cannot load models: scikit-learn not available")
            return False

        load_path = Path(path or self.model_path)

        if not load_path.exists():
            logger.debug("No saved model found", extra={"path": str(load_path)})
            return False

        try:
            with open(load_path, "rb") as f:
                model_data = pickle.load(f)

            self.anomaly_detector = model_data["anomaly_detector"]
            self.trend_classifier = model_data["trend_classifier"]
            self.scaler = model_data["scaler"]
            self.is_trained = model_data["is_trained"]
            self.last_training_time = model_data.get("last_training_time")
            self.training_sample_count = model_data.get("training_sample_count", 0)

            logger.info(
                "ML models loaded",
                extra={
                    "path": str(load_path),
                    "training_samples": self.training_sample_count,
                    "last_trained": (
                        self.last_training_time.isoformat() if self.last_training_time else None
                    ),
                },
            )
            return True

        except Exception as e:
            logger.error("Failed to load ML models", extra={"error": str(e)})
            return False

    def auto_train_if_ready(self, db, force: bool = False) -> bool:
        """
        Automatically train models if enough historical data exists.

        Args:
            db: Database instance to fetch historical data
            force: Force retraining even if already trained

        Returns:
            True if training occurred and succeeded, False otherwise
        """
        if not self.enabled:
            return False

        # Check if already trained and not forcing
        if self.is_trained and not force:
            # Check if we should retrain (e.g., if more than 24h since last training)
            if self.last_training_time:
                hours_since_training = (
                    datetime.now() - self.last_training_time
                ).total_seconds() / 3600
                if hours_since_training < 24:
                    logger.debug(
                        "Skipping retraining, too recent",
                        extra={"hours_since": round(hours_since_training, 1)},
                    )
                    return False

        try:
            # Get historical data from database
            historical_data = self._fetch_historical_data(db)

            if len(historical_data) < self.min_training_articles:
                logger.info(
                    "Not enough historical data for auto-training",
                    extra={
                        "available": len(historical_data),
                        "required": self.min_training_articles,
                    },
                )
                return False

            # Train the models
            success = self.train(historical_data)

            if success:
                # Save the trained models
                self.save_model()

            return success

        except Exception as e:
            logger.error("Auto-training failed", extra={"error": str(e)})
            return False

    def _fetch_historical_data(self, db) -> list[dict[str, Any]]:
        """
        Fetch historical data from database for training.

        Args:
            db: Database instance

        Returns:
            List of historical company data dictionaries
        """
        historical_data = []

        try:
            # Get all companies with mentions
            with db.get_connection() as conn:
                # Get mention counts by company over the last 30 days
                companies = conn.execute(
                    """
                    SELECT DISTINCT company_ticker, company_name
                    FROM company_mentions
                    WHERE mentioned_at > datetime('now', '-30 days')
                    """
                ).fetchall()

                for company in companies:
                    ticker = company["company_ticker"]

                    # Get article counts at different time windows
                    count_1h = db.get_article_count_for_company(ticker, hours=1)
                    count_6h = db.get_article_count_for_company(ticker, hours=6)
                    count_24h = db.get_article_count_for_company(ticker, hours=24)
                    count_7d = db.get_article_count_for_company(ticker, hours=168)

                    # Get sentiment data
                    sentiment_data = self._get_sentiment_stats(conn, ticker)

                    # Check if there was a recent alert
                    had_alert = self._check_for_recent_alert(conn, ticker)

                    company_data = {
                        "ticker": ticker,
                        "count_1h": count_1h,
                        "count_6h": count_6h,
                        "count_24h": count_24h,
                        "count_7d": count_7d,
                        "sentiment_mean": sentiment_data.get("mean", 0),
                        "sentiment_std": sentiment_data.get("std", 0),
                        "sentiment_mean_24h": sentiment_data.get("mean_24h", 0),
                        "sentiment_std_24h": sentiment_data.get("std_24h", 0),
                        "had_alert": had_alert,
                    }

                    historical_data.append(company_data)

        except Exception as e:
            logger.error("Failed to fetch historical data", extra={"error": str(e)})

        return historical_data

    def _get_sentiment_stats(self, conn, ticker: str) -> dict[str, float]:
        """Get sentiment statistics for a company."""
        try:
            # Get sentiment scores from articles mentioning this company
            rows = conn.execute(
                """
                SELECT a.sentiment_score
                FROM articles a
                JOIN company_mentions cm ON a.id = cm.article_id
                WHERE cm.company_ticker = ?
                AND a.sentiment_score IS NOT NULL
                AND a.published_at > datetime('now', '-7 days')
                """,
                (ticker,),
            ).fetchall()

            if not rows:
                return {"mean": 0, "std": 0, "mean_24h": 0, "std_24h": 0}

            scores = [row["sentiment_score"] for row in rows]
            mean = sum(scores) / len(scores)
            std = (sum((s - mean) ** 2 for s in scores) / len(scores)) ** 0.5

            # Get 24h stats
            rows_24h = conn.execute(
                """
                SELECT a.sentiment_score
                FROM articles a
                JOIN company_mentions cm ON a.id = cm.article_id
                WHERE cm.company_ticker = ?
                AND a.sentiment_score IS NOT NULL
                AND a.published_at > datetime('now', '-1 day')
                """,
                (ticker,),
            ).fetchall()

            if rows_24h:
                scores_24h = [row["sentiment_score"] for row in rows_24h]
                mean_24h = sum(scores_24h) / len(scores_24h)
                std_24h = (sum((s - mean_24h) ** 2 for s in scores_24h) / len(scores_24h)) ** 0.5
            else:
                mean_24h = mean
                std_24h = std

            return {"mean": mean, "std": std, "mean_24h": mean_24h, "std_24h": std_24h}

        except Exception:
            return {"mean": 0, "std": 0, "mean_24h": 0, "std_24h": 0}

    def _check_for_recent_alert(self, conn, ticker: str) -> bool:
        """Check if there was a recent alert for this company."""
        try:
            row = conn.execute(
                """
                SELECT COUNT(*) as count
                FROM alerts
                WHERE company_ticker = ?
                AND created_at > datetime('now', '-7 days')
                """,
                (ticker,),
            ).fetchone()

            return row["count"] > 0 if row else False

        except Exception:
            return False
