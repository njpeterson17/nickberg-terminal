"""
Unit tests for CompanyExtractor and SentimentAnalyzer classes.
"""

import pytest
import sys
import os

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from company_extractor import (
    CompanyExtractor,
    CompanyMatch,
    KeywordSentimentAnalyzer,
    SentimentAnalyzer,
    VADER_AVAILABLE,
)


class TestCompanyExtractor:
    """Tests for CompanyExtractor class."""

    def test_basic_company_matching(self, sample_watchlist):
        """Test basic company name extraction from text."""
        extractor = CompanyExtractor(sample_watchlist)

        text = "Apple announced new products today. The company expects strong sales."
        matches = extractor.extract(text)

        assert len(matches) == 1
        assert matches[0].ticker == "AAPL"
        assert matches[0].name == "Apple"

    def test_multiple_company_matching(self, sample_watchlist):
        """Test extraction of multiple companies from text."""
        extractor = CompanyExtractor(sample_watchlist)

        text = """
        Tech stocks rose today with Apple and Microsoft leading the gains.
        Google also saw significant trading volume as investors moved into tech.
        """
        matches = extractor.extract(text)

        tickers = {m.ticker for m in matches}
        assert "AAPL" in tickers
        assert "MSFT" in tickers
        assert "GOOGL" in tickers
        assert len(matches) == 3

    def test_case_insensitive_matching(self, sample_watchlist):
        """Test that company matching is case insensitive."""
        extractor = CompanyExtractor(sample_watchlist)

        text = "APPLE and apple and Apple all refer to the same company."
        matches = extractor.extract(text)

        # Should only match once per ticker
        assert len(matches) == 1
        assert matches[0].ticker == "AAPL"

    def test_company_name_variations(self, sample_watchlist):
        """Test matching different name variations."""
        extractor = CompanyExtractor(sample_watchlist)

        # Test "Apple Inc" variation
        text1 = "Apple Inc reported earnings today."
        matches1 = extractor.extract(text1)
        assert len(matches1) == 1
        assert matches1[0].ticker == "AAPL"

        # Test "Microsoft Corporation" variation
        text2 = "Microsoft Corporation announced a new product."
        matches2 = extractor.extract(text2)
        assert len(matches2) == 1
        assert matches2[0].ticker == "MSFT"

    def test_ticker_symbol_with_dollar_sign(self, sample_watchlist):
        """Test matching ticker symbols with $ prefix."""
        extractor = CompanyExtractor(sample_watchlist)

        text = "Investors are bullish on $AAPL and $TSLA today."
        matches = extractor.extract(text)

        tickers = {m.ticker for m in matches}
        assert "AAPL" in tickers
        assert "TSLA" in tickers

    def test_ticker_with_stock_keyword(self, sample_watchlist):
        """Test matching ticker when followed by 'stock' or 'shares'."""
        extractor = CompanyExtractor(sample_watchlist)

        text = "MSFT stock rose 5% while TSLA shares declined."
        matches = extractor.extract(text)

        tickers = {m.ticker for m in matches}
        assert "MSFT" in tickers
        assert "TSLA" in tickers

    def test_no_matches_for_unknown_companies(self, sample_watchlist):
        """Test that unknown company names are not matched."""
        extractor = CompanyExtractor(sample_watchlist)

        text = "Unknown Corp and Random Inc announced a merger."
        matches = extractor.extract(text)

        assert len(matches) == 0

    def test_partial_word_not_matched(self, sample_watchlist):
        """Test that partial word matches are rejected."""
        extractor = CompanyExtractor(sample_watchlist)

        # "Applewood" should not match "Apple"
        text = "Applewood restaurant opened a new location today."
        matches = extractor.extract(text)

        assert len(matches) == 0

    def test_context_window_extraction(self, sample_watchlist):
        """Test that context is extracted around matches."""
        extractor = CompanyExtractor(sample_watchlist)

        text = "A" * 200 + " Apple " + "B" * 200
        matches = extractor.extract(text, context_window=50)

        assert len(matches) == 1
        # Context should be around 100 chars (50 before + match + 50 after)
        assert len(matches[0].context) <= 110

    def test_confidence_scoring_exact_case(self, sample_watchlist):
        """Test confidence boost for exact case match."""
        extractor = CompanyExtractor(sample_watchlist)

        # Exact case match should have higher confidence
        text1 = "Apple reported earnings."
        matches1 = extractor.extract(text1)

        text2 = "apple reported earnings."
        matches2 = extractor.extract(text2)

        # Both should match, but exact case should have higher confidence
        assert matches1[0].confidence >= matches2[0].confidence

    def test_confidence_boost_financial_context(self, sample_watchlist):
        """Test confidence boost for financial context words."""
        extractor = CompanyExtractor(sample_watchlist)

        # Financial context should boost confidence
        text_financial = "Apple stock trading showed strong earnings and revenue growth."
        matches_financial = extractor.extract(text_financial)

        text_general = "Apple made an announcement today."
        matches_general = extractor.extract(text_general)

        assert matches_financial[0].confidence > matches_general[0].confidence

    def test_add_company_to_watchlist(self, sample_watchlist):
        """Test dynamically adding a company to the watchlist."""
        extractor = CompanyExtractor(sample_watchlist)

        # Initially should not match
        text = "AMD announced new chips today."
        matches = extractor.extract(text)
        assert len(matches) == 0

        # Add AMD to watchlist
        extractor.add_company("AMD", ["AMD", "Advanced Micro Devices"])

        # Now should match
        matches = extractor.extract(text)
        assert len(matches) == 1
        assert matches[0].ticker == "AMD"

    def test_add_company_case_normalization(self, sample_watchlist):
        """Test that ticker is normalized to uppercase when adding."""
        extractor = CompanyExtractor(sample_watchlist)

        extractor.add_company("amd", ["AMD", "Advanced Micro Devices"])

        assert "AMD" in extractor.watchlist
        assert "amd" not in extractor.watchlist

    def test_special_characters_in_company_names(self):
        """Test matching company names with special characters."""
        watchlist = {"AMZN": ["Amazon.com", "Amazon"], "BRK.A": ["Berkshire Hathaway"]}
        extractor = CompanyExtractor(watchlist)

        text = "Amazon.com reported strong quarterly results."
        matches = extractor.extract(text)

        assert len(matches) == 1
        assert matches[0].ticker == "AMZN"

    def test_empty_text(self, sample_watchlist):
        """Test extraction from empty text."""
        extractor = CompanyExtractor(sample_watchlist)

        matches = extractor.extract("")
        assert len(matches) == 0

    def test_empty_watchlist(self):
        """Test extraction with empty watchlist."""
        extractor = CompanyExtractor({})

        text = "Apple and Microsoft reported earnings."
        matches = extractor.extract(text)

        assert len(matches) == 0


