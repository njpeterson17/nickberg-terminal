"""
Unit tests for MLPatternDetector class.
"""

import pytest
import sys
import os
import tempfile
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Check if sklearn is available
try:
    import numpy as np
    from sklearn.ensemble import IsolationForest, RandomForestClassifier

    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

# Import ml_detector module
from ml_detector import MLPatternDetector, SKLEARN_AVAILABLE as MODULE_SKLEARN_AVAILABLE


@pytest.fixture
def ml_config():
    """Sample ML configuration."""
    return {
        "ml_detection": {
            "enabled": True,
            "model_path": "data/test_ml_model.pkl",
            "min_training_articles": 10,  # Lower threshold for testing
            "anomaly_threshold": 0.7,
        }
    }


@pytest.fixture
def disabled_ml_config():
    """ML configuration with detection disabled."""
    return {
        "ml_detection": {
            "enabled": False,
            "model_path": "data/test_ml_model.pkl",
            "min_training_articles": 10,
            "anomaly_threshold": 0.7,
        }
    }


@pytest.fixture
def sample_company_data():
    """Sample company data for feature extraction."""
    return {
        "ticker": "AAPL",
        "count_1h": 5,
        "count_6h": 15,
        "count_24h": 40,
        "count_7d": 150,
        "sentiment_mean": 0.3,
        "sentiment_std": 0.2,
        "sentiment_mean_24h": 0.1,
        "sentiment_std_24h": 0.15,
    }


@pytest.fixture
def historical_training_data():
    """Generate sample historical data for training."""
    data = []
    for i in range(50):
        # Normal pattern
        data.append(
            {
                "ticker": f"TICK{i}",
                "count_1h": 2 + (i % 3),
                "count_6h": 8 + (i % 5),
                "count_24h": 20 + (i % 10),
                "count_7d": 100 + (i % 20),
                "sentiment_mean": 0.1 + (i % 5) * 0.05,
                "sentiment_std": 0.1,
                "sentiment_mean_24h": 0.1,
                "sentiment_std_24h": 0.1,
                "had_alert": False,
            }
        )
    # Add some anomalous patterns
    for i in range(15):
        data.append(
            {
                "ticker": f"ALERT{i}",
                "count_1h": 20 + (i % 5),  # Much higher volume
                "count_6h": 50 + (i % 10),
                "count_24h": 100 + (i % 20),
                "count_7d": 150,
                "sentiment_mean": -0.5 - (i % 3) * 0.1,  # Negative sentiment
                "sentiment_std": 0.3,
                "sentiment_mean_24h": 0.1,
                "sentiment_std_24h": 0.1,
                "had_alert": True,
            }
        )
    return data


@pytest.mark.skipif(not SKLEARN_AVAILABLE, reason="scikit-learn not installed")
class TestMLPatternDetectorInitialization:
    """Tests for MLPatternDetector initialization."""

    def test_initialization_enabled(self, ml_config):
        """Test initialization with ML enabled."""
        detector = MLPatternDetector(ml_config)

        assert detector.enabled is True
        assert detector.anomaly_detector is not None
        assert detector.trend_classifier is not None
        assert detector.scaler is not None
        assert detector.is_trained is False

    def test_initialization_disabled(self, disabled_ml_config):
        """Test initialization with ML disabled."""
        detector = MLPatternDetector(disabled_ml_config)

        assert detector.enabled is False
        assert detector.is_trained is False

    def test_initialization_default_config(self):
        """Test initialization with minimal config."""
        detector = MLPatternDetector({})

        assert detector.enabled is False
        assert detector.model_path == "data/ml_model.pkl"
        assert detector.min_training_articles == 100
        assert detector.anomaly_threshold == 0.7


@pytest.mark.skipif(not SKLEARN_AVAILABLE, reason="scikit-learn not installed")
class TestFeatureExtraction:
    """Tests for feature extraction."""

    def test_extract_features_basic(self, ml_config, sample_company_data):
        """Test basic feature extraction."""
        detector = MLPatternDetector(ml_config)
        features = detector.extract_features(sample_company_data)

        assert features is not None
        assert features.shape == (1, 17)  # 17 features

    def test_extract_features_with_zeros(self, ml_config):
        """Test feature extraction with zero values."""
        detector = MLPatternDetector(ml_config)
        data = {
            "ticker": "TEST",
            "count_1h": 0,
            "count_6h": 0,
            "count_24h": 0,
            "count_7d": 0,
            "sentiment_mean": 0,
            "sentiment_std": 0,
            "sentiment_mean_24h": 0,
            "sentiment_std_24h": 0,
        }
        features = detector.extract_features(data)

        assert features is not None
        assert features.shape == (1, 17)
        # Should handle division by zero gracefully
        assert not any(np.isnan(features.flatten()))
        assert not any(np.isinf(features.flatten()))

    def test_extract_features_missing_fields(self, ml_config):
        """Test feature extraction with missing fields."""
        detector = MLPatternDetector(ml_config)
        data = {"ticker": "TEST"}  # Minimal data
        features = detector.extract_features(data)

        assert features is not None
        assert features.shape == (1, 17)
        # Should use defaults for missing fields
        assert not any(np.isnan(features.flatten()))


