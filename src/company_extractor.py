"""
Company name and ticker extraction from article text
"""

import re
import json
import logging
from typing import List, Dict, Tuple, Set, Optional, Literal
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

# Try to import VADER for ML-based sentiment analysis
try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer as VaderAnalyzer

    VADER_AVAILABLE = True
except ImportError:
    VADER_AVAILABLE = False
    logger.warning(
        "vaderSentiment not installed. ML sentiment analysis unavailable. "
        "Install with: pip install vaderSentiment"
    )


class SentimentMethod(Enum):
    """Sentiment analysis method selection"""

    KEYWORD = "keyword"
    ML = "ml"
    AUTO = "auto"  # Use ML if available, fallback to keyword


@dataclass
class CompanyMatch:
    ticker: str
    name: str
    confidence: float  # 0-1
    context: str  # Surrounding text


class CompanyExtractor:
    """Extract company mentions from text"""

    def __init__(self, watchlist: dict[str, list[str]]):
        """
        Initialize with company watchlist
        watchlist: {ticker: [name variations]}
        Example: {"AAPL": ["Apple", "Apple Inc", "AAPL"]}
        """
        self.watchlist = watchlist
        self._build_patterns()

    def _build_patterns(self):
        """Build regex patterns for matching"""
        self.patterns = {}

        for ticker, names in self.watchlist.items():
            patterns = []
            for name in names:
                # Create pattern that matches whole words
                # Handle special characters in company names
                escaped = re.escape(name)
                # Use word boundaries for most cases
                pattern = r"\b" + escaped + r"\b"
                patterns.append((name, re.compile(pattern, re.IGNORECASE)))

            self.patterns[ticker] = patterns

        # Common ticker-only patterns (when mentioned with $ or as standalone)
        self.ticker_pattern = re.compile(
            r"\$([A-Z]{1,5})\b|\b([A-Z]{2,5})\s+(?:stock|shares|equity)", re.IGNORECASE
        )

    def extract(self, text: str, context_window: int = 100) -> list[CompanyMatch]:
        """
        Extract company mentions from text
        Returns list of CompanyMatch objects
        """
        matches = []
        found_tickers = set()

        # Check each company in watchlist
        for ticker, patterns in self.patterns.items():
            for name, pattern in patterns:
                for match in pattern.finditer(text):
                    # Extract context around the match
                    start = max(0, match.start() - context_window)
                    end = min(len(text), match.end() + context_window)
                    context = text[start:end]

                    # Calculate confidence based on match quality
                    confidence = self._calculate_confidence(name, context)

                    if ticker not in found_tickers:
                        matches.append(
                            CompanyMatch(
                                ticker=ticker, name=name, confidence=confidence, context=context
                            )
                        )
                        found_tickers.add(ticker)
                    break  # Only count once per ticker

        # Also look for ticker symbols with $ prefix
        for match in self.ticker_pattern.finditer(text):
            ticker = match.group(1) or match.group(2)
            if ticker and ticker.upper() in self.watchlist:
                if ticker.upper() not in found_tickers:
                    start = max(0, match.start() - context_window)
                    end = min(len(text), match.end() + context_window)
                    context = text[start:end]

                    matches.append(
                        CompanyMatch(
                            ticker=ticker.upper(),
                            name=self.watchlist[ticker.upper()][0],
                            confidence=0.9,
                            context=context,
                        )
                    )
                    found_tickers.add(ticker.upper())

        return matches

    def _calculate_confidence(self, name: str, context: str) -> float:
        """Calculate confidence score for a match"""
        confidence = 0.5  # Base confidence

        context_lower = context.lower()
        name_lower = name.lower()

        # Boost confidence for exact case matches
        if name in context:
            confidence += 0.2

        # Boost for financial context words
        financial_words = [
            "stock",
            "shares",
            "trading",
            "investors",
            "earnings",
            "revenue",
            "profit",
            "market",
            "company",
            "corp",
            "inc",
        ]
        if any(word in context_lower for word in financial_words):
            confidence += 0.2

        # Boost for ticker symbol nearby
        words = context.split()
        for word in words:
            clean = word.strip("$.,;:!?()[]{}\"'").upper()
            if clean in self.watchlist:
                confidence += 0.1
                break

        return min(confidence, 1.0)

    def add_company(self, ticker: str, names: list[str]):
        """Add a new company to the watchlist"""
        ticker = ticker.upper()
        if ticker not in self.watchlist:
            self.watchlist[ticker] = names

            # Build patterns for new company
            patterns = []
            for name in names:
                escaped = re.escape(name)
                pattern = r"\b" + escaped + r"\b"
                patterns.append((name, re.compile(pattern, re.IGNORECASE)))

            self.patterns[ticker] = patterns
            logger.info(f"Added company to watchlist: {ticker} - {names[0]}")


