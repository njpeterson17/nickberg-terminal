"""
News Sentinel Bot - Web Dashboard
Flask application for monitoring and visualization
"""

import os
import sys
import json
from datetime import datetime, timedelta
from pathlib import Path
from functools import wraps

from flask import Flask, render_template, jsonify, request, send_from_directory, Response, g

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from database import Database
from alerts import AlertManager
from logging_config import setup_logging, get_logger

# Import market data modules with fallback
try:
    from market_data import MarketDataProvider
    from correlation_analyzer import CorrelationAnalyzer
    MARKET_DATA_AVAILABLE = True
except ImportError:
    MarketDataProvider = None
    CorrelationAnalyzer = None
    MARKET_DATA_AVAILABLE = False

# Setup logging for web app
setup_logging()
logger = get_logger(__name__)

app = Flask(__name__)

# API Authentication
# Read API key from environment variable
API_KEY = os.environ.get('NEWS_SENTINEL_API_KEY', '')


def require_api_key(f):
    """
    Decorator to require API key authentication for endpoints.

    If NEWS_SENTINEL_API_KEY env var is not set, allows unauthenticated access
    for backwards compatibility.

    API key can be provided via:
    - X-API-Key header
    - api_key query parameter
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # If no API key is configured, allow unauthenticated access
        if not API_KEY:
            return f(*args, **kwargs)

        # Check for API key in header or query param
        provided_key = request.headers.get('X-API-Key') or request.args.get('api_key')

        if not provided_key:
            logger.warning(
                "API key required but not provided",
                extra={'endpoint': request.endpoint, 'path': request.path}
            )
            return jsonify({'error': 'API key required', 'message': 'Provide API key via X-API-Key header or api_key query parameter'}), 401

        if provided_key != API_KEY:
            logger.warning(
                "Invalid API key provided",
                extra={'endpoint': request.endpoint, 'path': request.path}
            )
            return jsonify({'error': 'Invalid API key'}), 403

        return f(*args, **kwargs)
    return decorated_function

# Load config
import yaml
CONFIG_PATH = Path(__file__).parent.parent / 'config' / 'settings.yaml'
with open(CONFIG_PATH) as f:
    config = yaml.safe_load(f)

# Initialize database (use absolute path from project root)
DB_PATH = Path(__file__).parent.parent / config['database']['path']
db = Database(str(DB_PATH))

# Path to last scrape timestamp file
LAST_SCRAPE_FILE = Path(__file__).parent.parent / 'data' / 'last_scrape.json'

# Application version
APP_VERSION = "1.0.0"

# Initialize market data provider and correlation analyzer if available
market_data_provider = None
correlation_analyzer = None
if MARKET_DATA_AVAILABLE:
    market_config = config.get('market_data', {})
    if market_config.get('enabled', False):
        try:
            market_data_provider = MarketDataProvider(market_config)
            correlation_analyzer = CorrelationAnalyzer(db, market_data_provider, market_config)
            logger.info("Market data and correlation analyzer initialized")
        except Exception as e:
            logger.warning(f"Failed to initialize market data provider: {e}")


# Request logging
import time as _time

@app.before_request
def before_request():
    """Log incoming requests and start timing"""
    g.start_time = _time.time()


@app.after_request
def after_request(response):
    """Log request completion with timing and status"""
    # Skip logging for static files and health checks to reduce noise
    if request.path.startswith('/static') or request.path == '/health':
        return response

    duration = _time.time() - getattr(g, 'start_time', _time.time())
    logger.info(
        "Request processed",
        extra={
            'method': request.method,
            'path': request.path,
            'status_code': response.status_code,
            'duration_ms': round(duration * 1000, 2),
            'remote_addr': request.remote_addr
        }
    )
    return response


def get_last_scrape_time():
    """Get the timestamp of the last successful scrape"""
    try:
        if LAST_SCRAPE_FILE.exists():
            with open(LAST_SCRAPE_FILE, 'r') as f:
                data = json.load(f)
                return data.get('last_scrape')
    except (json.JSONDecodeError, IOError):
        pass
    return None


# Helper functions
def get_db_stats():
    """Get database statistics"""
    return db.get_stats()


def get_recent_alerts(limit=20):
    """Get recent unacknowledged alerts"""
    alerts = db.get_unacknowledged_alerts(limit)
    return [{
        'id': a.id,
        'type': a.alert_type,
        'ticker': a.company_ticker,
        'company': a.company_name,
        'severity': a.severity,
        'message': a.message,
        'details': json.loads(a.details) if a.details else {},
        'created_at': a.created_at if isinstance(a.created_at, str) else a.created_at.isoformat() if a.created_at else None
    } for a in alerts]


def get_mention_timeline(hours=24):
    """Get mention counts over time"""
    with db.get_connection() as conn:
        since = datetime.now() - timedelta(hours=hours)
        
        # Get mentions grouped by hour
        rows = conn.execute("""
            SELECT 
                strftime('%Y-%m-%d %H:00', mentioned_at) as hour,
                company_ticker,
                company_name,
                COUNT(*) as count
            FROM company_mentions
            WHERE mentioned_at > ?
            GROUP BY hour, company_ticker
            ORDER BY hour ASC
        """, (since,)).fetchall()
        
        # Organize by company
        timeline = {}
        for row in rows:
            ticker = row['company_ticker']
            if ticker not in timeline:
                timeline[ticker] = {
                    'name': row['company_name'],
                    'data': []
                }
            timeline[ticker]['data'].append({
                'time': row['hour'],
                'count': row['count']
            })
        
        return timeline


def get_top_companies(limit=10):
    """Get top mentioned companies"""
    companies = db.get_mention_counts(hours=24)
    return companies[:limit]


def format_datetime(dt):
    """Format datetime, handling both string and datetime objects"""
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    return dt.isoformat()


def get_recent_articles(limit=50):
    """Get recent articles"""
    articles = db.get_recent_articles(limit)
    return [{
        'id': a.id,
        'title': a.title,
        'source': a.source,
        'url': a.url,
        'published_at': format_datetime(a.published_at),
        'scraped_at': format_datetime(a.scraped_at),
        'sentiment': a.sentiment_score,
        'mentions': json.loads(a.mentions) if a.mentions else []
    } for a in articles]


def get_sentiment_distribution():
    """Get sentiment distribution of recent articles"""
    with db.get_connection() as conn:
        since = datetime.now() - timedelta(hours=24)
        
        rows = conn.execute("""
            SELECT sentiment_score
            FROM articles
            WHERE scraped_at > ? AND sentiment_score IS NOT NULL
        """, (since,)).fetchall()
        
        positive = sum(1 for r in rows if r['sentiment_score'] > 0.2)
        negative = sum(1 for r in rows if r['sentiment_score'] < -0.2)
        neutral = len(rows) - positive - negative
        
        return {
            'positive': positive,
            'negative': negative,
            'neutral': neutral,
            'total': len(rows)
        }


def get_source_distribution():
    """Get articles by source"""
    with db.get_connection() as conn:
        since = datetime.now() - timedelta(hours=24)
        
        rows = conn.execute("""
            SELECT source, COUNT(*) as count
            FROM articles
            WHERE scraped_at > ?
            GROUP BY source
            ORDER BY count DESC
        """, (since,)).fetchall()
        
        return [{'source': r['source'], 'count': r['count']} for r in rows]


# Routes
@app.route('/')
def index():
    """Main dashboard"""
    return render_template('index.html')


@app.route('/health')
def health_check():
    """
    Health check endpoint for monitoring.
    Returns 200 if healthy, 503 if unhealthy.
    Does not require API key authentication.
    """
    components = {}
    is_healthy = True

    # Check database connectivity
    try:
        with db.get_connection() as conn:
            conn.execute("SELECT 1").fetchone()
        components['database'] = 'ok'
    except Exception as e:
        components['database'] = f'error: {str(e)}'
        is_healthy = False

    # Get last scrape time
    last_scrape = get_last_scrape_time()
    components['last_scrape'] = last_scrape

    response_data = {
        'status': 'healthy' if is_healthy else 'unhealthy',
        'components': components,
        'version': APP_VERSION
    }

    status_code = 200 if is_healthy else 503
    return jsonify(response_data), status_code


@app.route('/metrics')
def metrics():
    """
    Prometheus-compatible metrics endpoint.
    Returns metrics in text/plain format.
    Does not require API key authentication.
    """
    metrics_lines = []

    # Helper to add metric with optional help and type
    def add_metric(name, value, help_text=None, metric_type=None):
        if help_text:
            metrics_lines.append(f'# HELP {name} {help_text}')
        if metric_type:
            metrics_lines.append(f'# TYPE {name} {metric_type}')
        metrics_lines.append(f'{name} {value}')

    try:
        with db.get_connection() as conn:
            # Total articles in database
            total_articles = conn.execute(
                "SELECT COUNT(*) as count FROM articles"
            ).fetchone()['count']
            add_metric(
                'news_sentinel_articles_total',
                total_articles,
                'Total number of articles in the database',
                'gauge'
            )

            # Total alerts generated
            total_alerts = conn.execute(
                "SELECT COUNT(*) as count FROM alerts"
            ).fetchone()['count']
            add_metric(
                'news_sentinel_alerts_total',
                total_alerts,
                'Total number of alerts generated',
                'gauge'
            )

            # Number of companies monitored (from config watchlist)
            companies_count = len(config.get('companies', {}).get('watchlist', {}))
            add_metric(
                'news_sentinel_companies_monitored',
                companies_count,
                'Number of companies in the watchlist',
                'gauge'
            )

    except Exception:
        # If database fails, return what we can
        pass

    # Last scrape timestamp (as Unix timestamp)
    last_scrape = get_last_scrape_time()
    if last_scrape:
        try:
            # Parse ISO format and convert to Unix timestamp
            dt = datetime.fromisoformat(last_scrape.replace('Z', '+00:00'))
            unix_timestamp = dt.timestamp()
            add_metric(
                'news_sentinel_scrape_last_timestamp',
                unix_timestamp,
                'Unix timestamp of the last successful scrape',
                'gauge'
            )
        except (ValueError, AttributeError):
            pass

    return Response('\n'.join(metrics_lines) + '\n', mimetype='text/plain')


@app.route('/api/stats')
@require_api_key
def api_stats():
    """Get database stats"""
    return jsonify(get_db_stats())


@app.route('/api/alerts')
@require_api_key
def api_alerts():
    """Get recent alerts"""
    limit = request.args.get('limit', 20, type=int)
    return jsonify(get_recent_alerts(limit))


@app.route('/api/alerts/<int:alert_id>/ack', methods=['POST'])
@require_api_key
def api_ack_alert(alert_id):
    """Acknowledge an alert"""
    with db.get_connection() as conn:
        conn.execute("UPDATE alerts SET acknowledged = TRUE WHERE id = ?", (alert_id,))
        conn.commit()
    return jsonify({'success': True})


@app.route('/api/timeline')
@require_api_key
def api_timeline():
    """Get mention timeline"""
    hours = request.args.get('hours', 24, type=int)
    return jsonify(get_mention_timeline(hours))


@app.route('/api/companies/top')
@require_api_key
def api_top_companies():
    """Get top mentioned companies"""
    limit = request.args.get('limit', 10, type=int)
    return jsonify(get_top_companies(limit))


@app.route('/api/companies/all')
@require_api_key
def api_all_companies():
    """Get all mentioned companies with stats"""
    return jsonify(db.get_mention_counts(hours=168))  # 7 days


@app.route('/api/articles')
@require_api_key
def api_articles():
    """Get recent articles"""
    limit = request.args.get('limit', 50, type=int)
    return jsonify(get_recent_articles(limit))


@app.route('/api/sentiment')
@require_api_key
def api_sentiment():
    """Get sentiment distribution"""
    return jsonify(get_sentiment_distribution())


@app.route('/api/sources')
@require_api_key
def api_sources():
    """Get source distribution"""
    return jsonify(get_source_distribution())


@app.route('/api/config')
@require_api_key
def api_config():
    """Get bot configuration"""
    return jsonify({
        'watchlist': config.get('companies', {}).get('watchlist', {}),
        'sources': {k: v for k, v in config.get('sources', {}).items() if v.get('enabled')},
        'patterns': {
            'volume_spike_threshold': config.get('patterns', {}).get('volume_spike_threshold', 3.0),
            'min_articles': config.get('patterns', {}).get('min_articles_for_alert', 3)
        }
    })


# =============================================================================
# Preferences API Endpoints
# =============================================================================

@app.route('/api/preferences', methods=['GET'])
@require_api_key
def api_get_preferences():
    """Get all user preferences"""
    try:
        preferences = db.get_all_preferences()

        # Merge with defaults from config
        defaults = {
            'alert_channels': {
                'telegram': config.get('alerts', {}).get('telegram', {}).get('enabled', False),
                'webhook': config.get('alerts', {}).get('webhook', {}).get('enabled', False),
                'file': config.get('alerts', {}).get('file', {}).get('enabled', True),
                'console': config.get('alerts', {}).get('console', True)
            },
            'severity_routing': {
                'high': ['telegram', 'webhook', 'file', 'console'],
                'medium': ['file', 'console'],
                'low': ['file']
            },
            'thresholds': {
                'volume_spike': config.get('patterns', {}).get('volume_spike_threshold', 3.0),
                'min_articles': config.get('patterns', {}).get('min_articles_for_alert', 3),
                'sentiment_shift': 0.3
            },
            'company_preferences': {}
        }

        # Merge defaults with stored preferences (stored takes precedence)
        for key, default_value in defaults.items():
            if key not in preferences:
                preferences[key] = default_value

        return jsonify(preferences)
    except Exception as e:
        logger.error("Error getting preferences", extra={"error": str(e)})
        return jsonify({'error': 'Failed to get preferences'}), 500


@app.route('/api/preferences', methods=['POST'])
@require_api_key
def api_save_preferences():
    """Save user preferences"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        # Validate and save each preference
        saved = []
        errors = []

        for key, value in data.items():
            # Validate preference values
            if key == 'thresholds':
                if not isinstance(value, dict):
                    errors.append(f"Invalid thresholds format")
                    continue
                # Validate threshold values
                if 'volume_spike' in value:
                    vol = value['volume_spike']
                    if not isinstance(vol, (int, float)) or vol < 1.0 or vol > 20.0:
                        errors.append("volume_spike must be between 1.0 and 20.0")
                        continue
                if 'min_articles' in value:
                    min_art = value['min_articles']
                    if not isinstance(min_art, int) or min_art < 1 or min_art > 50:
                        errors.append("min_articles must be between 1 and 50")
                        continue
                if 'sentiment_shift' in value:
                    sent = value['sentiment_shift']
                    if not isinstance(sent, (int, float)) or sent < 0.1 or sent > 1.0:
                        errors.append("sentiment_shift must be between 0.1 and 1.0")
                        continue

            if key == 'alert_channels':
                if not isinstance(value, dict):
                    errors.append("Invalid alert_channels format")
                    continue
                valid_channels = ['telegram', 'webhook', 'file', 'console']
                for channel, enabled in value.items():
                    if channel not in valid_channels:
                        errors.append(f"Unknown channel: {channel}")
                        continue
                    if not isinstance(enabled, bool):
                        errors.append(f"Channel {channel} must be boolean")
                        continue

            if key == 'severity_routing':
                if not isinstance(value, dict):
                    errors.append("Invalid severity_routing format")
                    continue
                valid_severities = ['high', 'medium', 'low']
                for severity, channels in value.items():
                    if severity not in valid_severities:
                        errors.append(f"Unknown severity: {severity}")
                        continue
                    if not isinstance(channels, list):
                        errors.append(f"Channels for {severity} must be a list")
                        continue

            # Save the preference
            if db.save_preference(key, value):
                saved.append(key)
            else:
                errors.append(f"Failed to save {key}")

        if errors:
            return jsonify({
                'success': len(saved) > 0,
                'saved': saved,
                'errors': errors
            }), 400 if not saved else 200

        return jsonify({'success': True, 'saved': saved})
    except Exception as e:
        logger.error("Error saving preferences", extra={"error": str(e)})
        return jsonify({'error': 'Failed to save preferences'}), 500