@pytest.mark.skipif(not SKLEARN_AVAILABLE, reason="scikit-learn not installed")
class TestModelTraining:
    """Tests for model training."""

    def test_train_with_sufficient_data(self, ml_config, historical_training_data):
        """Test training with sufficient historical data."""
        detector = MLPatternDetector(ml_config)
        success = detector.train(historical_training_data)

        assert success is True
        assert detector.is_trained is True
        assert detector.training_sample_count == len(historical_training_data)
        assert detector.last_training_time is not None

    def test_train_with_insufficient_data(self, ml_config):
        """Test training with insufficient data."""
        detector = MLPatternDetector(ml_config)
        small_data = [
            {"ticker": "TEST", "count_1h": 1, "count_6h": 5, "had_alert": False} for _ in range(5)
        ]
        success = detector.train(small_data)

        assert success is False
        assert detector.is_trained is False

    def test_train_disabled(self, disabled_ml_config, historical_training_data):
        """Test training when ML is disabled."""
        detector = MLPatternDetector(disabled_ml_config)
        success = detector.train(historical_training_data)

        assert success is False
        assert detector.is_trained is False


@pytest.mark.skipif(not SKLEARN_AVAILABLE, reason="scikit-learn not installed")
class TestAnomalyDetection:
    """Tests for anomaly detection."""

    def test_detect_anomalies_trained(
        self, ml_config, historical_training_data, sample_company_data
    ):
        """Test anomaly detection with trained model."""
        detector = MLPatternDetector(ml_config)
        detector.train(historical_training_data)

        result = detector.detect_anomalies(sample_company_data)

        assert "is_anomaly" in result
        assert "anomaly_score" in result
        assert "normalized_score" in result
        # Use bool() to handle numpy bool types
        assert bool(result["is_anomaly"]) in [True, False]
        assert 0 <= result["normalized_score"] <= 1

    def test_detect_anomalies_untrained(self, ml_config, sample_company_data):
        """Test anomaly detection with untrained model."""
        detector = MLPatternDetector(ml_config)
        result = detector.detect_anomalies(sample_company_data)

        assert result["is_anomaly"] is False
        assert result["anomaly_score"] == 0.0
        assert "error" in result

    def test_detect_anomalies_disabled(self, disabled_ml_config, sample_company_data):
        """Test anomaly detection when ML is disabled."""
        detector = MLPatternDetector(disabled_ml_config)
        result = detector.detect_anomalies(sample_company_data)

        assert result["is_anomaly"] is False
        assert "error" in result

    def test_detect_anomalies_high_volume(self, ml_config, historical_training_data):
        """Test anomaly detection with high volume pattern."""
        detector = MLPatternDetector(ml_config)
        detector.train(historical_training_data)

        # High volume pattern that should be flagged
        anomalous_data = {
            "ticker": "ANOM",
            "count_1h": 50,  # Very high
            "count_6h": 100,
            "count_24h": 200,
            "count_7d": 250,
            "sentiment_mean": -0.8,  # Very negative
            "sentiment_std": 0.4,
            "sentiment_mean_24h": 0.2,
            "sentiment_std_24h": 0.1,
        }

        result = detector.detect_anomalies(anomalous_data)
        # The high volume + negative sentiment should score higher
        assert result["normalized_score"] > 0.3


@pytest.mark.skipif(not SKLEARN_AVAILABLE, reason="scikit-learn not installed")
class TestPatternPrediction:
    """Tests for pattern prediction."""

    def test_predict_pattern_trained(
        self, ml_config, historical_training_data, sample_company_data
    ):
        """Test pattern prediction with trained model."""
        detector = MLPatternDetector(ml_config)
        detector.train(historical_training_data)

        result = detector.predict_pattern(sample_company_data)

        assert "is_significant" in result
        assert "confidence" in result
        assert "prediction_class" in result
        assert isinstance(result["is_significant"], bool)
        assert 0 <= result["confidence"] <= 1
        assert result["prediction_class"] in [0, 1]

    def test_predict_pattern_untrained(self, ml_config, sample_company_data):
        """Test pattern prediction with untrained model."""
        detector = MLPatternDetector(ml_config)
        result = detector.predict_pattern(sample_company_data)

        assert result["is_significant"] is False
        assert result["confidence"] == 0.0
        assert "error" in result


