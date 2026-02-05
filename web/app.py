"""
Nickberg Terminal - Web Dashboard
Flask application for monitoring and visualization
"""

import os
import sys
import json
import uuid
import re
from datetime import datetime, timedelta
from pathlib import Path
from functools import wraps

from flask import Flask, render_template, jsonify, request, send_from_directory, Response, g, redirect, make_response

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
API_KEY = os.environ.get('NICKBERG_API_KEY', '')


def require_api_key(f):
    """
    Decorator to require API key authentication for endpoints.

    If NICKBERG_API_KEY env var is not set, allows unauthenticated access
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
DB_PATH_ENV = os.environ.get('NICKBERG_DB_PATH')
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


def is_mobile_device():
    """Detect if request is from mobile device"""
    user_agent = request.headers.get('User-Agent', '').lower()
    mobile_patterns = [
        'android', 'iphone', 'ipad', 'ipod', 'blackberry',
        'windows phone', 'webos', 'opera mini', 'mobile'
    ]
    return any(pattern in user_agent for pattern in mobile_patterns)


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


def search_articles(
    limit=200,
    offset=0,
    sources=None,
    tickers=None,
    search=None,
    from_date=None,
    to_date=None,
    sentiment=None
):
    """
    Search articles with filtering and pagination support.
    
    Args:
        limit: Maximum number of articles to return (max 500)
        offset: Number of articles to skip for pagination
        sources: List of source names to filter by
        tickers: List of ticker symbols to filter by (searches mentions)
        search: Text search query for title/content
        from_date: ISO date string for start date filter
        to_date: ISO date string for end date filter
        sentiment: Filter by sentiment ('positive', 'negative', 'neutral')
    
    Returns:
        Dict with 'articles' list and 'total' count
    """
    # Build dynamic SQL query
    conditions = []
    params = []
    
    # Base query
    base_query = "FROM articles"
    
    # Add ticker filter via mentions join if needed
    if tickers:
        base_query = """FROM articles a 
            INNER JOIN company_mentions cm ON a.id = cm.article_id 
            WHERE cm.company_ticker IN ({})
        """.format(','.join('?' * len(tickers)))
        params.extend(tickers)
    else:
        base_query = "FROM articles WHERE 1=1"
    
    # Source filter
    if sources:
        if tickers:
            conditions.append("a.source IN ({})".format(','.join('?' * len(sources))))
        else:
            conditions.append("source IN ({})".format(','.join('?' * len(sources))))
        params.extend(sources)
    
    # Date range filters
    if from_date:
        try:
            datetime.fromisoformat(from_date.replace('Z', '+00:00'))
            if tickers:
                conditions.append("a.scraped_at >= ?")
            else:
                conditions.append("scraped_at >= ?")
            params.append(from_date)
        except ValueError:
            pass
    
    if to_date:
        try:
            datetime.fromisoformat(to_date.replace('Z', '+00:00'))
            if tickers:
                conditions.append("a.scraped_at <= ?")
            else:
                conditions.append("scraped_at <= ?")
            params.append(to_date)
        except ValueError:
            pass
    
    # Text search in title
    if search:
        search_term = f"%{search}%"
        if tickers:
            conditions.append("(a.title LIKE ? OR a.content LIKE ?)")
        else:
            conditions.append("(title LIKE ? OR content LIKE ?)")
        params.extend([search_term, search_term])
    
    # Sentiment filter
    if sentiment:
        if tickers:
            if sentiment == 'positive':
                conditions.append("a.sentiment_score > 0.2")
            elif sentiment == 'negative':
                conditions.append("a.sentiment_score < -0.2")
            elif sentiment == 'neutral':
                conditions.append("(a.sentiment_score >= -0.2 AND a.sentiment_score <= 0.2)")
        else:
            if sentiment == 'positive':
                conditions.append("sentiment_score > 0.2")
            elif sentiment == 'negative':
                conditions.append("sentiment_score < -0.2")
            elif sentiment == 'neutral':
                conditions.append("(sentiment_score >= -0.2 AND sentiment_score <= 0.2)")
    
    # Combine conditions
    where_clause = ""
    if conditions:
        if tickers:
            where_clause = " AND " + " AND ".join(conditions)
        else:
            where_clause = " AND " + " AND ".join(conditions)
    
    # Build final queries
    if tickers:
        count_query = f"SELECT COUNT(DISTINCT a.id) as count {base_query}{where_clause}"
        data_query = f"""
            SELECT DISTINCT a.* {base_query}{where_clause}
            ORDER BY a.scraped_at DESC
            LIMIT ? OFFSET ?
        """
    else:
        count_query = f"SELECT COUNT(*) as count {base_query}{where_clause}"
        data_query = f"""
            SELECT * {base_query}{where_clause}
            ORDER BY scraped_at DESC
            LIMIT ? OFFSET ?
        """
    
    # Execute queries
    with db.get_connection() as conn:
        # Get total count
        total = conn.execute(count_query, params).fetchone()['count']
        
        # Get articles with pagination
        query_params = params + [limit, offset]
        rows = conn.execute(data_query, query_params).fetchall()
        
        articles = [{
            'id': row['id'],
            'title': row['title'],
            'source': row['source'],
            'url': row['url'],
            'published_at': format_datetime(row['published_at']),
            'scraped_at': format_datetime(row['scraped_at']),
            'sentiment': row['sentiment_score'],
            'mentions': json.loads(row['mentions']) if row['mentions'] else []
        } for row in rows]
        
        return {
            'articles': articles,
            'total': total,
            'limit': limit,
            'offset': offset,
            'has_more': (offset + len(articles)) < total
        }


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
    """Main dashboard - Bloomberg Terminal Theme
    
    Auto-redirects mobile users to mobile dashboard unless ?desktop=1 is set.
    """
    # Check for desktop override
    if request.args.get('desktop') == '1':
        return render_template('bloomberg-dashboard.html')
    
    # Redirect mobile users to mobile dashboard
    if is_mobile_device():
        return redirect('/mobile')
    
    return render_template('bloomberg-dashboard.html')


@app.route('/classic')
def classic_dashboard():
    """Original dashboard theme"""
    return render_template('index.html')


@app.route('/mobile')
def mobile_dashboard():
    """Mobile-optimized dashboard"""
    response = make_response(render_template('mobile.html'))
    # Add caching headers for mobile assets
    response.headers['Cache-Control'] = 'public, max-age=300'
    return response


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
    """
    Get recent articles with optional filtering and pagination.
    
    Query Parameters:
        - limit: Maximum number of articles (default 200, max 500)
        - offset: Number of articles to skip (for pagination)
        - sources: Comma-separated list of source names to filter
        - tickers: Comma-separated list of ticker symbols to filter
        - search: Text search query for title/content
        - from_date: ISO date string for start date (e.g., 2024-01-01)
        - to_date: ISO date string for end date
        - sentiment: Filter by sentiment ('positive', 'negative', 'neutral')
    
    Returns:
        JSON with 'articles' list and pagination metadata
    """
    # Parse parameters
    limit = min(request.args.get('limit', 200, type=int), 500)
    offset = max(request.args.get('offset', 0, type=int), 0)
    
    # Parse comma-separated lists
    sources = None
    if request.args.get('sources'):
        sources = [s.strip() for s in request.args.get('sources').split(',') if s.strip()]
    
    tickers = None
    if request.args.get('tickers'):
        tickers = [t.strip().upper() for t in request.args.get('tickers').split(',') if t.strip()]
    
    search = request.args.get('search', '').strip() or None
    from_date = request.args.get('from_date') or None
    to_date = request.args.get('to_date') or None
    sentiment = request.args.get('sentiment', '').lower() or None
    
    # If no filters provided and no offset, use simple get_recent_articles for backward compatibility
    if not any([sources, tickers, search, from_date, to_date, sentiment, offset > 0]):
        return jsonify(get_recent_articles(limit))
    
    # Use search function for filtered/paginated results
    result = search_articles(
        limit=limit,
        offset=offset,
        sources=sources,
        tickers=tickers,
        search=search,
        from_date=from_date,
        to_date=to_date,
        sentiment=sentiment
    )
    
    return jsonify(result)


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


@app.route('/api/trending-keywords')
@require_api_key
def api_trending_keywords():
    """
    Get trending keywords from recent article titles.

    Query params:
        - hours: Time window in hours (default 24, max 168)
        - limit: Number of keywords to return (default 20, max 50)
    """
    import re
    from collections import Counter

    hours = request.args.get('hours', 24, type=int)
    hours = min(max(hours, 1), 168)  # Clamp between 1 and 168
    limit = request.args.get('limit', 20, type=int)
    limit = min(max(limit, 1), 50)  # Clamp between 1 and 50

    # Common stop words to filter out
    stop_words = {
        'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
        'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were', 'been',
        'be', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
        'could', 'should', 'may', 'might', 'must', 'shall', 'can', 'need',
        'it', 'its', 'this', 'that', 'these', 'those', 'i', 'you', 'he',
        'she', 'we', 'they', 'what', 'which', 'who', 'whom', 'when', 'where',
        'why', 'how', 'all', 'each', 'every', 'both', 'few', 'more', 'most',
        'other', 'some', 'such', 'no', 'nor', 'not', 'only', 'own', 'same',
        'so', 'than', 'too', 'very', 'just', 'about', 'into', 'over', 'after',
        'before', 'between', 'under', 'again', 'further', 'then', 'once',
        'here', 'there', 'out', 'up', 'down', 'off', 'above', 'below',
        'new', 'says', 'said', 'say', 'amid', 'also', 'now', 'get', 'gets',
        'one', 'two', 'first', 'last', 'year', 'years', 'week', 'day', 'days',
        'today', 'after', 'while', 'still', 'back', 'being', 'even', 'well',
        'way', 'our', 'my', 'your', 'his', 'her', 'their', 'any', 'many',
        'much', 'us', 'him', 'them', 'me', 'reuters', 'bloomberg', 'cnbc',
        'report', 'reports', 'news', 'update', 'updates', 'via', 'per', 'like'
    }

    try:
        with db.get_connection() as conn:
            since = datetime.now() - timedelta(hours=hours)

            # Get article titles from the time window
            rows = conn.execute("""
                SELECT title FROM articles
                WHERE scraped_at > ?
            """, (since,)).fetchall()

            # Extract and count words
            word_counts = Counter()
            for row in rows:
                title = row['title'] or ''
                # Extract words (alphanumeric, 3+ chars)
                words = re.findall(r'\b[a-zA-Z]{3,}\b', title.lower())
                # Filter stop words and count
                for word in words:
                    if word not in stop_words:
                        word_counts[word] += 1

            # Get top keywords
            top_keywords = [
                {'keyword': word, 'count': count}
                for word, count in word_counts.most_common(limit)
            ]

            return jsonify({
                'keywords': top_keywords,
                'hours': hours,
                'article_count': len(rows)
            })

    except Exception as e:
        logger.error("Error getting trending keywords", extra={"error": str(e)})
        return jsonify({'error': 'Failed to get trending keywords'}), 500


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


# =============================================================================
# Stock Detail Modal API Endpoints
# =============================================================================

# Simple in-memory cache for stock data
_stock_cache = {}
STOCK_CACHE_TTL = 300  # 5 minutes

def _get_cached_stock_data(ticker):
    """Get cached stock data if still valid"""
    if ticker in _stock_cache:
        cached = _stock_cache[ticker]
        if datetime.now() - cached['timestamp'] < timedelta(seconds=STOCK_CACHE_TTL):
            return cached['data']
    return None

def _set_cached_stock_data(ticker, data):
    """Cache stock data with timestamp"""
    _stock_cache[ticker] = {
        'data': data,
        'timestamp': datetime.now()
    }


def _format_market_cap(cap):
    """Format market cap in billions/trillions"""
    if cap is None:
        return 'N/A'
    if cap >= 1e12:
        return f"{cap / 1e12:.2f}T"
    elif cap >= 1e9:
        return f"{cap / 1e9:.1f}B"
    elif cap >= 1e6:
        return f"{cap / 1e6:.1f}M"
    return f"{cap:.0f}"


def _format_number(num):
    """Format large numbers"""
    if num is None:
        return 'N/A'
    if num >= 1e9:
        return f"{num / 1e9:.2f}B"
    elif num >= 1e6:
        return f"{num / 1e6:.1f}M"
    elif num >= 1e3:
        return f"{num / 1e3:.1f}K"
    return f"{num:.0f}"


@app.route('/api/stock/<ticker>')
@require_api_key
def get_stock_details(ticker):
    """
    Get comprehensive stock details for a ticker.
    Uses yfinance with 5-minute caching.
    """
    ticker = ticker.upper().strip()
    
    # Validate ticker format
    if not ticker.isalpha() or len(ticker) > 5:
        return jsonify({'error': 'Invalid ticker format'}), 400
    
    # Check cache first
    cached = _get_cached_stock_data(ticker)
    if cached:
        return jsonify(cached)
    
    try:
        # Try to get real data from yfinance
        try:
            import yfinance as yf
            stock = yf.Ticker(ticker)
            info = stock.info
            
            # Get current price data
            hist = stock.history(period="2d", interval="1d")
            
            if len(hist) >= 1:
                current_price = hist['Close'].iloc[-1]
                previous_close = hist['Close'].iloc[-2] if len(hist) >= 2 else info.get('previousClose', current_price)
                day_high = hist['High'].iloc[-1]
                day_low = hist['Low'].iloc[-1]
                volume = hist['Volume'].iloc[-1]
            else:
                current_price = info.get('currentPrice', info.get('regularMarketPrice', 0))
                previous_close = info.get('previousClose', 0)
                day_high = info.get('dayHigh', 0)
                day_low = info.get('dayLow', 0)
                volume = info.get('volume', 0)
            
            # Calculate change
            change = current_price - previous_close if previous_close else 0
            change_percent = (change / previous_close * 100) if previous_close else 0
            
            # Format the response
            result = {
                'ticker': ticker,
                'name': info.get('longName', info.get('shortName', ticker)),
                'price': round(current_price, 2),
                'change': round(change, 2),
                'change_percent': round(change_percent, 2),
                'market_cap': _format_market_cap(info.get('marketCap')),
                'volume': int(volume) if volume else 0,
                'avg_volume': int(info.get('averageVolume', volume or 0)),
                'pe_ratio': round(info.get('trailingPE', info.get('forwardPE', 0)), 2) if info.get('trailingPE') or info.get('forwardPE') else 'N/A',
                'eps': round(info.get('trailingEps', 0), 2) if info.get('trailingEps') else 'N/A',
                'dividend_yield': round(info.get('dividendYield', 0) * 100, 2) if info.get('dividendYield') else 0,
                '52_week_high': round(info.get('fiftyTwoWeekHigh', 0), 2) if info.get('fiftyTwoWeekHigh') else 'N/A',
                '52_week_low': round(info.get('fiftyTwoWeekLow', 0), 2) if info.get('fiftyTwoWeekLow') else 'N/A',
                'day_high': round(day_high, 2) if day_high else 'N/A',
                'day_low': round(day_low, 2) if day_low else 'N/A',
                'open': round(info.get('open', current_price), 2),
                'previous_close': round(previous_close, 2),
                'sector': info.get('sector', 'N/A'),
                'industry': info.get('industry', 'N/A'),
                'employees': info.get('fullTimeEmployees', 'N/A'),
                'website': info.get('website', ''),
                'description': info.get('longBusinessSummary', info.get('description', 'No description available')),
                'source': 'yfinance',
                'cached_at': datetime.now().isoformat()
            }
            
        except ImportError:
            # yfinance not available, return mock data
            logger.warning(f"yfinance not available, returning mock data for {ticker}")
            result = _get_mock_stock_data(ticker)
            
        except Exception as e:
            logger.warning(f"Error fetching from yfinance for {ticker}: {e}")
            result = _get_mock_stock_data(ticker)
        
        # Cache the result
        _set_cached_stock_data(ticker, result)
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error getting stock details for {ticker}: {e}")
        return jsonify({'error': 'Failed to get stock details'}), 500


def _get_mock_stock_data(ticker):
    """Generate mock stock data as fallback"""
    import random
    
    base_price = random.uniform(50, 500)
    change_pct = random.uniform(-5, 5)
    change = base_price * change_pct / 100
    
    return {
        'ticker': ticker,
        'name': f'{ticker} Inc.',
        'price': round(base_price, 2),
        'change': round(change, 2),
        'change_percent': round(change_pct, 2),
        'market_cap': f'{random.uniform(1, 3000):.1f}B',
        'volume': int(random.uniform(1e6, 100e6)),
        'avg_volume': int(random.uniform(5e6, 50e6)),
        'pe_ratio': round(random.uniform(10, 40), 1),
        'eps': round(random.uniform(1, 10), 2),
        'dividend_yield': round(random.uniform(0, 4), 2),
        '52_week_high': round(base_price * 1.3, 2),
        '52_week_low': round(base_price * 0.7, 2),
        'day_high': round(base_price * 1.02, 2),
        'day_low': round(base_price * 0.98, 2),
        'open': round(base_price * (1 - change_pct/200), 2),
        'previous_close': round(base_price - change, 2),
        'sector': random.choice(['Technology', 'Healthcare', 'Finance', 'Consumer', 'Energy']),
        'industry': random.choice(['Software', 'Services', 'Manufacturing', 'Retail']),
        'employees': random.randint(1000, 500000),
        'website': f'https://www.{ticker.lower()}.com',
        'description': f'{ticker} Inc. is a leading company in its industry, providing innovative products and services to customers worldwide.',
        'source': 'mock',
        'cached_at': datetime.now().isoformat()
    }


@app.route('/api/stock/<ticker>/chart')
@require_api_key
def get_stock_chart(ticker):
    """
    Get historical price data for charting.
    
    Query params:
        - period: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, max (default: 1mo)
        - interval: 1m, 5m, 15m, 30m, 1h, 1d, 1wk, 1mo (default: 1d)
    """
    ticker = ticker.upper().strip()
    
    # Validate ticker
    if not ticker.isalpha() or len(ticker) > 5:
        return jsonify({'error': 'Invalid ticker format'}), 400
    
    # Get query params
    period = request.args.get('period', '1mo')
    interval = request.args.get('interval', '1d')
    
    # Validate period
    valid_periods = ['1d', '5d', '1mo', '3mo', '6mo', '1y', '2y', '5y', 'max']
    if period not in valid_periods:
        return jsonify({'error': f'Invalid period. Use: {", ".join(valid_periods)}'}), 400
    
    # Validate interval
    valid_intervals = ['1m', '5m', '15m', '30m', '1h', '1d', '1wk', '1mo']
    if interval not in valid_intervals:
        return jsonify({'error': f'Invalid interval. Use: {", ".join(valid_intervals)}'}), 400
    
    try:
        # Try to get real data from yfinance
        try:
            import yfinance as yf
            stock = yf.Ticker(ticker)
            hist = stock.history(period=period, interval=interval)
            
            if hist.empty:
                raise ValueError("No historical data available")
            
            # Format data for Chart.js
            chart_data = []
            for index, row in hist.iterrows():
                chart_data.append({
                    'date': index.strftime('%Y-%m-%d %H:%M') if hasattr(index, 'strftime') else str(index),
                    'open': round(row['Open'], 2),
                    'high': round(row['High'], 2),
                    'low': round(row['Low'], 2),
                    'close': round(row['Close'], 2),
                    'volume': int(row['Volume'])
                })
            
            result = {
                'ticker': ticker,
                'period': period,
                'interval': interval,
                'data': chart_data,
                'source': 'yfinance'
            }
            
        except ImportError:
            logger.warning("yfinance not available, returning mock chart data")
            result = _get_mock_chart_data(ticker, period, interval)
            
        except Exception as e:
            logger.warning(f"Error fetching chart data from yfinance: {e}")
            result = _get_mock_chart_data(ticker, period, interval)
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error getting chart data for {ticker}: {e}")
        return jsonify({'error': 'Failed to get chart data'}), 500


def _get_mock_chart_data(ticker, period, interval):
    """Generate mock chart data"""
    import random
    from datetime import datetime, timedelta
    
    # Determine number of data points based on period and interval
    period_days = {
        '1d': 1, '5d': 5, '1mo': 30, '3mo': 90,
        '6mo': 180, '1y': 365, '2y': 730, '5y': 1825, 'max': 2520
    }
    
    days = period_days.get(period, 30)
    
    # Determine interval in days
    interval_days = {
        '1m': 1/1440, '5m': 5/1440, '15m': 15/1440, '30m': 30/1440,
        '1h': 1/24, '1d': 1, '1wk': 7, '1mo': 30
    }
    
    interval_d = interval_days.get(interval, 1)
    num_points = min(int(days / interval_d), 500)  # Cap at 500 points
    
    base_price = random.uniform(50, 500)
    chart_data = []
    
    end_date = datetime.now()
    
    for i in range(num_points):
        if interval in ['1m', '5m', '15m', '30m', '1h']:
            # Intraday data
            date = end_date - timedelta(hours=num_points - i)
        else:
            # Daily/weekly/monthly data
            date = end_date - timedelta(days=int((num_points - i) * interval_d))
        
        # Random walk
        change = random.gauss(0, base_price * 0.02)
        open_price = base_price + change
        high_price = open_price + abs(random.gauss(0, base_price * 0.01))
        low_price = open_price - abs(random.gauss(0, base_price * 0.01))
        close_price = open_price + random.gauss(0, base_price * 0.01)
        
        chart_data.append({
            'date': date.strftime('%Y-%m-%d %H:%M'),
            'open': round(open_price, 2),
            'high': round(high_price, 2),
            'low': round(low_price, 2),
            'close': round(close_price, 2),
            'volume': int(random.uniform(1e6, 100e6))
        })
        
        base_price = close_price
    
    return {
        'ticker': ticker,
        'period': period,
        'interval': interval,
        'data': chart_data,
        'source': 'mock'
    }


@app.route('/api/stock/<ticker>/news')
@require_api_key
def get_stock_news(ticker):
    """
    Get recent news articles mentioning this ticker.
    Returns last 5 articles from the database.
    """
    ticker = ticker.upper().strip()
    
    try:
        # Get recent articles that mention this ticker
        with db.get_connection() as conn:
            # Search in mentions and title/content
            rows = conn.execute("""
                SELECT DISTINCT 
                    a.id, a.title, a.source, a.url, a.published_at, a.sentiment_score,
                    a.mentions
                FROM articles a
                WHERE (
                    a.mentions LIKE ? 
                    OR UPPER(a.title) LIKE ? 
                    OR UPPER(a.content) LIKE ?
                )
                AND a.published_at > datetime('now', '-7 days')
                ORDER BY a.published_at DESC
                LIMIT 5
            """, (f'%"{ticker}"%', f'%{ticker}%', f'%{ticker}%')).fetchall()
            
            articles = []
            for row in rows:
                # Parse mentions JSON
                mentions = []
                try:
                    mentions = json.loads(row['mentions']) if row['mentions'] else []
                except:
                    mentions = []
                
                articles.append({
                    'id': row['id'],
                    'title': row['title'],
                    'source': row['source'],
                    'url': row['url'],
                    'published_at': row['published_at'],
                    'sentiment_score': row['sentiment_score'],
                    'mentions': mentions
                })
        
        return jsonify({
            'ticker': ticker,
            'articles': articles,
            'count': len(articles)
        })
        
    except Exception as e:
        logger.error(f"Error getting news for {ticker}: {e}")
        return jsonify({'error': 'Failed to get news'}), 500


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


@app.route('/api/search')
@require_api_key
def advanced_search():
    """
    Advanced search across articles, companies, and alerts.
    
    Query params:
        - q: Search query string
        - type: Search type - 'all', 'articles', 'companies', 'alerts' (default: all)
        - date_from: Start date (ISO format)
        - date_to: End date (ISO format)
        - sources: List of source filters
        - sentiment: Sentiment filter - 'positive', 'negative', 'neutral'
        - tickers: List of ticker symbols to filter by
        - min_mentions: Minimum mention count for companies
        - limit: Maximum results per category (default: 50, max: 100)
        - offset: Pagination offset (default: 0)
    
    Returns categorized results with counts and highlighted matches.
    """
    query = request.args.get('q', '').strip()
    search_type = request.args.get('type', 'all')
    
    # Parse filters
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    sources = request.args.getlist('sources')
    sentiment = request.args.get('sentiment')
    tickers = [t.upper().strip() for t in request.args.getlist('tickers') if t.strip()]
    min_mentions = request.args.get('min_mentions', type=int)
    limit = min(request.args.get('limit', 50, type=int), 100)
    offset = request.args.get('offset', 0, type=int)
    
    # Prepare result containers
    results = {
        'query': query,
        'type': search_type,
        'filters': {
            'date_from': date_from,
            'date_to': date_to,
            'sources': sources,
            'sentiment': sentiment,
            'tickers': tickers,
            'min_mentions': min_mentions
        },
        'articles': {'items': [], 'total': 0, 'query': query},
        'companies': {'items': [], 'total': 0, 'query': query},
        'alerts': {'items': [], 'total': 0, 'query': query}
    }
    
    try:
        with db.get_connection() as conn:
            # Search Articles
            if search_type in ('all', 'articles'):
                article_results = search_articles(conn, query, {
                    'date_from': date_from,
                    'date_to': date_to,
                    'sources': sources,
                    'sentiment': sentiment,
                    'tickers': tickers,
                    'limit': limit,
                    'offset': offset
                })
                results['articles'] = article_results
            
            # Search Companies
            if search_type in ('all', 'companies'):
                company_results = search_companies(conn, query, {
                    'min_mentions': min_mentions,
                    'date_from': date_from,
                    'date_to': date_to,
                    'limit': limit,
                    'offset': offset
                })
                results['companies'] = company_results
            
            # Search Alerts
            if search_type in ('all', 'alerts'):
                alert_results = search_alerts(conn, query, {
                    'date_from': date_from,
                    'date_to': date_to,
                    'tickers': tickers,
                    'limit': limit,
                    'offset': offset
                })
                results['alerts'] = alert_results
        
        # Calculate total results
        results['total'] = (
            results['articles']['total'] +
            results['companies']['total'] +
            results['alerts']['total']
        )
        
        return jsonify(results)
        
    except Exception as e:
        logger.error("Search error", extra={"error": str(e), "query": query})
        return jsonify({'error': 'Search failed', 'message': str(e)}), 500


def search_articles(conn, query, filters):
    """Search articles with full-text search and filtering."""
    conditions = []
    params = []
    
    # Text search in title and content
    if query:
        conditions.append("(title LIKE ? OR content LIKE ?)")
        search_pattern = f"%{query}%"
        params.extend([search_pattern, search_pattern])
    
    # Date filters
    if filters.get('date_from'):
        conditions.append("scraped_at >= ?")
        params.append(filters['date_from'])
    if filters.get('date_to'):
        conditions.append("scraped_at <= ?")
        params.append(filters['date_to'])
    
    # Source filter
    if filters.get('sources'):
        placeholders = ','.join('?' * len(filters['sources']))
        conditions.append(f"source IN ({placeholders})")
        params.extend(filters['sources'])
    
    # Sentiment filter
    if filters.get('sentiment'):
        if filters['sentiment'] == 'positive':
            conditions.append("sentiment_score > 0.2")
        elif filters['sentiment'] == 'negative':
            conditions.append("sentiment_score < -0.2")
        elif filters['sentiment'] == 'neutral':
            conditions.append("(sentiment_score >= -0.2 AND sentiment_score <= 0.2)")
    
    # Ticker filter - articles mentioning specific companies
    if filters.get('tickers'):
        ticker_placeholders = ','.join('?' * len(filters['tickers']))
        conditions.append(f"""
            id IN (
                SELECT DISTINCT article_id 
                FROM company_mentions 
                WHERE company_ticker IN ({ticker_placeholders})
            )
        """)
        params.extend(filters['tickers'])
    
    # Build WHERE clause
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    
    # Count total
    count_sql = f"SELECT COUNT(*) as count FROM articles WHERE {where_clause}"
    total = conn.execute(count_sql, params).fetchone()['count']
    
    # Build main query with relevance scoring
    # Title matches get higher relevance than content matches
    if query:
        relevance_sql = f"""
            SELECT *,
                CASE 
                    WHEN title LIKE ? THEN 3
                    WHEN content LIKE ? THEN 1
                    ELSE 0
                END as relevance
            FROM articles
            WHERE {where_clause}
            ORDER BY relevance DESC, scraped_at DESC
            LIMIT ? OFFSET ?
        """
        search_pattern = f"%{query}%"
        query_params = [search_pattern, search_pattern] + params + [filters.get('limit', 50), filters.get('offset', 0)]
    else:
        relevance_sql = f"""
            SELECT *, 0 as relevance
            FROM articles
            WHERE {where_clause}
            ORDER BY scraped_at DESC
            LIMIT ? OFFSET ?
        """
        query_params = params + [filters.get('limit', 50), filters.get('offset', 0)]
    
    rows = conn.execute(relevance_sql, query_params).fetchall()
    
    items = []
    for row in rows:
        item = {
            'id': row['id'],
            'title': row['title'],
            'url': row['url'],
            'source': row['source'],
            'published_at': row['published_at'],
            'scraped_at': row['scraped_at'],
            'sentiment_score': row['sentiment_score'],
            'mentions': json.loads(row['mentions']) if row['mentions'] else [],
            'relevance': row['relevance'],
            'highlight': create_highlight(row['title'], row['content'], query)
        }
        items.append(item)
    
    return {'items': items, 'total': total, 'query': query}


def search_companies(conn, query, filters):
    """Search companies by ticker or name with mention statistics."""
    conditions = []
    params = []
    
    # Text search on ticker or name
    if query:
        conditions.append("(company_ticker LIKE ? OR company_name LIKE ?)")
        search_pattern = f"%{query}%"
        params.extend([search_pattern.upper(), search_pattern])
    
    # Date filters for mentions
    date_conditions = []
    if filters.get('date_from'):
        date_conditions.append("mentioned_at >= ?")
        params.append(filters['date_from'])
    if filters.get('date_to'):
        date_conditions.append("mentioned_at <= ?")
        params.append(filters['date_to'])
    
    date_where = " AND ".join(date_conditions) if date_conditions else "1=1"
    
    # Build main query
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    
    # Get company mention counts with date filtering
    sql = f"""
        SELECT 
            company_ticker,
            company_name,
            COUNT(*) as mention_count,
            COUNT(DISTINCT article_id) as article_count,
            MAX(mentioned_at) as last_mentioned
        FROM company_mentions
        WHERE {date_where}
        GROUP BY company_ticker, company_name
        HAVING {where_clause}
        {f"AND mention_count >= {filters['min_mentions']}" if filters.get('min_mentions') else ""}
        ORDER BY mention_count DESC
        LIMIT ? OFFSET ?
    """
    
    query_params = params + [filters.get('limit', 50), filters.get('offset', 0)]
    rows = conn.execute(sql, query_params).fetchall()
    
    # Get total count
    count_sql = f"""
        SELECT COUNT(DISTINCT company_ticker) as count
        FROM company_mentions
        WHERE {date_where}
        GROUP BY company_ticker
        HAVING {where_clause}
        {f"AND COUNT(*) >= {filters['min_mentions']}" if filters.get('min_mentions') else ""}
    """
    total_rows = conn.execute(count_sql, params[:-2] if params else []).fetchall()
    total = len(total_rows)
    
    items = []
    for row in rows:
        # Get recent articles for this company
        recent_articles = conn.execute("""
            SELECT a.title, a.source, a.scraped_at, a.sentiment_score
            FROM articles a
            JOIN company_mentions cm ON a.id = cm.article_id
            WHERE cm.company_ticker = ?
            ORDER BY a.scraped_at DESC
            LIMIT 3
        """, (row['company_ticker'],)).fetchall()
        
        item = {
            'ticker': row['company_ticker'],
            'name': row['company_name'],
            'mention_count': row['mention_count'],
            'article_count': row['article_count'],
            'last_mentioned': row['last_mentioned'],
            'recent_articles': [
                {
                    'title': ra['title'],
                    'source': ra['source'],
                    'scraped_at': ra['scraped_at'],
                    'sentiment': ra['sentiment_score']
                }
                for ra in recent_articles
            ]
        }
        items.append(item)
    
    return {'items': items, 'total': total, 'query': query}


def search_alerts(conn, query, filters):
    """Search alerts by message content and metadata."""
    conditions = []
    params = []
    
    # Text search on message
    if query:
        conditions.append("message LIKE ?")
        params.append(f"%{query}%")
    
    # Date filters
    if filters.get('date_from'):
        conditions.append("created_at >= ?")
        params.append(filters['date_from'])
    if filters.get('date_to'):
        conditions.append("created_at <= ?")
        params.append(filters['date_to'])
    
    # Ticker filter
    if filters.get('tickers'):
        placeholders = ','.join('?' * len(filters['tickers']))
        conditions.append(f"company_ticker IN ({placeholders})")
        params.extend(filters['tickers'])
    
    # Build WHERE clause
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    
    # Count total
    count_sql = f"SELECT COUNT(*) as count FROM alerts WHERE {where_clause}"
    total = conn.execute(count_sql, params).fetchone()['count']
    
    # Main query
    sql = f"""
        SELECT *
        FROM alerts
        WHERE {where_clause}
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
    """
    query_params = params + [filters.get('limit', 50), filters.get('offset', 0)]
    
    rows = conn.execute(sql, query_params).fetchall()
    
    items = []
    for row in rows:
        item = {
            'id': row['id'],
            'type': row['alert_type'],
            'ticker': row['company_ticker'],
            'company': row['company_name'],
            'severity': row['severity'],
            'message': row['message'],
            'details': json.loads(row['details']) if row['details'] else {},
            'created_at': row['created_at'],
            'acknowledged': row['acknowledged'],
            'highlight': create_highlight(row['message'], None, query) if query else None
        }
        items.append(item)
    
    return {'items': items, 'total': total, 'query': query}


def create_highlight(title, content, query):
    """Create highlighted snippet from text matching query."""
    if not query:
        return {'title': title, 'snippet': content[:200] + '...' if content and len(content) > 200 else content}
    
    # Highlight title
    highlighted_title = title
    if title:
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        highlighted_title = pattern.sub(lambda m: f"<mark>{m.group()}</mark>", title)
    
    # Find snippet around first match in content
    snippet = ""
    if content:
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        match = pattern.search(content)
        if match:
            start = max(0, match.start() - 80)
            end = min(len(content), match.end() + 80)
            snippet = ('...' if start > 0 else '') + content[start:end] + ('...' if end < len(content) else '')
            snippet = pattern.sub(lambda m: f"<mark>{m.group()}</mark>", snippet)
        else:
            snippet = content[:160] + '...' if len(content) > 160 else content
    
    return {'title': highlighted_title, 'snippet': snippet}


@app.route('/api/search/suggestions')
@require_api_key
def search_suggestions():
    """
    Get search suggestions as user types.
    
    Query params:
        - q: Partial query string
        - limit: Maximum suggestions (default: 10)
    
    Returns suggestions from tickers, company names, and sources.
    """
    query = request.args.get('q', '').strip()
    limit = min(request.args.get('limit', 10, type=int), 20)
    
    if len(query) < 2:
        return jsonify({'suggestions': [], 'query': query})
    
    suggestions = []
    seen = set()
    
    try:
        with db.get_connection() as conn:
            search_pattern = f"%{query}%"
            
            # Suggest tickers
            ticker_rows = conn.execute("""
                SELECT DISTINCT company_ticker as value, 'ticker' as type
                FROM company_mentions
                WHERE company_ticker LIKE ?
                LIMIT ?
            """, (search_pattern.upper(), limit)).fetchall()
            
            for row in ticker_rows:
                if row['value'] not in seen:
                    suggestions.append({'value': row['value'], 'type': row['type']})
                    seen.add(row['value'])
            
            # Suggest company names
            name_rows = conn.execute("""
                SELECT DISTINCT company_name as value, 'company' as type
                FROM company_mentions
                WHERE company_name LIKE ?
                LIMIT ?
            """, (search_pattern, limit)).fetchall()
            
            for row in name_rows:
                if row['value'] not in seen:
                    suggestions.append({'value': row['value'], 'type': row['type']})
                    seen.add(row['value'])
            
            # Suggest sources
            source_rows = conn.execute("""
                SELECT DISTINCT source as value, 'source' as type
                FROM articles
                WHERE source LIKE ?
                LIMIT ?
            """, (search_pattern, limit)).fetchall()
            
            for row in source_rows:
                if row['value'] not in seen:
                    suggestions.append({'value': row['value'], 'type': row['type']})
                    seen.add(row['value'])
        
        return jsonify({'suggestions': suggestions[:limit], 'query': query})
        
    except Exception as e:
        logger.error("Suggestions error", extra={"error": str(e)})
        return jsonify({'suggestions': [], 'query': query})


@app.route('/api/sources/all')
@require_api_key
def api_all_sources():
    """Get all unique article sources for filter dropdown."""
    try:
        with db.get_connection() as conn:
            rows = conn.execute("""
                SELECT DISTINCT source, COUNT(*) as count
                FROM articles
                GROUP BY source
                ORDER BY count DESC
            """).fetchall()
            
            return jsonify([
                {'source': row['source'], 'count': row['count']}
                for row in rows
            ])
    except Exception as e:
        logger.error("Error getting sources", extra={"error": str(e)})
        return jsonify([])


@app.route('/api/economic-calendar')
@require_api_key
def api_economic_calendar():
    """
    Get upcoming economic events calendar.
    
    Returns economic events for the next 7 days including:
    - Employment reports (Nonfarm Payrolls, Jobless Claims, etc.)
    - Inflation data (CPI, PPI, PCE)
    - Central bank meetings (FOMC, Fed decisions)
    - Economic indicators (GDP, Retail Sales, ISM, etc.)
    
    Query params:
        - days: Number of days to look ahead (default 7, max 30)
    """
    from datetime import datetime, timedelta
    import random
    
    days = request.args.get('days', 7, type=int)
    days = min(max(days, 1), 30)  # Clamp between 1 and 30
    
    # Major US economic events with typical schedule
    event_templates = [
        # Weekly events
        {'name': 'Initial Jobless Claims', 'country': 'US', 'impact': 'medium', 'type': 'employment', 
         'time': '08:30', 'day': 3},  # Thursday
        
        # Monthly events
        {'name': 'Nonfarm Payrolls', 'country': 'US', 'impact': 'high', 'type': 'employment', 
         'time': '08:30', 'day_of_month': 1},
        {'name': 'Unemployment Rate', 'country': 'US', 'impact': 'high', 'type': 'employment', 
         'time': '08:30', 'day_of_month': 1},
        {'name': 'CPI (MoM)', 'country': 'US', 'impact': 'high', 'type': 'inflation', 
         'time': '08:30', 'day_of_month': 10},
        {'name': 'CPI (YoY)', 'country': 'US', 'impact': 'high', 'type': 'inflation', 
         'time': '08:30', 'day_of_month': 10},
        {'name': 'Core CPI', 'country': 'US', 'impact': 'high', 'type': 'inflation', 
         'time': '08:30', 'day_of_month': 10},
        {'name': 'PPI (MoM)', 'country': 'US', 'impact': 'medium', 'type': 'inflation', 
         'time': '08:30', 'day_of_month': 12},
        {'name': 'Retail Sales', 'country': 'US', 'impact': 'medium', 'type': 'economic', 
         'time': '08:30', 'day_of_month': 15},
        {'name': 'Industrial Production', 'country': 'US', 'impact': 'low', 'type': 'economic', 
         'time': '09:15', 'day_of_month': 15},
        {'name': 'Housing Starts', 'country': 'US', 'impact': 'medium', 'type': 'housing', 
         'time': '08:30', 'day_of_month': 17},
        {'name': 'Building Permits', 'country': 'US', 'impact': 'medium', 'type': 'housing', 
         'time': '08:30', 'day_of_month': 17},
        {'name': 'Fed Interest Rate Decision', 'country': 'US', 'impact': 'high', 'type': 'interest-rate', 
         'time': '14:00', 'day_of_month': 18, 'notes': '8x per year'},
        {'name': 'FOMC Statement', 'country': 'US', 'impact': 'high', 'type': 'interest-rate', 
         'time': '14:00', 'day_of_month': 18, 'notes': '8x per year'},
        {'name': 'GDP (QoQ)', 'country': 'US', 'impact': 'high', 'type': 'gdp', 
         'time': '08:30', 'day_of_month': 25, 'notes': 'Quarterly'},
        {'name': 'Trade Balance', 'country': 'US', 'impact': 'low', 'type': 'economic', 
         'time': '08:30', 'day_of_month': 5},
        {'name': 'Consumer Confidence', 'country': 'US', 'impact': 'medium', 'type': 'sentiment', 
         'time': '10:00', 'day_of_month': 25},
        {'name': 'ISM Manufacturing', 'country': 'US', 'impact': 'medium', 'type': 'economic', 
         'time': '10:00', 'day_of_month': 1},
        {'name': 'ISM Services', 'country': 'US', 'impact': 'medium', 'type': 'economic', 
         'time': '10:00', 'day_of_month': 3},
        {'name': 'PCE Price Index', 'country': 'US', 'impact': 'high', 'type': 'inflation', 
         'time': '08:30', 'day_of_month': 28},
        {'name': 'Core PCE', 'country': 'US', 'impact': 'high', 'type': 'inflation', 
         'time': '08:30', 'day_of_month': 28},
        {'name': 'Durable Goods Orders', 'country': 'US', 'impact': 'medium', 'type': 'economic', 
         'time': '08:30', 'day_of_month': 25},
        {'name': 'New Home Sales', 'country': 'US', 'impact': 'medium', 'type': 'housing', 
         'time': '10:00', 'day_of_month': 23},
        {'name': 'Existing Home Sales', 'country': 'US', 'impact': 'medium', 'type': 'housing', 
         'time': '10:00', 'day_of_month': 20},
        {'name': 'Philadelphia Fed', 'country': 'US', 'impact': 'medium', 'type': 'economic', 
         'time': '08:30', 'day_of_month': 18},
        {'name': 'Empire State Manufacturing', 'country': 'US', 'impact': 'medium', 'type': 'economic', 
         'time': '08:30', 'day_of_month': 15},
        {'name': 'Chicago PMI', 'country': 'US', 'impact': 'medium', 'type': 'economic', 
         'time': '09:45', 'day_of_month': 28},
        {'name': 'Michigan Consumer Sentiment', 'country': 'US', 'impact': 'medium', 'type': 'sentiment', 
         'time': '10:00', 'day_of_month': 10},
    ]
    
    # Mock historical data for events
    mock_history = {
        'Nonfarm Payrolls': {'previous': '256K', 'forecast': '185K'},
        'Unemployment Rate': {'previous': '4.1%', 'forecast': '4.2%'},
        'CPI (MoM)': {'previous': '0.3%', 'forecast': '0.2%'},
        'CPI (YoY)': {'previous': '2.9%', 'forecast': '2.7%'},
        'Core CPI': {'previous': '0.3%', 'forecast': '0.3%'},
        'PPI (MoM)': {'previous': '0.2%', 'forecast': '0.1%'},
        'Retail Sales': {'previous': '0.4%', 'forecast': '0.3%'},
        'Fed Interest Rate Decision': {'previous': '4.50%', 'forecast': '4.50%'},
        'GDP (QoQ)': {'previous': '2.8%', 'forecast': '2.3%'},
        'Initial Jobless Claims': {'previous': '217K', 'forecast': '215K'},
        'Housing Starts': {'previous': '1.36M', 'forecast': '1.35M'},
        'Building Permits': {'previous': '1.48M', 'forecast': '1.46M'},
        'Consumer Confidence': {'previous': '104.1', 'forecast': '105.0'},
        'ISM Manufacturing': {'previous': '49.2', 'forecast': '49.5'},
        'ISM Services': {'previous': '52.7', 'forecast': '52.5'},
        'PCE Price Index': {'previous': '0.2%', 'forecast': '0.2%'},
        'Core PCE': {'previous': '0.1%', 'forecast': '0.2%'},
        'Durable Goods Orders': {'previous': '-0.8%', 'forecast': '0.5%'},
        'New Home Sales': {'previous': '698K', 'forecast': '680K'},
        'Existing Home Sales': {'previous': '4.15M', 'forecast': '4.10M'},
        'Philadelphia Fed': {'previous': '-10.6', 'forecast': '-5.0'},
        'Empire State Manufacturing': {'previous': '-12.4', 'forecast': '-8.0'},
        'Michigan Consumer Sentiment': {'previous': '73.0', 'forecast': '74.0'},
    }
    
    events = []
    now = datetime.now()
    
    # Generate events for the requested period
    for i in range(days + 1):
        date = now + timedelta(days=i)
        day_of_week = date.weekday()  # 0 = Monday, 3 = Thursday, etc.
        day_of_month = date.day
        date_str = date.strftime('%Y-%m-%d')
        
        # Skip weekends
        if day_of_week >= 5:  # Saturday or Sunday
            continue
        
        for template in event_templates:
            should_include = False
            
            # Weekly event (e.g., Jobless Claims on Thursday)
            if 'day' in template and template['day'] == day_of_week:
                should_include = True
            
            # Monthly event with variance
            elif 'day_of_month' in template:
                variance = abs(template['day_of_month'] - day_of_month)
                if variance <= 2:  # Within 2 days of scheduled date
                    should_include = True
            
            if should_include:
                history = mock_history.get(template['name'], {})
                events.append({
                    'id': f"{template['name']}-{date_str}",
                    'name': template['name'],
                    'country': template['country'],
                    'date': date_str,
                    'time': template['time'],
                    'impact': template['impact'],
                    'type': template['type'],
                    'previous': history.get('previous'),
                    'forecast': history.get('forecast'),
                    'actual': None,
                    'notes': template.get('notes')
                })
    
    # Sort by date and time
    events.sort(key=lambda x: (x['date'], x['time']))
    
    return jsonify({
        'events': events[:20],  # Limit to 20 events
        'days_requested': days,
        'generated_at': datetime.now().isoformat()
    })


# Static files
@app.route('/static/<path:path>')
def send_static(path):
    return send_from_directory('static', path)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