@app.route('/api/watchlist', methods=['GET'])
@require_api_key
def api_get_watchlist():
    """Get the current watchlist"""
    try:
        # Get from database preferences first, fall back to config
        db_watchlist = db.get_preference('watchlist')
        if db_watchlist:
            return jsonify(db_watchlist)
        return jsonify(config.get('companies', {}).get('watchlist', {}))
    except Exception as e:
        logger.error("Error getting watchlist", extra={"error": str(e)})
        return jsonify({'error': 'Failed to get watchlist'}), 500


@app.route('/api/watchlist', methods=['POST'])
@require_api_key
def api_update_watchlist():
    """Update the watchlist (add/remove companies)"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        action = data.get('action')

        if action == 'add':
            ticker = data.get('ticker', '').upper().strip()
            names = data.get('names', [])

            if not ticker:
                return jsonify({'error': 'Ticker is required'}), 400
            if not isinstance(names, list) or not names:
                return jsonify({'error': 'Names must be a non-empty list'}), 400

            # Validate ticker format (1-5 uppercase letters)
            if not ticker.isalpha() or len(ticker) > 5:
                return jsonify({'error': 'Invalid ticker format'}), 400

            # Get current watchlist
            watchlist = db.get_preference('watchlist') or config.get('companies', {}).get('watchlist', {})

            # Add or update company
            watchlist[ticker] = names

            if db.save_preference('watchlist', watchlist):
                return jsonify({'success': True, 'watchlist': watchlist})
            return jsonify({'error': 'Failed to save watchlist'}), 500

        elif action == 'remove':
            ticker = data.get('ticker', '').upper().strip()

            if not ticker:
                return jsonify({'error': 'Ticker is required'}), 400

            # Get current watchlist
            watchlist = db.get_preference('watchlist') or config.get('companies', {}).get('watchlist', {})

            if ticker in watchlist:
                del watchlist[ticker]
                if db.save_preference('watchlist', watchlist):
                    return jsonify({'success': True, 'watchlist': watchlist})
                return jsonify({'error': 'Failed to save watchlist'}), 500

            return jsonify({'error': 'Ticker not found in watchlist'}), 404

        elif action == 'replace':
            # Replace entire watchlist
            watchlist = data.get('watchlist', {})

            if not isinstance(watchlist, dict):
                return jsonify({'error': 'Watchlist must be a dictionary'}), 400

            # Validate all entries
            for ticker, names in watchlist.items():
                if not ticker.isalpha() or len(ticker) > 5:
                    return jsonify({'error': f'Invalid ticker format: {ticker}'}), 400
                if not isinstance(names, list) or not names:
                    return jsonify({'error': f'Names for {ticker} must be a non-empty list'}), 400

            if db.save_preference('watchlist', watchlist):
                return jsonify({'success': True, 'watchlist': watchlist})
            return jsonify({'error': 'Failed to save watchlist'}), 500

        else:
            return jsonify({'error': 'Invalid action. Use: add, remove, or replace'}), 400

    except Exception as e:
        logger.error("Error updating watchlist", extra={"error": str(e)})
        return jsonify({'error': 'Failed to update watchlist'}), 500


@app.route('/api/alert-rules', methods=['GET'])
@require_api_key
def api_get_alert_rules():
    """Get alert routing rules"""
    try:
        # Get channel settings
        alert_channels = db.get_preference('alert_channels') or {
            'telegram': config.get('alerts', {}).get('telegram', {}).get('enabled', False),
            'webhook': config.get('alerts', {}).get('webhook', {}).get('enabled', False),
            'file': config.get('alerts', {}).get('file', {}).get('enabled', True),
            'console': config.get('alerts', {}).get('console', True)
        }

        # Get severity routing
        severity_routing = db.get_preference('severity_routing') or {
            'high': ['telegram', 'webhook', 'file', 'console'],
            'medium': ['file', 'console'],
            'low': ['file']
        }

        # Get company-specific preferences
        company_preferences = db.get_preference('company_preferences') or {}

        return jsonify({
            'alert_channels': alert_channels,
            'severity_routing': severity_routing,
            'company_preferences': company_preferences
        })
    except Exception as e:
        logger.error("Error getting alert rules", extra={"error": str(e)})
        return jsonify({'error': 'Failed to get alert rules'}), 500


@app.route('/api/alert-rules', methods=['POST'])
@require_api_key
def api_update_alert_rules():
    """Update alert routing rules"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        saved = []
        errors = []

        # Update alert channels
        if 'alert_channels' in data:
            channels = data['alert_channels']
            if isinstance(channels, dict):
                valid_channels = ['telegram', 'webhook', 'file', 'console']
                for channel, enabled in channels.items():
                    if channel not in valid_channels:
                        errors.append(f"Unknown channel: {channel}")
                    elif not isinstance(enabled, bool):
                        errors.append(f"Channel {channel} must be boolean")

                if not errors:
                    if db.save_preference('alert_channels', channels):
                        saved.append('alert_channels')
                    else:
                        errors.append('Failed to save alert_channels')
            else:
                errors.append('alert_channels must be a dictionary')

        # Update severity routing
        if 'severity_routing' in data:
            routing = data['severity_routing']
            if isinstance(routing, dict):
                valid_severities = ['high', 'medium', 'low']
                for severity, channels in routing.items():
                    if severity not in valid_severities:
                        errors.append(f"Unknown severity: {severity}")
                    elif not isinstance(channels, list):
                        errors.append(f"Channels for {severity} must be a list")

                if not any('severity' in e for e in errors):
                    if db.save_preference('severity_routing', routing):
                        saved.append('severity_routing')
                    else:
                        errors.append('Failed to save severity_routing')
            else:
                errors.append('severity_routing must be a dictionary')

        # Update company preferences
        if 'company_preferences' in data:
            prefs = data['company_preferences']
            if isinstance(prefs, dict):
                if db.save_preference('company_preferences', prefs):
                    saved.append('company_preferences')
                else:
                    errors.append('Failed to save company_preferences')
            else:
                errors.append('company_preferences must be a dictionary')

        if errors and not saved:
            return jsonify({'success': False, 'errors': errors}), 400

        return jsonify({
            'success': True,
            'saved': saved,
            'errors': errors if errors else None
        })
    except Exception as e:
        logger.error("Error updating alert rules", extra={"error": str(e)})
        return jsonify({'error': 'Failed to update alert rules'}), 500