@pytest.mark.skipif(not SKLEARN_AVAILABLE, reason="scikit-learn not installed")
class TestCombinedMLScore:
    """Tests for combined ML score."""

    def test_get_ml_score(self, ml_config, historical_training_data, sample_company_data):
        """Test getting combined ML score."""
        detector = MLPatternDetector(ml_config)
        detector.train(historical_training_data)

        result = detector.get_ml_score(sample_company_data)

        assert "ml_score" in result
        assert "anomaly_result" in result
        assert "pattern_result" in result
        assert "is_trained" in result
        assert "training_samples" in result

        assert 0 <= result["ml_score"] <= 1
        assert result["is_trained"] is True
        assert result["training_samples"] == len(historical_training_data)


@pytest.mark.skipif(not SKLEARN_AVAILABLE, reason="scikit-learn not installed")
class TestModelPersistence:
    """Tests for model save/load."""

    def test_save_and_load_model(self, ml_config, historical_training_data):
        """Test saving and loading trained models."""
        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = os.path.join(tmpdir, "test_model.pkl")
            ml_config["ml_detection"]["model_path"] = model_path

            # Train and save
            detector1 = MLPatternDetector(ml_config)
            detector1.train(historical_training_data)
            save_success = detector1.save_model()

            assert save_success is True
            assert os.path.exists(model_path)

            # Load in new detector
            detector2 = MLPatternDetector(ml_config)
            load_success = detector2.load_model()

            assert load_success is True
            assert detector2.is_trained is True
            assert detector2.training_sample_count == len(historical_training_data)

    def test_save_untrained_model(self, ml_config):
        """Test saving untrained model fails gracefully."""
        detector = MLPatternDetector(ml_config)
        success = detector.save_model()

        assert success is False

    def test_load_nonexistent_model(self, ml_config):
        """Test loading nonexistent model fails gracefully."""
        ml_config["ml_detection"]["model_path"] = "/nonexistent/path/model.pkl"
        detector = MLPatternDetector(ml_config)
        success = detector.load_model()

        assert success is False
        assert detector.is_trained is False


@pytest.mark.skipif(not SKLEARN_AVAILABLE, reason="scikit-learn not installed")
class TestAutoTraining:
    """Tests for auto-training functionality."""

    def test_auto_train_if_ready(self, ml_config):
        """Test auto-training with mock database."""
        mock_db = MagicMock()

        # Mock database methods
        mock_db.get_connection.return_value.__enter__ = MagicMock(
            return_value=MagicMock(
                execute=MagicMock(
                    return_value=MagicMock(
                        fetchall=MagicMock(
                            return_value=[
                                {"company_ticker": f"TICK{i}", "company_name": f"Company {i}"}
                                for i in range(20)
                            ]
                        )
                    )
                )
            )
        )
        mock_db.get_connection.return_value.__exit__ = MagicMock(return_value=False)

        # Mock article counts
        mock_db.get_article_count_for_company.return_value = 10

        detector = MLPatternDetector(ml_config)

        # Should attempt training
        with patch.object(detector, "_fetch_historical_data") as mock_fetch:
            # Return enough data for training
            mock_fetch.return_value = [
                {
                    "ticker": f"TICK{i}",
                    "count_1h": 5,
                    "count_6h": 15,
                    "count_24h": 30,
                    "count_7d": 100,
                    "sentiment_mean": 0.1,
                    "sentiment_std": 0.1,
                    "sentiment_mean_24h": 0.1,
                    "sentiment_std_24h": 0.1,
                    "had_alert": i % 5 == 0,
                }
                for i in range(20)
            ]

            with tempfile.TemporaryDirectory() as tmpdir:
                detector.model_path = os.path.join(tmpdir, "model.pkl")
                success = detector.auto_train_if_ready(mock_db)

                assert success is True
                assert detector.is_trained is True


class TestMLDetectorDisabled:
    """Tests for when sklearn is not available."""

    def test_graceful_fallback_no_sklearn(self, ml_config):
        """Test graceful fallback when sklearn import fails."""
        # Even if sklearn is available, test the disabled path
        with patch.dict("sys.modules", {"sklearn": None}):
            detector = MLPatternDetector(ml_config)
            # Should work but be disabled
            result = detector.detect_anomalies({"ticker": "TEST"})
            assert result["is_anomaly"] is False


@pytest.mark.skipif(not SKLEARN_AVAILABLE, reason="scikit-learn not installed")
class TestIntegrationWithPatternDetector:
    """Integration tests with PatternDetector."""

    def test_ml_score_in_alert(self, ml_config, historical_training_data):
        """Test that ML score is added to alerts."""
        detector = MLPatternDetector(ml_config)
        detector.train(historical_training_data)

        # Simulate getting ML score for a company
        company_data = {
            "ticker": "AAPL",
            "count_1h": 10,
            "count_6h": 30,
            "count_24h": 80,
            "count_7d": 200,
            "sentiment_mean": 0.2,
            "sentiment_std": 0.15,
            "sentiment_mean_24h": 0.1,
            "sentiment_std_24h": 0.1,
        }

        result = detector.get_ml_score(company_data)

        # Should have valid ML score
        assert 0 <= result["ml_score"] <= 1
        assert result["is_trained"] is True
