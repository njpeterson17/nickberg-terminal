"""
Pydantic models for API request/response validation
"""

from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field, field_validator


# =============================================================================
# Response Models
# =============================================================================

class ArticleResponse(BaseModel):
    """Article data returned by API"""
    id: int
    title: str
    source: str
    url: str
    published_at: str | None = None
    scraped_at: str | None = None
    sentiment: float | None = Field(None, ge=-1.0, le=1.0)
    mentions: list[str] = []


class AlertResponse(BaseModel):
    """Alert data returned by API"""
    id: int
    type: str
    ticker: str
    company: str
    severity: str
    message: str
    details: dict[str, Any] = {}
    created_at: str | None = None


class StatsResponse(BaseModel):
    """Database statistics"""
    total_articles: int
    total_mentions: int
    total_alerts: int
    articles_24h: int


class HealthResponse(BaseModel):
    """Health check response"""
    status: str
    components: dict[str, str]
    version: str


class CompanyMentionCount(BaseModel):
    """Company mention count data"""
    company_ticker: str
    company_name: str
    count: int
    last_mention: str | None = None


class SentimentDistribution(BaseModel):
    """Sentiment distribution data"""
    positive: int
    negative: int
    neutral: int
    total: int


class SourceCount(BaseModel):
    """Source article count"""
    source: str
    count: int


# =============================================================================
# Request Models
# =============================================================================

class PreferencesRequest(BaseModel):
    """Request body for saving preferences"""
    thresholds: dict[str, float | int] | None = None
    alert_channels: dict[str, bool] | None = None
    severity_routing: dict[str, list[str]] | None = None
    company_preferences: dict[str, Any] | None = None

    @field_validator('thresholds')
    @classmethod
    def validate_thresholds(cls, v: dict | None) -> dict | None:
        if v is None:
            return v
        if 'volume_spike' in v:
            vol = v['volume_spike']
            if not isinstance(vol, (int, float)) or vol < 1.0 or vol > 20.0:
                raise ValueError("volume_spike must be between 1.0 and 20.0")
        if 'min_articles' in v:
            min_art = v['min_articles']
            if not isinstance(min_art, int) or min_art < 1 or min_art > 50:
                raise ValueError("min_articles must be between 1 and 50")
        if 'sentiment_shift' in v:
            sent = v['sentiment_shift']
            if not isinstance(sent, (int, float)) or sent < 0.1 or sent > 1.0:
                raise ValueError("sentiment_shift must be between 0.1 and 1.0")
        return v

    @field_validator('alert_channels')
    @classmethod
    def validate_alert_channels(cls, v: dict | None) -> dict | None:
        if v is None:
            return v
        valid_channels = {'telegram', 'webhook', 'file', 'console'}
        for channel, enabled in v.items():
            if channel not in valid_channels:
                raise ValueError(f"Unknown channel: {channel}")
            if not isinstance(enabled, bool):
                raise ValueError(f"Channel {channel} must be boolean")
        return v

    @field_validator('severity_routing')
    @classmethod
    def validate_severity_routing(cls, v: dict | None) -> dict | None:
        if v is None:
            return v
        valid_severities = {'high', 'medium', 'low'}
        for severity, channels in v.items():
            if severity not in valid_severities:
                raise ValueError(f"Unknown severity: {severity}")
            if not isinstance(channels, list):
                raise ValueError(f"Channels for {severity} must be a list")
        return v


class WatchlistAddRequest(BaseModel):
    """Request to add a company to watchlist"""
    action: str = Field(..., pattern="^(add|remove|replace)$")
    ticker: str | None = None
    names: list[str] | None = None
    watchlist: dict[str, list[str]] | None = None

    @field_validator('ticker', mode='before')
    @classmethod
    def validate_ticker(cls, v: str | None) -> str | None:
        if v is not None:
            # Strip whitespace and uppercase before any other validation
            v = str(v).strip().upper()
            if not v:
                return None
            if len(v) > 5:
                raise ValueError("Ticker must be 5 characters or less")
            if not v.isalpha():
                raise ValueError("Ticker must contain only letters")
        return v


class AlertRulesRequest(BaseModel):
    """Request to update alert rules"""
    alert_channels: dict[str, bool] | None = None
    severity_routing: dict[str, list[str]] | None = None
    company_preferences: dict[str, Any] | None = None


# =============================================================================
# API Error Response
# =============================================================================

class ErrorResponse(BaseModel):
    """Standard error response"""
    error: str
    message: str | None = None
    details: dict[str, Any] | None = None