# =============================================================================
# Market Data and Correlation API Endpoints
# =============================================================================

@app.route('/api/correlation/<ticker>')
@require_api_key
def api_correlation(ticker):
    """
    Get correlation analysis between news alerts and price movements for a ticker.

    Returns:
        - Market context (current price, day/week changes)
        - Correlation statistics (alert hit rate, average price change after alerts)
        - Accuracy metrics by alert type
        - Recent alert impacts

    Query params:
        - days: Number of days to analyze (default 30, max 90)
    """
    if not MARKET_DATA_AVAILABLE or not correlation_analyzer:
        return jsonify({
            'error': 'Market data not available',
            'message': 'Install yfinance and enable market_data in settings.yaml'
        }), 503

    try:
        # Validate ticker
        ticker = ticker.upper().strip()
        if not ticker.isalpha() or len(ticker) > 5:
            return jsonify({'error': 'Invalid ticker format'}), 400

        # Get analysis period
        days = request.args.get('days', 30, type=int)
        days = min(max(days, 1), 90)  # Clamp between 1 and 90

        # Generate correlation report
        report = correlation_analyzer.get_correlation_report(ticker, days)

        return jsonify(report)

    except Exception as e:
        logger.error(
            "Error getting correlation data",
            extra={"ticker": ticker, "error": str(e)}
        )
        return jsonify({'error': 'Failed to get correlation data'}), 500


