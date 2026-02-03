"""
Nickberg Terminal - Web Dashboard
Flask application for monitoring and visualization
"""

import os
import sys
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from functools import wraps

from flask import Flask, render_template, jsonify, request, send_from_directory, Response, g

# Prometheus metrics
try:
    from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST, CollectorRegistry
    PROMETHEUS_AVAILABLE = True
    # Use a separate registry to avoid conflicts in tests
    METRICS_REGISTRY = CollectorRegistry(auto_describe=True)
except ImportError:
    PROMETHEUS_AVAILABLE = False
    METRICS_REGISTRY = None

# Try to import CORS, install if not available
try:
    from flask_cors import CORS
    CORS_AVAILABLE = True
except ImportError:
    CORS_AVAILABLE = False

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from database import Database
from alerts import AlertManager
from logging_config import setup_logging, get_logger

# Pydantic validation
try:
    from pydantic import ValidationError
    from models import (
        PreferencesRequest, WatchlistAddRequest, AlertRulesRequest,
        ErrorResponse
    )
    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False

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

import os
template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
app = Flask('nickberg', template_folder=template_dir, static_folder=static_dir)

# =============================================================================
# Prometheus Metrics Setup (using separate registry to avoid test conflicts)
# =============================================================================
REQUEST_COUNT = None
REQUEST_LATENCY = None
ARTICLES_TOTAL = None
ALERTS_TOTAL = None
ALERTS_UNACKNOWLEDGED = None
COMPANIES_MONITORED = None
SCRAPE_LAST_TIMESTAMP = None
ARTICLES_24H = None

if PROMETHEUS_AVAILABLE and METRICS_REGISTRY is not None:
    # Request metrics
    REQUEST_COUNT = Counter(
        'nickberg_http_requests_total',
        'Total HTTP requests',
        ['method', 'endpoint', 'status'],
        registry=METRICS_REGISTRY
    )
    REQUEST_LATENCY = Histogram(
        'nickberg_http_request_duration_seconds',
        'HTTP request latency in seconds',
        ['method', 'endpoint'],
        buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
        registry=METRICS_REGISTRY
    )

    # Business metrics
    ARTICLES_TOTAL = Gauge(
        'nickberg_articles_total',
        'Total number of articles in database',
        registry=METRICS_REGISTRY
    )
    ALERTS_TOTAL = Gauge(
        'nickberg_alerts_total',
        'Total number of alerts generated',
        registry=METRICS_REGISTRY
    )
    ALERTS_UNACKNOWLEDGED = Gauge(
        'nickberg_alerts_unacknowledged',
        'Number of unacknowledged alerts',
        registry=METRICS_REGISTRY
    )
    COMPANIES_MONITORED = Gauge(
        'nickberg_companies_monitored',
        'Number of companies in watchlist',
        registry=METRICS_REGISTRY
    )
    SCRAPE_LAST_TIMESTAMP = Gauge(
        'nickberg_scrape_last_timestamp',
        'Unix timestamp of last successful scrape',
        registry=METRICS_REGISTRY
    )
    ARTICLES_24H = Gauge(
        'nickberg_articles_24h',
        'Articles scraped in last 24 hours',
        registry=METRICS_REGISTRY
    )

# Disable caching for all responses (development)
@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '-1'
    return response

# Enable CORS for all origins in development
if CORS_AVAILABLE:
    CORS(app, resources={
        r"/api/*": {
            "origins": "*",
            "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            "allow_headers": ["Content-Type", "Authorization", "X-API-Key"]
        }
    })
    logger.info("CORS enabled for API endpoints")

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

# Initialize database (support environment variable for cloud deployment)
DB_PATH_ENV = os.environ.get('NEWS_SENTINEL_DB_PATH')
if DB_PATH_ENV:
    DB_PATH = Path(DB_PATH_ENV)
    # Ensure directory exists
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
else:
    DB_PATH = Path(__file__).parent.parent / config['database']['path']
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

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


# Request logging with tracing
import time as _time

@app.before_request
def before_request():
    """Log incoming requests, start timing, and assign trace ID"""
    g.start_time = _time.time()
    # Generate or extract trace ID for request correlation
    g.trace_id = request.headers.get('X-Trace-ID') or str(uuid.uuid4())[:8]