class TestKeywordSentimentAnalyzer:
    """Tests for KeywordSentimentAnalyzer class (keyword-based sentiment)."""

    def test_positive_sentiment(self, positive_keywords, negative_keywords):
        """Test detection of positive sentiment."""
        analyzer = KeywordSentimentAnalyzer(positive_keywords, negative_keywords)

        text = "The company reported strong growth and record profit this quarter."
        score = analyzer.analyze(text)

        assert score > 0
        assert score <= 1.0

    def test_negative_sentiment(self, positive_keywords, negative_keywords):
        """Test detection of negative sentiment."""
        analyzer = KeywordSentimentAnalyzer(positive_keywords, negative_keywords)

        text = "The company faced a major scandal and decline in sales."
        score = analyzer.analyze(text)

        assert score < 0
        assert score >= -1.0

    def test_neutral_sentiment(self, positive_keywords, negative_keywords):
        """Test neutral text returns zero score."""
        analyzer = KeywordSentimentAnalyzer(positive_keywords, negative_keywords)

        text = "The company held its annual meeting on Tuesday."
        score = analyzer.analyze(text)

        assert score == 0.0

    def test_mixed_sentiment_balanced(self, positive_keywords, negative_keywords):
        """Test that balanced positive/negative results in neutral."""
        analyzer = KeywordSentimentAnalyzer(positive_keywords, negative_keywords)

        text = "Despite the decline in one sector, the company saw growth in another."
        score = analyzer.analyze(text)

        # Should be close to neutral due to balance
        assert abs(score) < 0.5

    def test_intensifier_boost(self, positive_keywords, negative_keywords):
        """Test that intensifiers increase score magnitude."""
        analyzer = KeywordSentimentAnalyzer(positive_keywords, negative_keywords)

        text_normal = "The company reported growth."
        text_intensified = "The company reported extremely strong growth."

        score_normal = analyzer.analyze(text_normal)
        score_intensified = analyzer.analyze(text_intensified)

        # Intensified should have higher magnitude
        assert abs(score_intensified) >= abs(score_normal)

    def test_diminisher_reduction(self, positive_keywords, negative_keywords):
        """Test that diminishers reduce score magnitude."""
        analyzer = KeywordSentimentAnalyzer(positive_keywords, negative_keywords)

        text_normal = "The company reported growth."
        text_diminished = "The company reported slightly positive growth."

        score_normal = analyzer.analyze(text_normal)
        score_diminished = analyzer.analyze(text_diminished)

        # Both positive, but diminished should have lower magnitude
        assert score_normal > 0
        assert score_diminished > 0
        assert score_diminished <= score_normal

    def test_score_clamped_to_range(self, positive_keywords, negative_keywords):
        """Test that scores are clamped to [-1, 1]."""
        analyzer = KeywordSentimentAnalyzer(positive_keywords, negative_keywords)

        # Text with many positive words and intensifiers
        text_very_positive = (
            "Extremely strong growth, massive profit, huge surge, "
            "significant gains, record success, breakthrough innovation"
        )
        score = analyzer.analyze(text_very_positive)

        assert score <= 1.0

        # Text with many negative words
        text_very_negative = (
            "Major crash, huge decline, massive loss, significant concern, "
            "scandal and fraud, investigation and lawsuit"
        )
        score = analyzer.analyze(text_very_negative)

        assert score >= -1.0

    def test_case_insensitive_matching(self, positive_keywords, negative_keywords):
        """Test that sentiment keywords are matched case-insensitively."""
        analyzer = KeywordSentimentAnalyzer(positive_keywords, negative_keywords)

        text_lower = "the company reported strong growth"
        text_upper = "THE COMPANY REPORTED STRONG GROWTH"
        text_mixed = "The Company Reported Strong Growth"

        score_lower = analyzer.analyze(text_lower)
        score_upper = analyzer.analyze(text_upper)
        score_mixed = analyzer.analyze(text_mixed)

        assert score_lower == score_upper == score_mixed

    def test_analyze_batch(self, positive_keywords, negative_keywords):
        """Test batch sentiment analysis."""
        analyzer = KeywordSentimentAnalyzer(positive_keywords, negative_keywords)

        texts = [
            "Strong growth and record profit.",
            "Major decline and significant loss.",
            "The meeting was held on Tuesday.",
        ]

        result = analyzer.analyze_batch(texts)

        assert "average" in result
        assert "positive_count" in result
        assert "negative_count" in result
        assert "neutral_count" in result
        assert result["positive_count"] == 1
        assert result["negative_count"] == 1
        assert result["neutral_count"] == 1

    def test_analyze_batch_empty_list(self, positive_keywords, negative_keywords):
        """Test batch analysis with empty list."""
        analyzer = KeywordSentimentAnalyzer(positive_keywords, negative_keywords)

        result = analyzer.analyze_batch([])

        assert result["average"] == 0
        assert result["positive_count"] == 0
        assert result["negative_count"] == 0
        assert result["neutral_count"] == 0

    def test_multiple_keyword_occurrences(self, positive_keywords, negative_keywords):
        """Test that multiple occurrences of keywords are counted."""
        analyzer = KeywordSentimentAnalyzer(positive_keywords, negative_keywords)

        text_single = "The company showed growth."
        text_multiple = "Growth, growth, and more growth in every sector."

        score_single = analyzer.analyze(text_single)
        score_multiple = analyzer.analyze(text_multiple)

        # Multiple mentions should still result in positive score
        # (though score calculation normalizes by total matches)
        assert score_single > 0
        assert score_multiple > 0

    def test_highly_negative_text(self, positive_keywords, negative_keywords):
        """Test strongly negative sentiment detection."""
        analyzer = KeywordSentimentAnalyzer(positive_keywords, negative_keywords)

        text = (
            "The investigation revealed fraud and scandal. "
            "The company faces bankruptcy and lawsuits. "
            "Stock crash and plunge expected."
        )
        score = analyzer.analyze(text)

        assert score < -0.5

    def test_highly_positive_text(self, positive_keywords, negative_keywords):
        """Test strongly positive sentiment detection."""
        analyzer = KeywordSentimentAnalyzer(positive_keywords, negative_keywords)

        text = (
            "Record profit and massive growth. "
            "The breakthrough success continues to surge. "
            "Bullish momentum drives rally."
        )
        score = analyzer.analyze(text)

        assert score > 0.5