@app.route('/api/market/<ticker>')
@require_api_key
def api_market_data(ticker):
    """
    Get current market data for a ticker.

    Returns current price, day change, and week change.
    """
    if not MARKET_DATA_AVAILABLE or not market_data_provider:
        return jsonify({
            'error': 'Market data not available',
            'message': 'Install yfinance and enable market_data in settings.yaml'
        }), 503

    try:
        # Validate ticker
        ticker = ticker.upper().strip()
        if not ticker.isalpha() or len(ticker) > 5:
            return jsonify({'error': 'Invalid ticker format'}), 400

        # Get market context
        context = market_data_provider.get_market_context(ticker)

        if not context:
            return jsonify({
                'error': 'No market data available',
                'ticker': ticker
            }), 404

        return jsonify({
            'ticker': ticker,
            **context
        })

    except Exception as e:
        logger.error(
            "Error getting market data",
            extra={"ticker": ticker, "error": str(e)}
        )
        return jsonify({'error': 'Failed to get market data'}), 500


@app.route('/api/market/<ticker>/history')
@require_api_key
def api_market_history(ticker):
    """
    Get historical price data for a ticker.

    Query params:
        - days: Number of days of history (default 30, max 365)
    """
    if not MARKET_DATA_AVAILABLE or not market_data_provider:
        return jsonify({
            'error': 'Market data not available',
            'message': 'Install yfinance and enable market_data in settings.yaml'
        }), 503

    try:
        # Validate ticker
        ticker = ticker.upper().strip()
        if not ticker.isalpha() or len(ticker) > 5:
            return jsonify({'error': 'Invalid ticker format'}), 400

        # Get number of days
        days = request.args.get('days', 30, type=int)
        days = min(max(days, 1), 365)  # Clamp between 1 and 365

        # Get historical prices
        history = market_data_provider.get_historical_prices(ticker, days)

        if not history:
            return jsonify({
                'error': 'No historical data available',
                'ticker': ticker
            }), 404

        return jsonify({
            'ticker': ticker,
            'days': days,
            'prices': history
        })

    except Exception as e:
        logger.error(
            "Error getting market history",
            extra={"ticker": ticker, "error": str(e)}
        )
        return jsonify({'error': 'Failed to get market history'}), 500


@app.route('/api/run', methods=['POST'])
@require_api_key
def api_run_bot():
    """Trigger bot run"""
    import subprocess
    try:
        result = subprocess.run(
            ['python3', str(Path(__file__).parent.parent / 'src' / 'main.py'), 'schedule'],
            capture_output=True,
            text=True,
            timeout=300
        )
        return jsonify({
            'success': result.returncode == 0,
            'output': result.stdout,
            'error': result.stderr
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


# Static files
@app.route('/static/<path:path>')
def send_static(path):
    return send_from_directory('static', path)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
