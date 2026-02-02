"""
Correlation analyzer for news-price relationships.

Analyzes how news alerts correlate with stock price movements to measure
prediction accuracy and identify patterns.
"""

from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from collections import defaultdict

from logging_config import get_logger
from database import Database, Alert
from market_data import MarketDataProvider

logger = get_logger(__name__)


@dataclass
class AlertImpact:
    """Analysis of a single alert's impact on price."""

    alert_id: int
    ticker: str
    alert_type: str
    alert_time: datetime
    price_at_alert: Optional[float]
    price_after: Optional[float]
    hours_measured: int
    price_change_pct: Optional[float]
    preceded_significant_move: bool
    move_direction: Optional[str]  # 'up', 'down', or None


@dataclass
class CorrelationStats:
    """Aggregated correlation statistics for a ticker."""

    ticker: str
    total_alerts: int
    alerts_with_data: int
    significant_moves_preceded: int
    average_price_change: Optional[float]
    hit_rate: float  # Percentage of alerts preceding significant moves
    by_alert_type: Dict[str, Dict[str, Any]] = field(default_factory=dict)


class CorrelationAnalyzer:
    """
    Analyze correlations between news alerts and price movements.

    This helps measure how predictive alerts are and which types of alerts
    are most useful for anticipating price changes.
    """

    def __init__(
        self, db: Database, market_data: MarketDataProvider, config: Optional[Dict[str, Any]] = None
    ):
        """
        Initialize the correlation analyzer.

        Args:
            db: Database instance for accessing alerts
            market_data: MarketDataProvider for price data
            config: Optional configuration dict
        """
        self.db = db
        self.market_data = market_data
        self.config = config or {}
        self.significant_move_threshold = self.config.get("significant_move_threshold", 2.0)

    def analyze_alert_impact(self, alert: Alert, hours_after: int = 24) -> Optional[AlertImpact]:
        """
        Analyze the price impact following a specific alert.

        Args:
            alert: The Alert object to analyze
            hours_after: Hours after alert to measure price change

        Returns:
            AlertImpact object with analysis results, or None if data unavailable
        """
        if not alert.company_ticker:
            return None

        try:
            ticker = alert.company_ticker
            alert_time = (
                alert.created_at
                if isinstance(alert.created_at, datetime)
                else datetime.fromisoformat(str(alert.created_at))
            )

            # Get price at alert time (using that day's close)
            price_at_alert = self.market_data.get_price(ticker, alert_time)

            # Get price after specified hours
            after_time = alert_time + timedelta(hours=hours_after)
            price_after = self.market_data.get_price(ticker, after_time)

            # Calculate change
            price_change_pct = None
            preceded_significant = False
            move_direction = None

            if price_at_alert and price_after and price_at_alert > 0:
                price_change_pct = ((price_after - price_at_alert) / price_at_alert) * 100
                price_change_pct = round(price_change_pct, 2)

                preceded_significant = abs(price_change_pct) >= self.significant_move_threshold
                if price_change_pct > 0:
                    move_direction = "up"
                elif price_change_pct < 0:
                    move_direction = "down"

            return AlertImpact(
                alert_id=alert.id or 0,
                ticker=ticker,
                alert_type=alert.alert_type,
                alert_time=alert_time,
                price_at_alert=price_at_alert,
                price_after=price_after,
                hours_measured=hours_after,
                price_change_pct=price_change_pct,
                preceded_significant_move=preceded_significant,
                move_direction=move_direction,
            )

        except Exception as e:
            logger.warning(f"Failed to analyze alert impact: {e}")
            return None

    def calculate_correlation(self, ticker: str, days: int = 30) -> Optional[CorrelationStats]:
        """
        Calculate correlation between alert volume and price volatility.

        Args:
            ticker: Stock ticker to analyze
            days: Number of days to look back

        Returns:
            CorrelationStats with aggregated metrics, or None if insufficient data
        """
        try:
            # Get alerts for this ticker in the time period
            since = datetime.now() - timedelta(days=days)
            alerts = self._get_ticker_alerts(ticker, since)

            if not alerts:
                return CorrelationStats(
                    ticker=ticker,
                    total_alerts=0,
                    alerts_with_data=0,
                    significant_moves_preceded=0,
                    average_price_change=None,
                    hit_rate=0.0,
                )

            # Analyze each alert
            impacts: List[AlertImpact] = []
            for alert in alerts:
                impact = self.analyze_alert_impact(alert)
                if impact:
                    impacts.append(impact)

            # Calculate aggregate stats
            total = len(alerts)
            with_data = len([i for i in impacts if i.price_change_pct is not None])
            significant = len([i for i in impacts if i.preceded_significant_move])

            avg_change = None
            if with_data > 0:
                changes = [i.price_change_pct for i in impacts if i.price_change_pct is not None]
                avg_change = round(sum(changes) / len(changes), 2)

            hit_rate = (significant / with_data * 100) if with_data > 0 else 0.0

            # Break down by alert type
            by_type: Dict[str, Dict[str, Any]] = defaultdict(
                lambda: {"count": 0, "significant_moves": 0, "avg_change": 0.0, "changes": []}
            )

            for impact in impacts:
                by_type[impact.alert_type]["count"] += 1
                if impact.preceded_significant_move:
                    by_type[impact.alert_type]["significant_moves"] += 1
                if impact.price_change_pct is not None:
                    by_type[impact.alert_type]["changes"].append(impact.price_change_pct)

            # Calculate averages per type
            for alert_type in by_type:
                changes = by_type[alert_type]["changes"]
                if changes:
                    by_type[alert_type]["avg_change"] = round(sum(changes) / len(changes), 2)
                del by_type[alert_type]["changes"]  # Remove temp list

            return CorrelationStats(
                ticker=ticker,
                total_alerts=total,
                alerts_with_data=with_data,
                significant_moves_preceded=significant,
                average_price_change=avg_change,
                hit_rate=round(hit_rate, 1),
                by_alert_type=dict(by_type),
            )

        except Exception as e:
            logger.warning(f"Failed to calculate correlation for {ticker}: {e}")
            return None

    def score_alert_accuracy(
        self,
        alerts: Optional[List[Alert]] = None,
        lookback_days: int = 30,
        ticker: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Score how predictive alerts have been overall.

        Args:
            alerts: List of alerts to analyze (if None, fetches from DB)
            lookback_days: Days to look back when fetching alerts
            ticker: Optional ticker to filter alerts

        Returns:
            Dict with accuracy metrics by alert type
        """
        try:
            if alerts is None:
                since = datetime.now() - timedelta(days=lookback_days)
                if ticker:
                    alerts = self._get_ticker_alerts(ticker, since)
                else:
                    alerts = self._get_all_alerts(since)

            if not alerts:
                return {
                    "total_alerts": 0,
                    "accuracy_by_type": {},
                    "overall_hit_rate": 0.0,
                    "message": "No alerts found in the specified period",
                }

            # Group by type and analyze
            by_type: Dict[str, Dict[str, Any]] = defaultdict(
                lambda: {
                    "total": 0,
                    "with_data": 0,
                    "preceded_significant": 0,
                    "correct_direction": 0,
                    "avg_abs_change": 0.0,
                    "changes": [],
                }
            )

            all_impacts: List[AlertImpact] = []

            for alert in alerts:
                impact = self.analyze_alert_impact(alert)
                if impact:
                    all_impacts.append(impact)
                    alert_type = impact.alert_type

                    by_type[alert_type]["total"] += 1

                    if impact.price_change_pct is not None:
                        by_type[alert_type]["with_data"] += 1
                        by_type[alert_type]["changes"].append(impact.price_change_pct)

                        if impact.preceded_significant_move:
                            by_type[alert_type]["preceded_significant"] += 1

                            # Check if direction matched alert type expectation
                            if self._direction_matches_alert(impact):
                                by_type[alert_type]["correct_direction"] += 1

            # Calculate final metrics
            accuracy_by_type = {}
            for alert_type, data in by_type.items():
                changes = data["changes"]
                with_data = data["with_data"]

                accuracy_by_type[alert_type] = {
                    "total_alerts": data["total"],
                    "alerts_with_data": with_data,
                    "significant_moves": data["preceded_significant"],
                    "hit_rate_pct": round(data["preceded_significant"] / with_data * 100, 1)
                    if with_data > 0
                    else 0.0,
                    "correct_direction_pct": round(
                        data["correct_direction"] / data["preceded_significant"] * 100, 1
                    )
                    if data["preceded_significant"] > 0
                    else 0.0,
                    "avg_absolute_change": round(sum(abs(c) for c in changes) / len(changes), 2)
                    if changes
                    else 0.0,
                }

            # Overall stats
            total_with_data = sum(d["with_data"] for d in by_type.values())
            total_significant = sum(d["preceded_significant"] for d in by_type.values())
            overall_hit_rate = (
                (total_significant / total_with_data * 100) if total_with_data > 0 else 0.0
            )

            return {
                "total_alerts": len(alerts),
                "alerts_with_price_data": total_with_data,
                "overall_hit_rate": round(overall_hit_rate, 1),
                "accuracy_by_type": accuracy_by_type,
                "lookback_days": lookback_days,
            }

        except Exception as e:
            logger.warning(f"Failed to score alert accuracy: {e}")
            return {
                "error": str(e),
                "total_alerts": 0,
                "accuracy_by_type": {},
                "overall_hit_rate": 0.0,
            }

    def _direction_matches_alert(self, impact: AlertImpact) -> bool:
        """
        Check if price direction matches what alert type would predict.

        For example:
        - negative_cluster alerts should precede down moves
        - momentum alerts with positive sentiment should precede up moves
        """
        if not impact.move_direction or not impact.preceded_significant_move:
            return False

        alert_type = impact.alert_type.lower()

        # Negative alert types should precede down moves
        if "negative" in alert_type:
            return impact.move_direction == "down"

        # Volume spikes are directionally neutral, any significant move counts
        if "volume" in alert_type or "spike" in alert_type:
            return True

        # For other types, any significant move is considered a match
        return True

    def _get_ticker_alerts(self, ticker: str, since: datetime) -> List[Alert]:
        """Get alerts for a specific ticker since a date."""
        try:
            with self.db.get_connection() as conn:
                rows = conn.execute(
                    """
                    SELECT id, alert_type, company_ticker, company_name,
                           severity, message, details, created_at, acknowledged
                    FROM alerts
                    WHERE company_ticker = ? AND created_at > ?
                    ORDER BY created_at DESC
                """,
                    (ticker, since),
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
                        acknowledged=bool(row["acknowledged"]),
                    )
                    for row in rows
                ]
        except Exception as e:
            logger.warning(f"Failed to get alerts for {ticker}: {e}")
            return []

    def _get_all_alerts(self, since: datetime) -> List[Alert]:
        """Get all alerts since a date."""
        try:
            with self.db.get_connection() as conn:
                rows = conn.execute(
                    """
                    SELECT id, alert_type, company_ticker, company_name,
                           severity, message, details, created_at, acknowledged
                    FROM alerts
                    WHERE created_at > ?
                    ORDER BY created_at DESC
                """,
                    (since,),
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
                        acknowledged=bool(row["acknowledged"]),
                    )
                    for row in rows
                ]
        except Exception as e:
            logger.warning(f"Failed to get all alerts: {e}")
            return []

    def get_correlation_report(self, ticker: str, days: int = 30) -> Dict[str, Any]:
        """
        Generate a comprehensive correlation report for a ticker.

        This is the main method for the API endpoint.

        Args:
            ticker: Stock ticker symbol
            days: Days of history to analyze

        Returns:
            Dict with correlation stats, recent alert accuracy, and market context
        """
        try:
            # Get correlation stats
            correlation = self.calculate_correlation(ticker, days)

            # Get accuracy scores
            accuracy = self.score_alert_accuracy(ticker=ticker, lookback_days=days)

            # Get current market context
            market_context = self.market_data.get_market_context(ticker)

            # Get recent alert impacts
            since = datetime.now() - timedelta(days=min(days, 7))
            recent_alerts = self._get_ticker_alerts(ticker, since)
            recent_impacts = []

            for alert in recent_alerts[:10]:  # Limit to 10 most recent
                impact = self.analyze_alert_impact(alert)
                if impact:
                    recent_impacts.append(
                        {
                            "alert_id": impact.alert_id,
                            "alert_type": impact.alert_type,
                            "alert_time": impact.alert_time.isoformat(),
                            "price_at_alert": impact.price_at_alert,
                            "price_after": impact.price_after,
                            "price_change_pct": impact.price_change_pct,
                            "preceded_significant_move": impact.preceded_significant_move,
                            "move_direction": impact.move_direction,
                        }
                    )

            return {
                "ticker": ticker,
                "analysis_period_days": days,
                "generated_at": datetime.now().isoformat(),
                "market_context": market_context,
                "correlation_stats": {
                    "total_alerts": correlation.total_alerts if correlation else 0,
                    "alerts_with_data": correlation.alerts_with_data if correlation else 0,
                    "significant_moves_preceded": correlation.significant_moves_preceded
                    if correlation
                    else 0,
                    "average_price_change": correlation.average_price_change
                    if correlation
                    else None,
                    "hit_rate_pct": correlation.hit_rate if correlation else 0.0,
                    "by_alert_type": correlation.by_alert_type if correlation else {},
                },
                "accuracy_metrics": accuracy,
                "recent_impacts": recent_impacts,
            }

        except Exception as e:
            logger.error(f"Failed to generate correlation report for {ticker}: {e}")
            return {"ticker": ticker, "error": str(e), "generated_at": datetime.now().isoformat()}