class TestSentimentAnalyzerUnified:
    """Tests for the unified SentimentAnalyzer class."""

    def test_keyword_method_explicit(self, positive_keywords, negative_keywords):
        """Test SentimentAnalyzer with explicit keyword method."""
        analyzer = SentimentAnalyzer(
            positive_words=positive_keywords, negative_words=negative_keywords, method="keyword"
        )

        assert analyzer.active_method == "keyword"

        text = "Strong growth and profit."
        score = analyzer.analyze(text)
        assert score > 0

    def test_keyword_method_requires_words(self):
        """Test that keyword method raises error without word lists."""
        with pytest.raises(ValueError):
            SentimentAnalyzer(method="keyword")

    @pytest.mark.skipif(not VADER_AVAILABLE, reason="VADER not installed")
    def test_ml_method(self):
        """Test SentimentAnalyzer with ML (VADER) method."""
        analyzer = SentimentAnalyzer(method="ml")

        assert analyzer.active_method == "ml"

        # Positive text
        score = analyzer.analyze("This is absolutely wonderful and amazing!")
        assert score > 0

        # Negative text
        score = analyzer.analyze("This is terrible and awful, very disappointing.")
        assert score < 0

    @pytest.mark.skipif(not VADER_AVAILABLE, reason="VADER not installed")
    def test_auto_uses_ml_when_available(self, positive_keywords, negative_keywords):
        """Test that auto mode uses ML when VADER is available."""
        analyzer = SentimentAnalyzer(
            positive_words=positive_keywords, negative_words=negative_keywords, method="auto"
        )

        # When VADER is available, auto should use ML
        assert analyzer.active_method == "ml"

    def test_analyze_batch_unified(self, positive_keywords, negative_keywords):
        """Test batch analysis through unified analyzer."""
        analyzer = SentimentAnalyzer(
            positive_words=positive_keywords, negative_words=negative_keywords, method="keyword"
        )

        texts = ["Growth and profit.", "Decline and loss.", "Meeting today."]
        result = analyzer.analyze_batch(texts)

        assert "average" in result
        assert "positive_count" in result
        assert "negative_count" in result
        assert "neutral_count" in result

    def test_analyze_detailed(self, positive_keywords, negative_keywords):
        """Test detailed analysis through unified analyzer."""
        analyzer = SentimentAnalyzer(
            positive_words=positive_keywords, negative_words=negative_keywords, method="keyword"
        )

        result = analyzer.analyze_detailed("Strong growth reported.")

        assert "neg" in result
        assert "neu" in result
        assert "pos" in result
        assert "compound" in result

    def test_word_list_properties(self, positive_keywords, negative_keywords):
        """Test backward compatibility word list properties."""
        analyzer = SentimentAnalyzer(
            positive_words=positive_keywords, negative_words=negative_keywords, method="keyword"
        )

        assert analyzer.positive_words == positive_keywords
        assert analyzer.negative_words == negative_keywords