class KeywordSentimentAnalyzer:
    """Simple keyword-based sentiment analysis"""

    def __init__(self, positive_words: list[str], negative_words: list[str]):
        self.positive_words = [w.lower() for w in positive_words]
        self.negative_words = [w.lower() for w in negative_words]

        # Compile patterns
        self.positive_pattern = re.compile(
            r"\b(" + "|".join(re.escape(w) for w in self.positive_words) + r")\b", re.IGNORECASE
        )
        self.negative_pattern = re.compile(
            r"\b(" + "|".join(re.escape(w) for w in self.negative_words) + r")\b", re.IGNORECASE
        )

        # Intensity modifiers
        self.intensifiers = [
            "very",
            "extremely",
            "significantly",
            "massively",
            "hugely",
            "dramatically",
            "substantially",
        ]
        self.diminishers = ["slightly", "somewhat", "relatively", "fairly", "pretty"]

    def analyze(self, text: str) -> float:
        """
        Analyze sentiment of text
        Returns score between -1 (very negative) and 1 (very positive)
        """
        text_lower = text.lower()

        # Count positive and negative mentions
        pos_matches = len(self.positive_pattern.findall(text_lower))
        neg_matches = len(self.negative_pattern.findall(text_lower))

        if pos_matches == 0 and neg_matches == 0:
            return 0.0  # Neutral

        # Calculate base score
        total = pos_matches + neg_matches
        score = (pos_matches - neg_matches) / total

        # Adjust for intensity modifiers
        for intensifier in self.intensifiers:
            if intensifier in text_lower:
                score *= 1.2
                break

        for diminisher in self.diminishers:
            if diminisher in text_lower:
                score *= 0.8
                break

        # Clamp to [-1, 1]
        return max(-1.0, min(1.0, score))

    def analyze_batch(self, texts: list[str]) -> dict[str, float]:
        """Analyze sentiment of multiple texts"""
        return {
            "average": sum(self.analyze(t) for t in texts) / len(texts) if texts else 0,
            "positive_count": sum(1 for t in texts if self.analyze(t) > 0.2),
            "negative_count": sum(1 for t in texts if self.analyze(t) < -0.2),
            "neutral_count": sum(1 for t in texts if -0.2 <= self.analyze(t) <= 0.2),
        }


class MLSentimentAnalyzer:
    """ML-based sentiment analysis using VADER

    VADER (Valence Aware Dictionary and sEntiment Reasoner) is specifically
    attuned to sentiments expressed in social media and works well for news headlines.
    """

    def __init__(self):
        if not VADER_AVAILABLE:
            raise ImportError(
                "vaderSentiment is not installed. Install with: pip install vaderSentiment"
            )
        self._analyzer = VaderAnalyzer()

    def analyze(self, text: str) -> float:
        """
        Analyze sentiment of text using VADER
        Returns score between -1 (very negative) and 1 (very positive)

        VADER returns a 'compound' score that is already normalized to [-1, 1]
        """
        scores = self._analyzer.polarity_scores(text)
        return scores["compound"]

    def analyze_detailed(self, text: str) -> dict[str, float]:
        """
        Get detailed sentiment breakdown

        Returns:
            dict with keys: 'neg', 'neu', 'pos', 'compound'
            - neg/neu/pos: proportion of text that falls in each category (sum to 1)
            - compound: normalized score from -1 to 1
        """
        return self._analyzer.polarity_scores(text)

    def analyze_batch(self, texts: list[str]) -> dict[str, float]:
        """Analyze sentiment of multiple texts"""
        if not texts:
            return {"average": 0, "positive_count": 0, "negative_count": 0, "neutral_count": 0}

        scores = [self.analyze(t) for t in texts]
        return {
            "average": sum(scores) / len(scores),
            "positive_count": sum(1 for s in scores if s > 0.05),
            "negative_count": sum(1 for s in scores if s < -0.05),
            "neutral_count": sum(1 for s in scores if -0.05 <= s <= 0.05),
        }