@app.after_request
def after_request(response):
    """Log request completion with timing, status, and trace ID"""
    # Add trace ID to response headers for client correlation
    trace_id = getattr(g, 'trace_id', 'unknown')
    response.headers['X-Trace-ID'] = trace_id

    # Skip logging for static files and health checks to reduce noise
    if request.path.startswith('/static') or request.path == '/health':
        return response

    duration = _time.time() - getattr(g, 'start_time', _time.time())

    # Update Prometheus metrics
    if PROMETHEUS_AVAILABLE and REQUEST_COUNT is not None:
        endpoint = request.endpoint or 'unknown'
        REQUEST_COUNT.labels(
            method=request.method,
            endpoint=endpoint,
            status=response.status_code
        ).inc()
        REQUEST_LATENCY.labels(
            method=request.method,
            endpoint=endpoint
        ).observe(duration)

    logger.info(
        "Request processed",
        extra={
            'trace_id': trace_id,
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
@app.route('/favicon.ico')
def favicon():
    """Serve favicon"""
    return send_from_directory(
        os.path.join(app.root_path, 'static'),
        'favicon.ico',
        mimetype='image/x-icon'
    )


@app.route('/')
def index():
    """Main dashboard - Bloomberg Terminal Theme"""
    return render_template('bloomberg-dashboard.html')


@app.route('/classic')
def classic_dashboard():
    """Original dashboard theme"""
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
    Returns metrics in Prometheus exposition format.
    Does not require API key authentication.
    """
    if not PROMETHEUS_AVAILABLE or METRICS_REGISTRY is None:
        # Fallback to simple text format if prometheus_client not installed
        return Response("# prometheus_client not installed\n", mimetype='text/plain')

    # Update business metrics
    try:
        with db.get_connection() as conn:
            # Total articles
            total_articles = conn.execute(
                "SELECT COUNT(*) as count FROM articles"
            ).fetchone()['count']
            if ARTICLES_TOTAL:
                ARTICLES_TOTAL.set(total_articles)

            # Total alerts
            total_alerts = conn.execute(
                "SELECT COUNT(*) as count FROM alerts"
            ).fetchone()['count']
            if ALERTS_TOTAL:
                ALERTS_TOTAL.set(total_alerts)

            # Unacknowledged alerts
            unack_alerts = conn.execute(
                "SELECT COUNT(*) as count FROM alerts WHERE acknowledged = FALSE"
            ).fetchone()['count']
            if ALERTS_UNACKNOWLEDGED:
                ALERTS_UNACKNOWLEDGED.set(unack_alerts)

            # Articles in last 24h
            articles_24h = conn.execute(
                "SELECT COUNT(*) as count FROM articles WHERE scraped_at > datetime('now', '-1 day')"
            ).fetchone()['count']
            if ARTICLES_24H:
                ARTICLES_24H.set(articles_24h)

    except Exception as e:
        logger.warning(f"Error updating metrics: {e}")

    # Companies monitored
    companies_count = len(config.get('companies', {}).get('watchlist', {}))
    if COMPANIES_MONITORED:
        COMPANIES_MONITORED.set(companies_count)

    # Last scrape timestamp
    last_scrape = get_last_scrape_time()
    if last_scrape:
        try:
            dt = datetime.fromisoformat(last_scrape.replace('Z', '+00:00'))
            if SCRAPE_LAST_TIMESTAMP:
                SCRAPE_LAST_TIMESTAMP.set(dt.timestamp())
        except (ValueError, AttributeError):
            pass

    return Response(generate_latest(METRICS_REGISTRY), mimetype=CONTENT_TYPE_LATEST)


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
    trace_id = getattr(g, 'trace_id', 'unknown')

    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided', 'trace_id': trace_id}), 400

        # Use Pydantic validation if available
        if PYDANTIC_AVAILABLE:
            try:
                validated = PreferencesRequest(**data)
                data = validated.model_dump(exclude_none=True)
            except ValidationError as e:
                # Convert Pydantic errors to JSON-serializable format
                errors = [err['msg'] for err in e.errors()]
                return jsonify({
                    'error': 'Validation failed',
                    'errors': errors,  # backwards compatible
                    'details': [{'field': '.'.join(str(x) for x in err['loc']), 'message': err['msg']} for err in e.errors()],
                    'trace_id': trace_id
                }), 400

        # Save each preference
        saved = []
        errors = []

        for key, value in data.items():
            if db.save_preference(key, value):
                saved.append(key)
            else:
                errors.append(f"Failed to save {key}")

        if errors and not saved:
            return jsonify({
                'success': False,
                'errors': errors,
                'trace_id': trace_id
            }), 400

        return jsonify({'success': True, 'saved': saved, 'trace_id': trace_id})
    except Exception as e:
        logger.error("Error saving preferences", extra={"error": str(e), "trace_id": trace_id})
        return jsonify({'error': 'Failed to save preferences', 'trace_id': trace_id}), 500


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
    trace_id = getattr(g, 'trace_id', 'unknown')

    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided', 'trace_id': trace_id}), 400

        # Use Pydantic validation if available
        if PYDANTIC_AVAILABLE:
            try:
                validated = WatchlistAddRequest(**data)
                action = validated.action
                ticker = validated.ticker
                names = validated.names
                watchlist_data = validated.watchlist
            except ValidationError as e:
                # Convert Pydantic errors to JSON-serializable format
                errors = [{'field': '.'.join(str(x) for x in err['loc']), 'message': err['msg']} for err in e.errors()]
                return jsonify({
                    'error': 'Validation failed',
                    'details': errors,
                    'trace_id': trace_id
                }), 400
        else:
            action = data.get('action')
            ticker = data.get('ticker', '').upper().strip() if data.get('ticker') else None
            names = data.get('names', [])
            watchlist_data = data.get('watchlist', {})

        if action == 'add':
            if not ticker:
                return jsonify({'error': 'Ticker is required', 'trace_id': trace_id}), 400
            if not isinstance(names, list) or not names:
                return jsonify({'error': 'Names must be a non-empty list', 'trace_id': trace_id}), 400

            watchlist = db.get_preference('watchlist') or config.get('companies', {}).get('watchlist', {})
            watchlist[ticker] = names

            if db.save_preference('watchlist', watchlist):
                return jsonify({'success': True, 'watchlist': watchlist, 'trace_id': trace_id})
            return jsonify({'error': 'Failed to save watchlist', 'trace_id': trace_id}), 500

        elif action == 'remove':
            if not ticker:
                return jsonify({'error': 'Ticker is required', 'trace_id': trace_id}), 400

            watchlist = db.get_preference('watchlist') or config.get('companies', {}).get('watchlist', {})

            if ticker in watchlist:
                del watchlist[ticker]
                if db.save_preference('watchlist', watchlist):
                    return jsonify({'success': True, 'watchlist': watchlist, 'trace_id': trace_id})
                return jsonify({'error': 'Failed to save watchlist', 'trace_id': trace_id}), 500

            return jsonify({'error': 'Ticker not found in watchlist', 'trace_id': trace_id}), 404

        elif action == 'replace':
            if not isinstance(watchlist_data, dict):
                return jsonify({'error': 'Watchlist must be a dictionary', 'trace_id': trace_id}), 400

            if db.save_preference('watchlist', watchlist_data):
                return jsonify({'success': True, 'watchlist': watchlist_data, 'trace_id': trace_id})
            return jsonify({'error': 'Failed to save watchlist', 'trace_id': trace_id}), 500

        else:
            return jsonify({'error': 'Invalid action. Use: add, remove, or replace', 'trace_id': trace_id}), 400

    except Exception as e:
        logger.error("Error updating watchlist", extra={"error": str(e), "trace_id": trace_id})
        return jsonify({'error': 'Failed to update watchlist', 'trace_id': trace_id}), 500


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


@app.route('/api/prices')
@require_api_key
def get_prices():
    """Get current stock prices for watchlist companies"""
    import concurrent.futures
    
    tickers = request.args.get('tickers', '').split(',')
    
    # Mock prices for quick fallback
    mock_prices = {
        'AAPL': { 'price': 185.92, 'change_pct': 1.25 },
        'MSFT': { 'price': 420.55, 'change_pct': 0.85 },
        'GOOGL': { 'price': 175.98, 'change_pct': -0.45 },
        'AMZN': { 'price': 178.35, 'change_pct': 1.12 },
        'TSLA': { 'price': 248.50, 'change_pct': -2.30 },
        'NVDA': { 'price': 875.28, 'change_pct': 3.45 },
        'META': { 'price': 505.20, 'change_pct': 0.95 },
        'NFLX': { 'price': 628.75, 'change_pct': -0.85 },
        'AMD': { 'price': 162.45, 'change_pct': 1.85 },
        'CRM': { 'price': 295.30, 'change_pct': -0.35 },
        'SPY': { 'price': 520.50, 'change_pct': 0.65 },
        'QQQ': { 'price': 445.25, 'change_pct': 0.95 },
        'DIA': { 'price': 390.80, 'change_pct': 0.25 },
        'IWM': { 'price': 205.40, 'change_pct': -0.15 },
    }
    
    prices = {}
    
    def get_ticker_price(ticker):
        """Fetch price for a single ticker with timeout"""
        ticker = ticker.strip().upper()
        if not ticker:
            return None
        
        # Try market data provider first
        if market_data_provider:
            try:
                price = market_data_provider.get_price(ticker)
                change = market_data_provider.get_intraday_change(ticker)
                if price:
                    return (ticker, {
                        'price': round(price, 2),
                        'change_pct': round(change, 2) if change else 0,
                        'timestamp': datetime.now().isoformat()
                    })
            except Exception as e:
                logger.debug(f"Market data failed for {ticker}: {e}")
        
        # Fallback to mock data
        if ticker in mock_prices:
            return (ticker, {
                'price': mock_prices[ticker]['price'],
                'change_pct': mock_prices[ticker]['change_pct'],
                'timestamp': datetime.now().isoformat(),
                'source': 'mock'
            })
        
        return None
    
    # Fetch prices with timeout
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        future_to_ticker = {executor.submit(get_ticker_price, t): t for t in tickers if t.strip()}
        
        for future in concurrent.futures.as_completed(future_to_ticker, timeout=3):
            try:
                result = future.result(timeout=1)
                if result:
                    ticker, data = result
                    prices[ticker] = data
            except Exception as e:
                logger.debug(f"Error fetching price: {e}")
    
    return jsonify(prices)


@app.route('/api/stock/<ticker>/details')
@require_api_key
def api_stock_details(ticker):
    """
    Get detailed stock information for a ticker.
    Includes price, change, volume, and recent mentions.
    """
    ticker = ticker.upper().strip()
    
    # Mock detailed data for any ticker
    import random
    base_price = 100 + random.random() * 400
    change_pct = (random.random() - 0.5) * 10
    
    # Try to get real data if available
    real_price = None
    real_change = None
    if market_data_provider:
        try:
            real_price = market_data_provider.get_price(ticker)
            real_change = market_data_provider.get_intraday_change(ticker)
        except Exception as e:
            logger.debug(f"Could not get real data for {ticker}: {e}")
    
    price = real_price if real_price else base_price
    change = real_change if real_change is not None else change_pct
    
    # Generate realistic mock details
    day_high = price * (1 + abs(random.gauss(0, 0.01)))
    day_low = price * (1 - abs(random.gauss(0, 0.01)))
    volume = int(random.uniform(1000000, 50000000))
    avg_volume = int(volume * random.uniform(0.8, 1.2))
    
    # Get mentions from database
    mentions = []
    try:
        recent_articles = db.get_recent_articles(limit=100)
        for article in recent_articles:
            if ticker in str(article.get('title', '')).upper() or ticker in str(article.get('content', '')).upper():
                mentions.append({
                    'title': article.get('title', ''),
                    'source': article.get('source', ''),
                    'published': article.get('published_at', ''),
                    'sentiment': article.get('sentiment', 'neutral')
                })
        mentions = mentions[:5]  # Top 5 mentions
    except Exception as e:
        logger.debug(f"Could not get mentions for {ticker}: {e}")
    
    return jsonify({
        'ticker': ticker,
        'price': round(price, 2),
        'change': round(change, 2),
        'change_amount': round(price * change / 100, 2),
        'day_high': round(day_high, 2),
        'day_low': round(day_low, 2),
        'volume': volume,
        'avg_volume': avg_volume,
        'market_cap': f"${random.uniform(10, 3000):.1f}B",
        'pe_ratio': round(random.uniform(10, 40), 1),
        'mentions': mentions,
        'mentions_count': len(mentions),
        'timestamp': datetime.now().isoformat()
    })


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
