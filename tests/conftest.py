"""
Shared pytest fixtures for nickberg-terminal tests.
"""

import pytest
from unittest.mock import MagicMock
from datetime import datetime, timedelta


@pytest.fixture
def sample_watchlist():
    """Sample company watchlist for testing."""
    return {
        "AAPL": ["Apple", "Apple Inc", "Apple Inc."],
        "GOOGL": ["Google", "Alphabet", "Alphabet Inc"],
        "MSFT": ["Microsoft", "Microsoft Corp", "Microsoft Corporation"],
        "TSLA": ["Tesla", "Tesla Inc", "Tesla Motors"],
        "AMZN": ["Amazon", "Amazon.com", "Amazon Inc"],
        "META": ["Meta", "Meta Platforms", "Facebook"],
        "NVDA": ["Nvidia", "NVIDIA", "Nvidia Corp"],
    }


@pytest.fixture
def positive_keywords():
    """Sample positive sentiment keywords."""
    return [
        "growth",
        "profit",
        "gain",
        "surge",
        "rise",
        "bullish",
        "upgrade",
        "beat",
        "exceeds",
        "strong",
        "positive",
        "success",
        "breakthrough",
        "innovative",
        "record",
        "outperform",
        "momentum",
        "rally",
        "soar",
        "boost",
    ]


@pytest.fixture
def negative_keywords():
    """Sample negative sentiment keywords."""
    return [
        "loss",
        "decline",
        "drop",
        "fall",
        "bearish",
        "downgrade",
        "miss",
        "below",
        "weak",
        "negative",
        "concern",
        "risk",
        "crash",
        "plunge",
        "scandal",
        "fraud",
        "investigation",
        "layoffs",
        "bankruptcy",
        "lawsuit",
        "warning",
        "trouble",
    ]


@pytest.fixture
def mock_database():
    """Create a mock database for pattern detector tests."""
    db = MagicMock()
    return db


@pytest.fixture
def sample_config(positive_keywords, negative_keywords):
    """Sample configuration for pattern detector."""
    return {
        "windows": {"short": 6, "medium": 24, "long": 168},
        "volume_spike_threshold": 3.0,
        "min_articles_for_alert": 3,
        "sentiment_keywords": {"positive": positive_keywords, "negative": negative_keywords},
    }


@pytest.fixture
def sample_articles():
    """Generate sample articles for testing."""
    now = datetime.now()
    return [
        {
            "id": 1,
            "title": "Apple Reports Record Revenue",
            "content": "Apple Inc announced record quarterly revenue today, "
            "showing strong growth in services and wearables. "
            "The company beat analyst expectations significantly.",
            "published_at": now - timedelta(hours=2),
            "source": "financial_times",
        },
        {
            "id": 2,
            "title": "Apple Stock Surges on Earnings",
            "content": "Apple shares surge after the company reports positive "
            "earnings that exceeded Wall Street expectations. "
            "Investors are bullish on future growth prospects.",
            "published_at": now - timedelta(hours=4),
            "source": "reuters",
        },
        {
            "id": 3,
            "title": "Apple Faces Regulatory Investigation",
            "content": "Apple faces a new investigation into its App Store practices. "
            "Concerns about monopolistic behavior have led to regulatory "
            "scrutiny. The lawsuit could result in significant fines.",
            "published_at": now - timedelta(hours=5),
            "source": "wsj",
        },
        {
            "id": 4,
            "title": "Tech Sector Mixed as Microsoft Rises",
            "content": "Microsoft Corp saw gains today while other tech stocks "
            "showed mixed results. Strong cloud revenue boosted "
            "investor confidence in the company.",
            "published_at": now - timedelta(hours=6),
            "source": "bloomberg",
        },
        {
            "id": 5,
            "title": "Tesla Layoffs Announced",
            "content": "Tesla Inc announced significant layoffs affecting thousands "
            "of workers. The news caused Tesla stock to plunge in "
            "early trading amid concerns about the company's direction.",
            "published_at": now - timedelta(hours=3),
            "source": "cnbc",
        },
    ]


@pytest.fixture
def negative_articles():
    """Generate sample negative articles for testing negative cluster detection."""
    now = datetime.now()
    return [
        {
            "id": 1,
            "content": "Company faces major investigation into fraud allegations. "
            "The scandal has rocked investors and caused significant concern.",
            "published_at": now - timedelta(hours=1),
        },
        {
            "id": 2,
            "content": "Stock crashes after disappointing earnings miss expectations. "
            "Analysts downgrade the stock citing weak performance.",
            "published_at": now - timedelta(hours=2),
        },
        {
            "id": 3,
            "content": "Lawsuit filed against company for bankruptcy-related issues. "
            "The legal troubles add to mounting concerns about viability.",
            "published_at": now - timedelta(hours=3),
        },
    ]


@pytest.fixture
def positive_articles():
    """Generate sample positive articles for testing."""
    now = datetime.now()
    return [
        {
            "id": 1,
            "content": "Company reports massive profit growth and record revenue. "
            "Analysts upgrade stock rating to strong buy.",
            "published_at": now - timedelta(hours=1),
        },
        {
            "id": 2,
            "content": "Stock surges on breakthrough product announcement. "
            "Investors are extremely bullish on future prospects.",
            "published_at": now - timedelta(hours=2),
        },
        {
            "id": 3,
            "content": "Company beats expectations with innovative new technology. "
            "Success drives positive momentum in the market.",
            "published_at": now - timedelta(hours=3),
        },
    ]


@pytest.fixture
def neutral_articles():
    """Generate sample neutral articles for testing."""
    now = datetime.now()
    return [
        {
            "id": 1,
            "content": "Company announced routine quarterly results today. "
            "The numbers were in line with expectations.",
            "published_at": now - timedelta(hours=1),
        },
        {
            "id": 2,
            "content": "CEO spoke at industry conference about general trends. "
            "No major announcements were made during the presentation.",
            "published_at": now - timedelta(hours=2),
        },
    ]