class SentimentAnalyzer:
    """Unified sentiment analyzer with configurable method

    Supports both keyword-based and ML-based (VADER) sentiment analysis.
    This class maintains backward compatibility with the original API.

    Args:
        positive_words: List of positive sentiment words (for keyword method)
        negative_words: List of negative sentiment words (for keyword method)
        method: Sentiment analysis method - 'keyword', 'ml', or 'auto'
                'auto' uses ML if available, falls back to keyword

    Example:
        # Using keyword method (original behavior)
        analyzer = SentimentAnalyzer(positive_words, negative_words, method='keyword')

        # Using ML method (VADER)
        analyzer = SentimentAnalyzer(method='ml')

        # Auto-select (ML if available, else keyword)
        analyzer = SentimentAnalyzer(positive_words, negative_words, method='auto')
    """

    def __init__(
        self,
        positive_words: list[str] | None = None,
        negative_words: list[str] | None = None,
        method: str = "auto",
    ):
        self.method = SentimentMethod(method)
        self._keyword_analyzer: KeywordSentimentAnalyzer | None = None
        self._ml_analyzer: MLSentimentAnalyzer | None = None

        # Store word lists for potential keyword analyzer initialization
        self._positive_words = positive_words or []
        self._negative_words = negative_words or []

        # Initialize the appropriate analyzer(s)
        self._init_analyzers()

    def _init_analyzers(self):
        """Initialize the appropriate sentiment analyzer(s)"""
        if self.method == SentimentMethod.KEYWORD:
            if not self._positive_words and not self._negative_words:
                raise ValueError("Keyword method requires positive_words and/or negative_words")
            self._keyword_analyzer = KeywordSentimentAnalyzer(
                self._positive_words, self._negative_words
            )
            self._active_method = "keyword"

        elif self.method == SentimentMethod.ML:
            if not VADER_AVAILABLE:
                raise ImportError(
                    "ML method requested but vaderSentiment is not installed. "
                    "Install with: pip install vaderSentiment"
                )
            self._ml_analyzer = MLSentimentAnalyzer()
            self._active_method = "ml"

        else:  # AUTO
            if VADER_AVAILABLE:
                self._ml_analyzer = MLSentimentAnalyzer()
                self._active_method = "ml"
                logger.info("Using ML (VADER) sentiment analysis")
            elif self._positive_words or self._negative_words:
                self._keyword_analyzer = KeywordSentimentAnalyzer(
                    self._positive_words, self._negative_words
                )
                self._active_method = "keyword"
                logger.info("VADER not available, using keyword sentiment analysis")
            else:
                raise ValueError(
                    "Neither VADER is available nor keyword lists provided. "
                    "Install vaderSentiment or provide positive/negative word lists."
                )

    @property
    def active_method(self) -> str:
        """Returns the currently active sentiment analysis method"""
        return self._active_method

    def analyze(self, text: str) -> float:
        """
        Analyze sentiment of text
        Returns score between -1 (very negative) and 1 (very positive)
        """
        if self._active_method == "ml":
            return self._ml_analyzer.analyze(text)
        else:
            return self._keyword_analyzer.analyze(text)

    def analyze_detailed(self, text: str) -> dict[str, float]:
        """
        Get detailed sentiment analysis (ML method only)

        For ML method, returns VADER's detailed breakdown.
        For keyword method, returns basic positive/negative counts.
        """
        if self._active_method == "ml":
            return self._ml_analyzer.analyze_detailed(text)
        else:
            # Provide similar structure for keyword method
            score = self._keyword_analyzer.analyze(text)
            if score > 0:
                return {"neg": 0, "neu": 1 - abs(score), "pos": abs(score), "compound": score}
            elif score < 0:
                return {"neg": abs(score), "neu": 1 - abs(score), "pos": 0, "compound": score}
            else:
                return {"neg": 0, "neu": 1, "pos": 0, "compound": 0}

    def analyze_batch(self, texts: list[str]) -> dict[str, float]:
        """Analyze sentiment of multiple texts"""
        if self._active_method == "ml":
            return self._ml_analyzer.analyze_batch(texts)
        else:
            return self._keyword_analyzer.analyze_batch(texts)

    # Backward compatibility: expose word lists as properties
    @property
    def positive_words(self) -> list[str]:
        """Get positive words list (keyword method)"""
        return self._positive_words

    @property
    def negative_words(self) -> list[str]:
        """Get negative words list (keyword method)"""
        return self._negative_words
