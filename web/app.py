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
from flask_compress import Compress

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
# Enable GZIP Compression for API responses
# =============================================================================
try:
    Compress(app)
    app.config['COMPRESS_MIMETYPES'] = ['application/json', 'text/html', 'text/css', 'text/javascript', 'application/javascript']
    app.config['COMPRESS_LEVEL'] = 6
    app.config['COMPRESS_MIN_SIZE'] = 500
    logger.info("GZIP compression enabled")
except Exception:
    logger.warning("flask-compress not available, responses will not be compressed")

# SocketIO for real-time price updates
try:
    from flask_socketio import SocketIO, emit
    # Use threading mode instead of eventlet to prevent blocking from yfinance calls
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')
    SOCKETIO_AVAILABLE = True
    logger.info("SocketIO initialized successfully")
except ImportError:
    socketio = None
    SOCKETIO_AVAILABLE = False
    logger.warning("flask-socketio not available - WebSocket features disabled")

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

# Smart caching headers based on endpoint type
@app.after_request
def add_header(response):
    """
    Add appropriate cache headers based on endpoint type.
    - Static files: Cache for 1 hour
    - Stock prices: Cache for 60 seconds
    - News/articles: Cache for 5 minutes
    - Other API: No cache
    """
    # Static files can be cached longer
    if request.path.startswith('/static'):
        response.headers['Cache-Control'] = 'public, max-age=3600'  # 1 hour
        return response

    # API endpoints with specific caching
    if request.path.startswith('/api/'):
        # Stock price endpoints - short cache
        if '/prices' in request.path or '/market/' in request.path:
            response.headers['Cache-Control'] = 'public, max-age=60'  # 1 minute
            response.headers['X-Cache-TTL'] = '60'
        # News and article endpoints - medium cache
        elif '/articles' in request.path or '/news' in request.path:
            response.headers['Cache-Control'] = 'public, max-age=300'  # 5 minutes
            response.headers['X-Cache-TTL'] = '300'
        # Stock details - medium cache
        elif '/stock/' in request.path:
            response.headers['Cache-Control'] = 'public, max-age=300'  # 5 minutes
            response.headers['X-Cache-TTL'] = '300'
        else:
            # Default: no cache for dynamic API endpoints
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            response.headers['Pragma'] = 'no-cache'
    else:
        # HTML pages - no cache
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
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


def interleave_by_source(articles):
    """
    Interleave articles by source to avoid clustering.
    Instead of showing 5 Motley Fool, then 10 Bloomberg, etc.,
    this distributes articles so different sources appear alternately.
    """
    if not articles:
        return articles

    from collections import defaultdict

    # Group articles by source, maintaining order within each source
    by_source = defaultdict(list)
    for article in articles:
        by_source[article.get('source', 'Unknown')].append(article)

    # Get list of sources sorted by how many articles they have (descending)
    sources = sorted(by_source.keys(), key=lambda s: len(by_source[s]), reverse=True)

    # Interleave: round-robin through sources
    result = []
    source_indices = {s: 0 for s in sources}

    while len(result) < len(articles):
        added_this_round = False
        for source in sources:
            idx = source_indices[source]
            if idx < len(by_source[source]):
                result.append(by_source[source][idx])
                source_indices[source] += 1
                added_this_round = True

        # Safety check to prevent infinite loop
        if not added_this_round:
            break

    return result


def get_recent_articles(limit=50):
    """Get recent articles, interleaved by source"""
    articles = db.get_recent_articles(limit)
    formatted = [{
        'id': a.id,
        'title': a.title,
        'source': a.source,
        'url': a.url,
        'published_at': format_datetime(a.published_at),
        'scraped_at': format_datetime(a.scraped_at),
        'sentiment': a.sentiment_score,
        'mentions': json.loads(a.mentions) if a.mentions else []
    } for a in articles]
    return interleave_by_source(formatted)


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

        # Interleave articles by source for variety
        interleaved = interleave_by_source(articles)

        return {
            'articles': interleaved,
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


@app.route('/api/cache/stats')
def cache_stats():
    """
    Get cache statistics for monitoring.
    Does not require API key authentication.
    """
    return jsonify(api_cache.get_stats())


@app.route('/api/cache/clear', methods=['POST'])
@require_api_key
def cache_clear():
    """
    Clear the API cache.
    Optionally specify category to clear only that type.
    """
    category = request.args.get('category')
    if category:
        api_cache.clear_category(category)
        return jsonify({'success': True, 'cleared': category})
    else:
        api_cache.clear_all()
        return jsonify({'success': True, 'cleared': 'all'})


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
    Uses TTL cache (5 minutes) for unfiltered requests.

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

    # If no filters provided and no offset, use cache for recent articles
    if not any([sources, tickers, search, from_date, to_date, sentiment, offset > 0]):
        cache_key = f'articles:recent:{limit}'
        cached = api_cache.get(cache_key, 'articles')
        if cached:
            return jsonify(cached)

        result = get_recent_articles(limit)
        api_cache.set(cache_key, result, 'articles')
        return jsonify(result)

    # Use search function for filtered/paginated results
    # Generate cache key for filtered requests
    cache_key = f'articles:search:{limit}:{offset}:{sources}:{tickers}:{search}:{sentiment}'
    cached = api_cache.get(cache_key, 'articles')
    if cached:
        return jsonify(cached)

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

    api_cache.set(cache_key, result, 'articles')
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
    """
    Get current stock prices for watchlist companies.
    Uses TTL cache (60 seconds) to avoid hammering external APIs.
    """
    import concurrent.futures

    tickers = request.args.get('tickers', '').split(',')
    tickers = [t.strip().upper() for t in tickers if t.strip()]

    if not tickers:
        return jsonify({})

    # Check cache for each ticker
    prices = {}
    tickers_to_fetch = []

    for ticker in tickers:
        cached = api_cache.get(f'price:{ticker}', 'stock')
        if cached:
            prices[ticker] = cached
        else:
            tickers_to_fetch.append(ticker)

    # If all tickers were cached, return immediately
    if not tickers_to_fetch:
        return jsonify(prices)

    # Mock prices for quick fallback
    mock_prices = {
        'AAPL': {'price': 185.92, 'change_pct': 1.25},
        'MSFT': {'price': 420.55, 'change_pct': 0.85},
        'GOOGL': {'price': 175.98, 'change_pct': -0.45},
        'AMZN': {'price': 178.35, 'change_pct': 1.12},
        'TSLA': {'price': 248.50, 'change_pct': -2.30},
        'NVDA': {'price': 875.28, 'change_pct': 3.45},
        'META': {'price': 505.20, 'change_pct': 0.95},
        'NFLX': {'price': 628.75, 'change_pct': -0.85},
        'AMD': {'price': 162.45, 'change_pct': 1.85},
        'CRM': {'price': 295.30, 'change_pct': -0.35},
        'SPY': {'price': 520.50, 'change_pct': 0.65},
        'QQQ': {'price': 445.25, 'change_pct': 0.95},
        'DIA': {'price': 390.80, 'change_pct': 0.25},
        'IWM': {'price': 205.40, 'change_pct': -0.15},
    }

    def get_ticker_price(ticker):
        """Fetch price for a single ticker with timeout"""
        # Try market data provider first
        if market_data_provider:
            try:
                price = market_data_provider.get_price(ticker)
                change = market_data_provider.get_intraday_change(ticker)
                if price:
                    data = {
                        'price': round(price, 2),
                        'change_pct': round(change, 2) if change else 0,
                        'timestamp': datetime.now().isoformat()
                    }
                    # Cache the result
                    api_cache.set(f'price:{ticker}', data, 'stock')
                    return (ticker, data)
            except Exception as e:
                logger.debug(f"Market data failed for {ticker}: {e}")

        # Fallback to mock data
        if ticker in mock_prices:
            data = {
                'price': mock_prices[ticker]['price'],
                'change_pct': mock_prices[ticker]['change_pct'],
                'timestamp': datetime.now().isoformat(),
                'source': 'mock'
            }
            # Cache mock data too (shorter TTL handled by cache)
            api_cache.set(f'price:{ticker}', data, 'stock')
            return (ticker, data)

        return None

    # Fetch uncached prices with timeout
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        future_to_ticker = {executor.submit(get_ticker_price, t): t for t in tickers_to_fetch}

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

# =============================================================================
# In-Memory Cache System with TTL (Redis-like)
# =============================================================================

class TTLCache:
    """
    Thread-safe in-memory cache with TTL support.
    Supports different TTLs for different data types:
    - Stock prices: 60 seconds
    - News articles: 300 seconds (5 minutes)
    - Stock details: 300 seconds
    - Chart data: 60 seconds
    """

    def __init__(self):
        self._cache = {}
        self._lock = __import__('threading').Lock()
        self._default_ttls = {
            'stock': 60,      # Stock prices - 1 minute
            'news': 300,      # News - 5 minutes
            'details': 300,   # Stock details - 5 minutes
            'chart': 60,      # Chart data - 1 minute
            'articles': 300,  # Article lists - 5 minutes
            'default': 120    # Default - 2 minutes
        }

    def get(self, key, category='default'):
        """Get value from cache if not expired"""
        with self._lock:
            if key in self._cache:
                entry = self._cache[key]
                ttl = self._default_ttls.get(category, self._default_ttls['default'])
                if datetime.now() - entry['timestamp'] < timedelta(seconds=ttl):
                    logger.debug(f"Cache HIT: {key}")
                    return entry['data']
                else:
                    # Expired, remove it
                    del self._cache[key]
                    logger.debug(f"Cache EXPIRED: {key}")
            return None

    def set(self, key, data, category='default'):
        """Store value in cache with timestamp"""
        with self._lock:
            self._cache[key] = {
                'data': data,
                'timestamp': datetime.now(),
                'category': category
            }
            logger.debug(f"Cache SET: {key}")

    def delete(self, key):
        """Delete a specific key from cache"""
        with self._lock:
            if key in self._cache:
                del self._cache[key]

    def clear_category(self, category):
        """Clear all entries of a specific category"""
        with self._lock:
            keys_to_delete = [
                k for k, v in self._cache.items()
                if v.get('category') == category
            ]
            for key in keys_to_delete:
                del self._cache[key]

    def clear_all(self):
        """Clear entire cache"""
        with self._lock:
            self._cache.clear()

    def get_stats(self):
        """Get cache statistics"""
        with self._lock:
            now = datetime.now()
            stats = {
                'total_entries': len(self._cache),
                'by_category': {}
            }
            for key, entry in self._cache.items():
                cat = entry.get('category', 'default')
                if cat not in stats['by_category']:
                    stats['by_category'][cat] = 0
                stats['by_category'][cat] += 1
            return stats

# Global cache instance
api_cache = TTLCache()

# Legacy cache variables for backwards compatibility
_stock_cache = {}
STOCK_CACHE_TTL = 300  # 5 minutes

def _get_cached_stock_data(ticker):
    """Get cached stock data if still valid (legacy wrapper)"""
    return api_cache.get(f'stock_details:{ticker}', 'details')

def _set_cached_stock_data(ticker, data):
    """Cache stock data with timestamp (legacy wrapper)"""
    api_cache.set(f'stock_details:{ticker}', data, 'details')


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
            
            # Format the response with comprehensive Bloomberg-style data
            result = {
                # Basic Info
                'ticker': ticker,
                'name': info.get('longName', info.get('shortName', ticker)),
                'price': round(current_price, 2),
                'change': round(change, 2),
                'change_percent': round(change_percent, 2),
                'currency': info.get('currency', 'USD'),
                'exchange': info.get('exchange', 'N/A'),

                # Market Data
                'market_cap': _format_market_cap(info.get('marketCap')),
                'market_cap_raw': info.get('marketCap', 0),
                'enterprise_value': _format_market_cap(info.get('enterpriseValue')),
                'volume': int(volume) if volume else 0,
                'avg_volume': int(info.get('averageVolume', volume or 0)),
                'avg_volume_10d': int(info.get('averageVolume10days', 0)) if info.get('averageVolume10days') else 'N/A',
                'bid': info.get('bid', 'N/A'),
                'ask': info.get('ask', 'N/A'),
                'bid_size': info.get('bidSize', 'N/A'),
                'ask_size': info.get('askSize', 'N/A'),

                # Price Levels
                '52_week_high': round(info.get('fiftyTwoWeekHigh', 0), 2) if info.get('fiftyTwoWeekHigh') else 'N/A',
                '52_week_low': round(info.get('fiftyTwoWeekLow', 0), 2) if info.get('fiftyTwoWeekLow') else 'N/A',
                'day_high': round(day_high, 2) if day_high else 'N/A',
                'day_low': round(day_low, 2) if day_low else 'N/A',
                'open': round(info.get('open', current_price), 2),
                'previous_close': round(previous_close, 2),

                # Technical Levels
                'fifty_day_avg': round(info.get('fiftyDayAverage', 0), 2) if info.get('fiftyDayAverage') else 'N/A',
                'two_hundred_day_avg': round(info.get('twoHundredDayAverage', 0), 2) if info.get('twoHundredDayAverage') else 'N/A',
                'fifty_two_week_change': round(info.get('52WeekChange', 0) * 100, 2) if info.get('52WeekChange') else 'N/A',

                # Valuation Metrics
                'pe_ratio': round(info.get('trailingPE', 0), 2) if info.get('trailingPE') else 'N/A',
                'forward_pe': round(info.get('forwardPE', 0), 2) if info.get('forwardPE') else 'N/A',
                'peg_ratio': round(info.get('pegRatio', 0), 2) if info.get('pegRatio') else 'N/A',
                'price_to_book': round(info.get('priceToBook', 0), 2) if info.get('priceToBook') else 'N/A',
                'price_to_sales': round(info.get('priceToSalesTrailing12Months', 0), 2) if info.get('priceToSalesTrailing12Months') else 'N/A',
                'ev_to_revenue': round(info.get('enterpriseToRevenue', 0), 2) if info.get('enterpriseToRevenue') else 'N/A',
                'ev_to_ebitda': round(info.get('enterpriseToEbitda', 0), 2) if info.get('enterpriseToEbitda') else 'N/A',

                # Earnings & EPS
                'eps': round(info.get('trailingEps', 0), 2) if info.get('trailingEps') else 'N/A',
                'forward_eps': round(info.get('forwardEps', 0), 2) if info.get('forwardEps') else 'N/A',
                'earnings_growth': round(info.get('earningsGrowth', 0) * 100, 2) if info.get('earningsGrowth') else 'N/A',
                'earnings_quarterly_growth': round(info.get('earningsQuarterlyGrowth', 0) * 100, 2) if info.get('earningsQuarterlyGrowth') else 'N/A',
                'revenue_growth': round(info.get('revenueGrowth', 0) * 100, 2) if info.get('revenueGrowth') else 'N/A',

                # Dividends
                'dividend_yield': round(info.get('dividendYield', 0) * 100, 2) if info.get('dividendYield') else 0,
                'dividend_rate': round(info.get('dividendRate', 0), 2) if info.get('dividendRate') else 'N/A',
                'payout_ratio': round(info.get('payoutRatio', 0) * 100, 2) if info.get('payoutRatio') else 'N/A',
                'ex_dividend_date': datetime.fromtimestamp(info.get('exDividendDate')).strftime('%Y-%m-%d') if info.get('exDividendDate') else 'N/A',
                'five_year_avg_dividend_yield': round(info.get('fiveYearAvgDividendYield', 0), 2) if info.get('fiveYearAvgDividendYield') else 'N/A',

                # Profitability & Margins
                'profit_margin': round(info.get('profitMargins', 0) * 100, 2) if info.get('profitMargins') else 'N/A',
                'operating_margin': round(info.get('operatingMargins', 0) * 100, 2) if info.get('operatingMargins') else 'N/A',
                'gross_margin': round(info.get('grossMargins', 0) * 100, 2) if info.get('grossMargins') else 'N/A',
                'ebitda_margin': round(info.get('ebitdaMargins', 0) * 100, 2) if info.get('ebitdaMargins') else 'N/A',

                # Financial Health
                'beta': round(info.get('beta', 0), 2) if info.get('beta') else 'N/A',
                'debt_to_equity': round(info.get('debtToEquity', 0), 2) if info.get('debtToEquity') else 'N/A',
                'current_ratio': round(info.get('currentRatio', 0), 2) if info.get('currentRatio') else 'N/A',
                'quick_ratio': round(info.get('quickRatio', 0), 2) if info.get('quickRatio') else 'N/A',
                'roe': round(info.get('returnOnEquity', 0) * 100, 2) if info.get('returnOnEquity') else 'N/A',
                'roa': round(info.get('returnOnAssets', 0) * 100, 2) if info.get('returnOnAssets') else 'N/A',

                # Cash & Debt
                'total_cash': _format_market_cap(info.get('totalCash')),
                'total_cash_per_share': round(info.get('totalCashPerShare', 0), 2) if info.get('totalCashPerShare') else 'N/A',
                'total_debt': _format_market_cap(info.get('totalDebt')),
                'free_cash_flow': _format_market_cap(info.get('freeCashflow')) if info.get('freeCashflow') else 'N/A',
                'operating_cash_flow': _format_market_cap(info.get('operatingCashflow')) if info.get('operatingCashflow') else 'N/A',

                # Revenue & Income
                'revenue': _format_market_cap(info.get('totalRevenue')),
                'revenue_per_share': round(info.get('revenuePerShare', 0), 2) if info.get('revenuePerShare') else 'N/A',
                'gross_profit': _format_market_cap(info.get('grossProfits')),
                'ebitda': _format_market_cap(info.get('ebitda')),
                'net_income': _format_market_cap(info.get('netIncomeToCommon')),
                'book_value': round(info.get('bookValue', 0), 2) if info.get('bookValue') else 'N/A',

                # Short Interest
                'short_ratio': round(info.get('shortRatio', 0), 2) if info.get('shortRatio') else 'N/A',
                'short_percent_of_float': round(info.get('shortPercentOfFloat', 0) * 100, 2) if info.get('shortPercentOfFloat') else 'N/A',
                'shares_short': info.get('sharesShort', 'N/A'),
                'shares_short_prior': info.get('sharesShortPriorMonth', 'N/A'),

                # Ownership
                'insider_ownership': round(info.get('heldPercentInsiders', 0) * 100, 2) if info.get('heldPercentInsiders') else 'N/A',
                'institutional_ownership': round(info.get('heldPercentInstitutions', 0) * 100, 2) if info.get('heldPercentInstitutions') else 'N/A',
                'float_shares': info.get('floatShares', 'N/A'),
                'shares_outstanding': info.get('sharesOutstanding', 'N/A'),

                # Analyst Data
                'target_price': round(info.get('targetMeanPrice', 0), 2) if info.get('targetMeanPrice') else 'N/A',
                'target_high': round(info.get('targetHighPrice', 0), 2) if info.get('targetHighPrice') else 'N/A',
                'target_low': round(info.get('targetLowPrice', 0), 2) if info.get('targetLowPrice') else 'N/A',
                'recommendation': info.get('recommendationKey', 'N/A'),
                'num_analysts': info.get('numberOfAnalystOpinions', 'N/A'),

                # Company Info
                'sector': info.get('sector', 'N/A'),
                'industry': info.get('industry', 'N/A'),
                'employees': info.get('fullTimeEmployees', 'N/A'),
                'website': info.get('website', ''),
                'headquarters': f"{info.get('city', '')}, {info.get('state', '')} {info.get('country', '')}".strip(', '),
                'description': info.get('longBusinessSummary', info.get('description', 'No description available')),

                'source': 'yfinance',
                'cached_at': datetime.now().isoformat()
            }

            # Try to get earnings dates (yfinance now returns a dict instead of DataFrame)
            try:
                calendar = stock.calendar
                if calendar is not None:
                    # New format: calendar is a dict with 'Earnings Date' as a list
                    if isinstance(calendar, dict):
                        earnings_dates = calendar.get('Earnings Date')
                        if earnings_dates and len(earnings_dates) > 0:
                            result['earnings_date'] = str(earnings_dates[0])[:10]
                        else:
                            result['earnings_date'] = 'N/A'
                    # Old format: calendar is a DataFrame
                    elif hasattr(calendar, 'empty') and not calendar.empty:
                        if 'Earnings Date' in calendar.index:
                            earnings_dates = calendar.loc['Earnings Date']
                            if hasattr(earnings_dates, 'iloc') and len(earnings_dates) > 0:
                                result['earnings_date'] = str(earnings_dates.iloc[0])[:10]
                            else:
                                result['earnings_date'] = str(earnings_dates)[:10] if earnings_dates else 'N/A'
                        else:
                            result['earnings_date'] = 'N/A'
                    else:
                        result['earnings_date'] = 'N/A'
                else:
                    result['earnings_date'] = 'N/A'
            except Exception:
                result['earnings_date'] = 'N/A'

            # Try to get top institutional holders
            try:
                holders = stock.institutional_holders
                if holders is not None and not holders.empty:
                    top_holders = holders.head(5).to_dict('records')
                    result['top_holders'] = [
                        {
                            'name': h.get('Holder', 'Unknown'),
                            'shares': h.get('Shares', 0),
                            'value': h.get('Value', 0),
                            'pct_out': round(h.get('% Out', 0) * 100, 2) if h.get('% Out') else 0
                        }
                        for h in top_holders
                    ]
                else:
                    result['top_holders'] = []
            except Exception:
                result['top_holders'] = []
            
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
    """Generate comprehensive mock stock data as fallback"""
    import random

    base_price = random.uniform(50, 500)
    change_pct = random.uniform(-5, 5)
    change = base_price * change_pct / 100
    market_cap = random.uniform(1e9, 3e12)

    return {
        # Basic Info
        'ticker': ticker,
        'name': f'{ticker} Inc.',
        'price': round(base_price, 2),
        'change': round(change, 2),
        'change_percent': round(change_pct, 2),
        'currency': 'USD',
        'exchange': 'NASDAQ',

        # Market Data
        'market_cap': _format_market_cap(market_cap),
        'market_cap_raw': market_cap,
        'enterprise_value': _format_market_cap(market_cap * 1.1),
        'volume': int(random.uniform(1e6, 100e6)),
        'avg_volume': int(random.uniform(5e6, 50e6)),
        'avg_volume_10d': int(random.uniform(5e6, 50e6)),
        'bid': round(base_price - 0.01, 2),
        'ask': round(base_price + 0.01, 2),
        'bid_size': random.randint(100, 1000),
        'ask_size': random.randint(100, 1000),

        # Price Levels
        '52_week_high': round(base_price * 1.3, 2),
        '52_week_low': round(base_price * 0.7, 2),
        'day_high': round(base_price * 1.02, 2),
        'day_low': round(base_price * 0.98, 2),
        'open': round(base_price * (1 - change_pct/200), 2),
        'previous_close': round(base_price - change, 2),

        # Technical Levels
        'fifty_day_avg': round(base_price * random.uniform(0.95, 1.05), 2),
        'two_hundred_day_avg': round(base_price * random.uniform(0.9, 1.1), 2),
        'fifty_two_week_change': round(random.uniform(-20, 40), 2),

        # Valuation Metrics
        'pe_ratio': round(random.uniform(10, 40), 1),
        'forward_pe': round(random.uniform(8, 35), 1),
        'peg_ratio': round(random.uniform(0.5, 3), 2),
        'price_to_book': round(random.uniform(1, 15), 2),
        'price_to_sales': round(random.uniform(1, 10), 2),
        'ev_to_revenue': round(random.uniform(2, 15), 2),
        'ev_to_ebitda': round(random.uniform(5, 25), 2),

        # Earnings & EPS
        'eps': round(random.uniform(1, 10), 2),
        'forward_eps': round(random.uniform(1, 12), 2),
        'earnings_growth': round(random.uniform(-10, 30), 2),
        'earnings_quarterly_growth': round(random.uniform(-15, 40), 2),
        'revenue_growth': round(random.uniform(-5, 25), 2),

        # Dividends
        'dividend_yield': round(random.uniform(0, 4), 2),
        'dividend_rate': round(random.uniform(0, 5), 2),
        'payout_ratio': round(random.uniform(0, 60), 2),
        'ex_dividend_date': 'N/A',
        'five_year_avg_dividend_yield': round(random.uniform(0, 3), 2),

        # Profitability & Margins
        'profit_margin': round(random.uniform(5, 25), 2),
        'operating_margin': round(random.uniform(10, 35), 2),
        'gross_margin': round(random.uniform(30, 70), 2),
        'ebitda_margin': round(random.uniform(15, 40), 2),

        # Financial Health
        'beta': round(random.uniform(0.5, 2.0), 2),
        'debt_to_equity': round(random.uniform(0, 200), 2),
        'current_ratio': round(random.uniform(1, 4), 2),
        'quick_ratio': round(random.uniform(0.5, 3), 2),
        'roe': round(random.uniform(5, 30), 2),
        'roa': round(random.uniform(2, 15), 2),

        # Cash & Debt
        'total_cash': f'{random.uniform(1, 100):.1f}B',
        'total_cash_per_share': round(random.uniform(1, 20), 2),
        'total_debt': f'{random.uniform(1, 80):.1f}B',
        'free_cash_flow': f'{random.uniform(1, 50):.1f}B',
        'operating_cash_flow': f'{random.uniform(2, 60):.1f}B',

        # Revenue & Income
        'revenue': f'{random.uniform(10, 500):.1f}B',
        'revenue_per_share': round(random.uniform(10, 100), 2),
        'gross_profit': f'{random.uniform(5, 200):.1f}B',
        'ebitda': f'{random.uniform(2, 100):.1f}B',
        'net_income': f'{random.uniform(1, 80):.1f}B',
        'book_value': round(random.uniform(10, 100), 2),

        # Short Interest
        'short_ratio': round(random.uniform(1, 10), 2),
        'short_percent_of_float': round(random.uniform(1, 20), 2),
        'shares_short': int(random.uniform(1e6, 50e6)),
        'shares_short_prior': int(random.uniform(1e6, 50e6)),

        # Ownership
        'insider_ownership': round(random.uniform(1, 15), 2),
        'institutional_ownership': round(random.uniform(50, 95), 2),
        'float_shares': int(random.uniform(100e6, 5e9)),
        'shares_outstanding': int(random.uniform(100e6, 6e9)),

        # Analyst Data
        'target_price': round(base_price * random.uniform(0.9, 1.3), 2),
        'target_high': round(base_price * random.uniform(1.2, 1.5), 2),
        'target_low': round(base_price * random.uniform(0.7, 0.9), 2),
        'recommendation': random.choice(['buy', 'hold', 'sell', 'strong_buy']),
        'num_analysts': random.randint(5, 40),
        'earnings_date': 'N/A',

        # Company Info
        'sector': random.choice(['Technology', 'Healthcare', 'Finance', 'Consumer', 'Energy']),
        'industry': random.choice(['Software', 'Services', 'Manufacturing', 'Retail']),
        'employees': random.randint(1000, 500000),
        'website': f'https://www.{ticker.lower()}.com',
        'headquarters': 'San Francisco, CA USA',
        'description': f'{ticker} Inc. is a leading company in its industry, providing innovative products and services to customers worldwide.',

        'top_holders': [],
        'source': 'mock',
        'cached_at': datetime.now().isoformat()
    }


def _calculate_rsi(closes, period=14):
    """Calculate Relative Strength Index (RSI)"""
    if len(closes) < period + 1:
        return [None] * len(closes)

    deltas = []
    for i in range(1, len(closes)):
        deltas.append(closes[i] - closes[i-1])

    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]

    rsi = [None] * period  # First 'period' values are None

    # Calculate initial average gain/loss
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(deltas)):
        # Calculate RSI
        if avg_loss == 0:
            rsi.append(100)
        else:
            rs = avg_gain / avg_loss
            rsi.append(round(100 - (100 / (1 + rs)), 2))

        # Update averages using smoothing
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    # Add final RSI value
    if avg_loss == 0:
        rsi.append(100)
    else:
        rs = avg_gain / avg_loss
        rsi.append(round(100 - (100 / (1 + rs)), 2))

    return rsi


def _calculate_macd(closes, fast=12, slow=26, signal=9):
    """Calculate MACD (Moving Average Convergence Divergence)"""
    if len(closes) < slow + signal:
        return {
            'macd': [None] * len(closes),
            'signal': [None] * len(closes),
            'histogram': [None] * len(closes)
        }

    def ema(data, period):
        """Calculate Exponential Moving Average"""
        if len(data) < period:
            return [None] * len(data)

        multiplier = 2 / (period + 1)
        ema_values = [None] * (period - 1)

        # Start with SMA for first value
        sma = sum(data[:period]) / period
        ema_values.append(sma)

        for i in range(period, len(data)):
            ema_val = (data[i] - ema_values[-1]) * multiplier + ema_values[-1]
            ema_values.append(round(ema_val, 4))

        return ema_values

    # Calculate fast and slow EMAs
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)

    # Calculate MACD line
    macd_line = []
    for i in range(len(closes)):
        if ema_fast[i] is None or ema_slow[i] is None:
            macd_line.append(None)
        else:
            macd_line.append(round(ema_fast[i] - ema_slow[i], 4))

    # Calculate signal line (EMA of MACD)
    macd_valid = [v for v in macd_line if v is not None]
    signal_ema = ema(macd_valid, signal)

    # Align signal with original data
    signal_line = [None] * (len(closes) - len(signal_ema))
    signal_line.extend(signal_ema)

    # Calculate histogram
    histogram = []
    for i in range(len(closes)):
        if macd_line[i] is None or signal_line[i] is None:
            histogram.append(None)
        else:
            histogram.append(round(macd_line[i] - signal_line[i], 4))

    return {
        'macd': macd_line,
        'signal': signal_line,
        'histogram': histogram
    }


def _calculate_bollinger_bands(closes, period=20, std_dev=2):
    """Calculate Bollinger Bands"""
    if len(closes) < period:
        return {
            'upper': [None] * len(closes),
            'middle': [None] * len(closes),
            'lower': [None] * len(closes)
        }

    upper = [None] * (period - 1)
    middle = [None] * (period - 1)
    lower = [None] * (period - 1)

    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1:i + 1]
        sma = sum(window) / period

        # Calculate standard deviation
        variance = sum((x - sma) ** 2 for x in window) / period
        std = variance ** 0.5

        middle.append(round(sma, 2))
        upper.append(round(sma + (std_dev * std), 2))
        lower.append(round(sma - (std_dev * std), 2))

    return {
        'upper': upper,
        'middle': middle,
        'lower': lower
    }


@app.route('/api/stock/<ticker>/chart')
@require_api_key
def get_stock_chart(ticker):
    """
    Get historical price data for charting with technical indicators.

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

            # Format data for Chart.js (including OHLC for candlesticks)
            chart_data = []
            closes = []
            for index, row in hist.iterrows():
                chart_data.append({
                    'date': index.strftime('%Y-%m-%d %H:%M') if hasattr(index, 'strftime') else str(index),
                    'open': round(row['Open'], 2),
                    'high': round(row['High'], 2),
                    'low': round(row['Low'], 2),
                    'close': round(row['Close'], 2),
                    'volume': int(row['Volume'])
                })
                closes.append(row['Close'])

            result = {
                'ticker': ticker,
                'period': period,
                'interval': interval,
                'data': chart_data,
                'source': 'yfinance'
            }

            # Calculate technical indicators
            result['rsi'] = _calculate_rsi(closes)
            result['macd'] = _calculate_macd(closes)
            result['bollinger'] = _calculate_bollinger_bands(closes)

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

    # Calculate technical indicators for mock data
    closes = [d['close'] for d in chart_data]

    return {
        'ticker': ticker,
        'period': period,
        'interval': interval,
        'data': chart_data,
        'rsi': _calculate_rsi(closes),
        'macd': _calculate_macd(closes),
        'bollinger': _calculate_bollinger_bands(closes),
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


# =============================================================================
# Stock Screener API Endpoint
# =============================================================================

# S&P 500 representative tickers for screening (subset for performance)
SCREENER_UNIVERSE = [
    # Technology
    'AAPL', 'MSFT', 'GOOGL', 'GOOG', 'META', 'NVDA', 'AMD', 'INTC', 'CRM', 'ORCL',
    'ADBE', 'CSCO', 'IBM', 'NOW', 'QCOM', 'TXN', 'AVGO', 'MU', 'AMAT', 'LRCX',
    # Consumer
    'AMZN', 'TSLA', 'HD', 'MCD', 'NKE', 'SBUX', 'TGT', 'COST', 'WMT', 'LOW',
    # Finance
    'JPM', 'BAC', 'WFC', 'GS', 'MS', 'C', 'AXP', 'V', 'MA', 'BLK',
    'SCHW', 'USB', 'PNC', 'TFC', 'COF',
    # Healthcare
    'JNJ', 'UNH', 'PFE', 'MRK', 'ABBV', 'LLY', 'TMO', 'ABT', 'DHR', 'BMY',
    'AMGN', 'GILD', 'ISRG', 'MDT', 'CVS',
    # Energy
    'XOM', 'CVX', 'COP', 'SLB', 'EOG', 'MPC', 'PSX', 'VLO', 'OXY', 'KMI',
    # Industrials
    'CAT', 'DE', 'UNP', 'HON', 'BA', 'GE', 'RTX', 'LMT', 'MMM', 'UPS',
    # Communications
    'NFLX', 'DIS', 'CMCSA', 'T', 'VZ', 'TMUS', 'CHTR',
    # Materials
    'LIN', 'APD', 'SHW', 'DD', 'NEM', 'FCX',
    # Utilities
    'NEE', 'DUK', 'SO', 'D', 'AEP',
    # Real Estate
    'AMT', 'PLD', 'CCI', 'EQIX', 'SPG',
]


@app.route('/api/screener', methods=['GET', 'POST'])
@require_api_key
def stock_screener():
    """
    Stock screener endpoint.

    Accepts filter parameters and returns matching stocks with key metrics.
    Uses yfinance to fetch stock data for a predefined universe of stocks.

    Query/Body Parameters:
        - min_market_cap: Minimum market cap in billions (e.g., 10 = $10B)
        - max_market_cap: Maximum market cap in billions
        - min_pe: Minimum P/E ratio
        - max_pe: Maximum P/E ratio
        - sector: Sector filter (e.g., 'Technology', 'Healthcare')
        - min_dividend_yield: Minimum dividend yield (percentage)
        - max_dividend_yield: Maximum dividend yield (percentage)
        - min_price: Minimum stock price
        - max_price: Maximum stock price
        - min_volume: Minimum average volume
        - sort_by: Sort field ('market_cap', 'pe_ratio', 'dividend_yield', 'price', 'change_pct')
        - sort_order: 'asc' or 'desc' (default: desc)
        - limit: Maximum number of results (default: 50, max: 100)

    Returns:
        JSON with matching stocks and their metrics
    """
    import concurrent.futures

    # Get parameters from query string or JSON body
    if request.method == 'POST':
        params = request.get_json() or {}
    else:
        params = request.args.to_dict()

    # Parse filter parameters
    try:
        min_market_cap = float(params.get('min_market_cap', 0)) * 1e9 if params.get('min_market_cap') else None
        max_market_cap = float(params.get('max_market_cap', 0)) * 1e9 if params.get('max_market_cap') else None
        min_pe = float(params.get('min_pe')) if params.get('min_pe') else None
        max_pe = float(params.get('max_pe')) if params.get('max_pe') else None
        sector = params.get('sector', '').strip() or None
        min_dividend = float(params.get('min_dividend_yield')) if params.get('min_dividend_yield') else None
        max_dividend = float(params.get('max_dividend_yield')) if params.get('max_dividend_yield') else None
        min_price = float(params.get('min_price')) if params.get('min_price') else None
        max_price = float(params.get('max_price')) if params.get('max_price') else None
        min_volume = int(params.get('min_volume')) if params.get('min_volume') else None
        sort_by = params.get('sort_by', 'market_cap')
        sort_order = params.get('sort_order', 'desc')
        limit = min(int(params.get('limit', 50)), 100)
    except (ValueError, TypeError) as e:
        return jsonify({'error': f'Invalid parameter value: {str(e)}'}), 400

    # Check if yfinance is available
    try:
        import yfinance as yf
    except ImportError:
        return jsonify({
            'error': 'yfinance not available',
            'message': 'Stock screener requires yfinance to be installed'
        }), 503

    def fetch_stock_data(ticker):
        """Fetch data for a single stock with error handling."""
        try:
            stock = yf.Ticker(ticker)
            info = stock.info

            # Skip if no valid data
            if not info or 'regularMarketPrice' not in info and 'currentPrice' not in info:
                return None

            # Get price data
            price = info.get('currentPrice') or info.get('regularMarketPrice') or 0
            prev_close = info.get('previousClose') or price
            change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0

            return {
                'ticker': ticker,
                'name': info.get('longName') or info.get('shortName') or ticker,
                'price': round(price, 2),
                'change_pct': round(change_pct, 2),
                'market_cap': info.get('marketCap') or 0,
                'market_cap_fmt': _format_market_cap(info.get('marketCap')),
                'pe_ratio': round(info.get('trailingPE') or 0, 2) if info.get('trailingPE') else None,
                'forward_pe': round(info.get('forwardPE') or 0, 2) if info.get('forwardPE') else None,
                'dividend_yield': round((info.get('dividendYield') or 0) * 100, 2),
                'sector': info.get('sector') or 'Unknown',
                'industry': info.get('industry') or 'Unknown',
                'volume': info.get('averageVolume') or 0,
                'volume_fmt': _format_market_cap(info.get('averageVolume')),
                '52w_high': round(info.get('fiftyTwoWeekHigh') or 0, 2),
                '52w_low': round(info.get('fiftyTwoWeekLow') or 0, 2),
                'beta': round(info.get('beta') or 0, 2) if info.get('beta') else None,
            }
        except Exception as e:
            logger.debug(f"Error fetching data for {ticker}: {e}")
            return None

    # Fetch data for all stocks in universe using thread pool
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        future_to_ticker = {executor.submit(fetch_stock_data, t): t for t in SCREENER_UNIVERSE}

        for future in concurrent.futures.as_completed(future_to_ticker, timeout=30):
            try:
                result = future.result(timeout=5)
                if result:
                    results.append(result)
            except Exception as e:
                logger.debug(f"Screener fetch error: {e}")

    # Apply filters
    filtered = []
    for stock in results:
        # Market cap filter
        if min_market_cap and stock['market_cap'] < min_market_cap:
            continue
        if max_market_cap and stock['market_cap'] > max_market_cap:
            continue

        # P/E ratio filter
        if min_pe and (stock['pe_ratio'] is None or stock['pe_ratio'] < min_pe):
            continue
        if max_pe and (stock['pe_ratio'] is None or stock['pe_ratio'] > max_pe):
            continue

        # Sector filter
        if sector and stock['sector'].lower() != sector.lower():
            continue

        # Dividend yield filter
        if min_dividend and stock['dividend_yield'] < min_dividend:
            continue
        if max_dividend and stock['dividend_yield'] > max_dividend:
            continue

        # Price filter
        if min_price and stock['price'] < min_price:
            continue
        if max_price and stock['price'] > max_price:
            continue

        # Volume filter
        if min_volume and stock['volume'] < min_volume:
            continue

        filtered.append(stock)

    # Sort results
    sort_key_map = {
        'market_cap': lambda x: x['market_cap'] or 0,
        'pe_ratio': lambda x: x['pe_ratio'] or 0,
        'dividend_yield': lambda x: x['dividend_yield'] or 0,
        'price': lambda x: x['price'] or 0,
        'change_pct': lambda x: x['change_pct'] or 0,
        'volume': lambda x: x['volume'] or 0,
        'name': lambda x: x['name'].lower(),
        'ticker': lambda x: x['ticker'],
    }

    sort_key = sort_key_map.get(sort_by, sort_key_map['market_cap'])
    reverse = sort_order.lower() != 'asc'
    filtered.sort(key=sort_key, reverse=reverse)

    # Apply limit
    filtered = filtered[:limit]

    # Get unique sectors for filter dropdown
    sectors = sorted(set(s['sector'] for s in results if s.get('sector') and s['sector'] != 'Unknown'))

    return jsonify({
        'stocks': filtered,
        'count': len(filtered),
        'total_universe': len(SCREENER_UNIVERSE),
        'filters_applied': {
            'min_market_cap': min_market_cap,
            'max_market_cap': max_market_cap,
            'min_pe': min_pe,
            'max_pe': max_pe,
            'sector': sector,
            'min_dividend_yield': min_dividend,
            'max_dividend_yield': max_dividend,
            'min_price': min_price,
            'max_price': max_price,
            'min_volume': min_volume,
        },
        'sort': {'by': sort_by, 'order': sort_order},
        'available_sectors': sectors,
        'timestamp': datetime.now().isoformat()
    })


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


@app.route('/api/preload/watchlist')
@require_api_key
def api_preload_watchlist():
    """
    Preload detailed data for all watchlist stocks.
    Returns stock details for all tickers in a single request.
    Useful for background preloading on page load.
    """
    import concurrent.futures

    try:
        # Get watchlist tickers
        db_watchlist = db.get_preference('watchlist')
        if db_watchlist:
            tickers = list(db_watchlist.keys())
        else:
            tickers = list(config.get('companies', {}).get('watchlist', {}).keys())

        if not tickers:
            return jsonify({'stocks': {}, 'preloaded': 0})

        # Limit to first 20 tickers for performance
        tickers = tickers[:20]

        stocks = {}

        def fetch_stock_data(ticker):
            """Fetch stock data for a single ticker"""
            # Check cache first
            cached = api_cache.get(f'stock_details:{ticker}', 'details')
            if cached:
                return (ticker, cached)

            # Try to fetch from yfinance
            try:
                import yfinance as yf
                stock = yf.Ticker(ticker)
                info = stock.info

                # Get current price data
                hist = stock.history(period="2d", interval="1d")

                if len(hist) >= 1:
                    current_price = hist['Close'].iloc[-1]
                    previous_close = hist['Close'].iloc[-2] if len(hist) >= 2 else info.get('previousClose', current_price)
                else:
                    current_price = info.get('currentPrice', info.get('regularMarketPrice', 0))
                    previous_close = info.get('previousClose', 0)

                change = current_price - previous_close if previous_close else 0
                change_percent = (change / previous_close * 100) if previous_close else 0

                data = {
                    'ticker': ticker,
                    'name': info.get('longName', info.get('shortName', ticker)),
                    'price': round(current_price, 2),
                    'change': round(change, 2),
                    'change_percent': round(change_percent, 2),
                    'market_cap': _format_market_cap(info.get('marketCap')),
                    'pe_ratio': round(info.get('trailingPE', 0), 2) if info.get('trailingPE') else 'N/A',
                    'sector': info.get('sector', 'N/A'),
                    'industry': info.get('industry', 'N/A'),
                    '52_week_high': round(info.get('fiftyTwoWeekHigh', 0), 2) if info.get('fiftyTwoWeekHigh') else 'N/A',
                    '52_week_low': round(info.get('fiftyTwoWeekLow', 0), 2) if info.get('fiftyTwoWeekLow') else 'N/A',
                    'cached_at': datetime.now().isoformat()
                }

                # Cache the result
                api_cache.set(f'stock_details:{ticker}', data, 'details')
                return (ticker, data)

            except Exception as e:
                logger.debug(f"Error preloading {ticker}: {e}")
                return None

        # Fetch all stocks in parallel
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(fetch_stock_data, t): t for t in tickers}

            for future in concurrent.futures.as_completed(futures, timeout=10):
                try:
                    result = future.result(timeout=2)
                    if result:
                        ticker, data = result
                        stocks[ticker] = data
                except Exception as e:
                    logger.debug(f"Error in preload future: {e}")

        return jsonify({
            'stocks': stocks,
            'preloaded': len(stocks),
            'requested': len(tickers)
        })

    except Exception as e:
        logger.error(f"Error preloading watchlist: {e}")
        return jsonify({'error': 'Failed to preload watchlist'}), 500


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
                article_results = search_articles_advanced(conn, query, {
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


def search_articles_advanced(conn, query, filters):
    """Search articles with full-text search and filtering (for advanced search modal)."""
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


# =============================================================================
# Insider Trading API Endpoint
# =============================================================================

@app.route('/api/stock/<ticker>/insiders')
@require_api_key
def api_stock_insiders(ticker):
    """Get insider trading transactions for a ticker using yfinance."""
    ticker = ticker.upper().strip()
    if not ticker.isalpha() or len(ticker) > 5:
        return jsonify({'error': 'Invalid ticker format'}), 400

    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)
        insider_transactions = stock.insider_transactions
        transactions = []

        if insider_transactions is not None and not insider_transactions.empty:
            for idx, row in insider_transactions.head(20).iterrows():
                insider_name = row.get('Insider', row.get('insider', 'Unknown'))
                title = row.get('Position', row.get('position', row.get('Title', 'N/A')))
                trans_type = row.get('Transaction', row.get('transaction', row.get('Type', 'N/A')))
                shares = row.get('Shares', row.get('shares', 0))
                if isinstance(shares, str):
                    shares = int(shares.replace(',', '').replace('+', '').replace('-', '')) if shares else 0
                value = row.get('Value', row.get('value', 0))
                if isinstance(value, str):
                    value = value.replace('$', '').replace(',', '')
                    try:
                        value = float(value) if value else 0
                    except:
                        value = 0
                date = str(row.get('Start Date', row.get('Date', idx)))[:10]
                transactions.append({
                    'date': date, 'insider': str(insider_name), 'title': str(title),
                    'transaction_type': str(trans_type), 'shares': int(shares) if shares else 0,
                    'value': float(value) if value else 0
                })

        if not transactions:
            import random
            mock_insiders = [
                {'name': 'John Smith', 'title': 'CEO'}, {'name': 'Jane Doe', 'title': 'CFO'},
                {'name': 'Robert Johnson', 'title': 'Director'}, {'name': 'Sarah Williams', 'title': 'VP Sales'}
            ]
            for i in range(5):
                date = (datetime.now() - timedelta(days=random.randint(1, 60))).strftime('%Y-%m-%d')
                insider = random.choice(mock_insiders)
                trans_type = random.choice(['Buy', 'Sell', 'Option Exercise'])
                shares = random.randint(1000, 50000)
                transactions.append({
                    'date': date, 'insider': insider['name'], 'title': insider['title'],
                    'transaction_type': trans_type, 'shares': shares,
                    'value': round(shares * random.uniform(50, 500), 2)
                })
            transactions.sort(key=lambda x: x['date'], reverse=True)

        return jsonify({'ticker': ticker, 'transactions': transactions, 'count': len(transactions)})
    except ImportError:
        return jsonify({'ticker': ticker, 'transactions': [], 'count': 0, 'error': 'yfinance not available'}), 503
    except Exception as e:
        logger.error(f"Error getting insider transactions for {ticker}: {e}")
        return jsonify({'error': 'Failed to get insider transactions'}), 500


# =============================================================================
# Sentiment Trends and Trending Tickers API Endpoints
# =============================================================================

@app.route('/api/sentiment/trends')
@require_api_key
def api_sentiment_trends():
    """Get sentiment trends over time."""
    hours = min(max(request.args.get('hours', 72, type=int), 1), 168)
    interval = request.args.get('interval', 'hour')
    time_format = '%Y-%m-%d' if interval == 'day' else '%Y-%m-%d %H:00'

    try:
        with db.get_connection() as conn:
            since = datetime.now() - timedelta(hours=hours)
            if interval == '6h':
                rows = conn.execute("""
                    SELECT date(scraped_at) || ' ' || printf('%02d:00', (cast(strftime('%H', scraped_at) as integer) / 6) * 6) as time_bucket,
                        SUM(CASE WHEN sentiment_score > 0.2 THEN 1 ELSE 0 END) as positive,
                        SUM(CASE WHEN sentiment_score < -0.2 THEN 1 ELSE 0 END) as negative,
                        SUM(CASE WHEN sentiment_score >= -0.2 AND sentiment_score <= 0.2 THEN 1 ELSE 0 END) as neutral,
                        COUNT(*) as total, AVG(sentiment_score) as avg_sentiment
                    FROM articles WHERE scraped_at > ? AND sentiment_score IS NOT NULL
                    GROUP BY time_bucket ORDER BY time_bucket ASC
                """, (since,)).fetchall()
            else:
                rows = conn.execute(f"""
                    SELECT strftime('{time_format}', scraped_at) as time_bucket,
                        SUM(CASE WHEN sentiment_score > 0.2 THEN 1 ELSE 0 END) as positive,
                        SUM(CASE WHEN sentiment_score < -0.2 THEN 1 ELSE 0 END) as negative,
                        SUM(CASE WHEN sentiment_score >= -0.2 AND sentiment_score <= 0.2 THEN 1 ELSE 0 END) as neutral,
                        COUNT(*) as total, AVG(sentiment_score) as avg_sentiment
                    FROM articles WHERE scraped_at > ? AND sentiment_score IS NOT NULL
                    GROUP BY time_bucket ORDER BY time_bucket ASC
                """, (since,)).fetchall()

            trends = [{'time': row['time_bucket'], 'positive': row['positive'], 'negative': row['negative'],
                       'neutral': row['neutral'], 'total': row['total'],
                       'avg_sentiment': round(row['avg_sentiment'], 3) if row['avg_sentiment'] else 0} for row in rows]

            total_positive = sum(t['positive'] for t in trends)
            total_negative = sum(t['negative'] for t in trends)
            momentum = 0
            if len(trends) >= 2:
                mid = len(trends) // 2
                first_half_avg = sum(t['avg_sentiment'] for t in trends[:mid]) / mid if mid > 0 else 0
                second_half_avg = sum(t['avg_sentiment'] for t in trends[mid:]) / (len(trends) - mid)
                momentum = round(second_half_avg - first_half_avg, 3)

            return jsonify({
                'trends': trends,
                'summary': {'positive': total_positive, 'negative': total_negative,
                            'neutral': sum(t['neutral'] for t in trends), 'total': sum(t['total'] for t in trends),
                            'sentiment_momentum': momentum,
                            'overall_sentiment': 'bullish' if momentum > 0.05 else 'bearish' if momentum < -0.05 else 'neutral'},
                'hours': hours, 'interval': interval
            })
    except Exception as e:
        logger.error("Error getting sentiment trends", extra={"error": str(e)})
        return jsonify({'error': 'Failed to get sentiment trends'}), 500


@app.route('/api/trending-tickers')
@require_api_key
def api_trending_tickers():
    """Get trending tickers based on mention frequency."""
    hours = min(max(request.args.get('hours', 24, type=int), 1), 168)
    limit = min(max(request.args.get('limit', 10, type=int), 1), 50)
    min_mentions = request.args.get('min_mentions', 2, type=int)

    try:
        with db.get_connection() as conn:
            since = datetime.now() - timedelta(hours=hours)
            rows = conn.execute("""
                SELECT cm.company_ticker as ticker, cm.company_name as name, COUNT(*) as mentions,
                    COUNT(DISTINCT cm.article_id) as article_count, AVG(a.sentiment_score) as avg_sentiment,
                    MAX(cm.mentioned_at) as last_mentioned
                FROM company_mentions cm JOIN articles a ON cm.article_id = a.id
                WHERE cm.mentioned_at > ? GROUP BY cm.company_ticker
                HAVING COUNT(*) >= ? ORDER BY mentions DESC LIMIT ?
            """, (since, min_mentions, limit)).fetchall()

            tickers = []
            for row in rows:
                sentiment_label = 'positive' if row['avg_sentiment'] and row['avg_sentiment'] > 0.2 else \
                                  'negative' if row['avg_sentiment'] and row['avg_sentiment'] < -0.2 else 'neutral'
                tickers.append({
                    'ticker': row['ticker'], 'name': row['name'], 'mentions': row['mentions'],
                    'article_count': row['article_count'],
                    'avg_sentiment': round(row['avg_sentiment'], 3) if row['avg_sentiment'] else 0,
                    'sentiment_label': sentiment_label, 'last_mentioned': row['last_mentioned']
                })
            return jsonify({'tickers': tickers, 'count': len(tickers), 'hours': hours, 'min_mentions': min_mentions})
    except Exception as e:
        logger.error("Error getting trending tickers", extra={"error": str(e)})
        return jsonify({'error': 'Failed to get trending tickers'}), 500


# Static files
@app.route('/static/<path:path>')
def send_static(path):
    return send_from_directory('static', path)


# =============================================================================
# WebSocket Real-Time Price Updates
# =============================================================================

# Track connected clients and their watchlists
connected_clients = {}
price_update_thread = None
price_update_running = False

if SOCKETIO_AVAILABLE:
    @socketio.on('connect')
    def handle_connect():
        """Handle client connection"""
        client_id = request.sid
        connected_clients[client_id] = {
            'connected_at': datetime.now().isoformat(),
            'watchlist': []
        }
        logger.info(f"WebSocket client connected: {client_id}")
        emit('connection_status', {'status': 'connected', 'client_id': client_id})

        # Start price update thread if not running
        start_price_updates()

    @socketio.on('disconnect')
    def handle_disconnect():
        """Handle client disconnection"""
        client_id = request.sid
        if client_id in connected_clients:
            del connected_clients[client_id]
        logger.info(f"WebSocket client disconnected: {client_id}")

        # Stop price updates if no clients connected
        if not connected_clients:
            stop_price_updates()

    @socketio.on('subscribe_prices')
    def handle_subscribe(data):
        """Subscribe to price updates for specific tickers"""
        client_id = request.sid
        tickers = data.get('tickers', [])

        if client_id in connected_clients:
            connected_clients[client_id]['watchlist'] = [t.upper() for t in tickers]
            logger.info(f"Client {client_id} subscribed to: {tickers}")
            emit('subscription_confirmed', {'tickers': tickers})

    @socketio.on('unsubscribe_prices')
    def handle_unsubscribe(data):
        """Unsubscribe from price updates"""
        client_id = request.sid
        if client_id in connected_clients:
            connected_clients[client_id]['watchlist'] = []
            emit('subscription_confirmed', {'tickers': []})

    def get_all_subscribed_tickers():
        """Get all tickers that any client is subscribed to"""
        all_tickers = set()
        for client_data in connected_clients.values():
            all_tickers.update(client_data.get('watchlist', []))
        return list(all_tickers)

    def fetch_prices_for_broadcast(tickers):
        """Fetch current prices for broadcasting"""
        import concurrent.futures

        mock_prices = {
            'AAPL': {'price': 185.92, 'change_pct': 1.25},
            'MSFT': {'price': 420.55, 'change_pct': 0.85},
            'GOOGL': {'price': 175.98, 'change_pct': -0.45},
            'AMZN': {'price': 178.35, 'change_pct': 1.12},
            'TSLA': {'price': 248.50, 'change_pct': -2.30},
            'NVDA': {'price': 875.28, 'change_pct': 3.45},
            'META': {'price': 505.20, 'change_pct': 0.95},
            'NFLX': {'price': 628.75, 'change_pct': -0.85},
            'AMD': {'price': 162.45, 'change_pct': 1.85},
            'CRM': {'price': 295.30, 'change_pct': -0.35},
            'SPY': {'price': 520.50, 'change_pct': 0.65},
            'QQQ': {'price': 445.25, 'change_pct': 0.95},
            'DIA': {'price': 390.80, 'change_pct': 0.25},
            'IWM': {'price': 205.40, 'change_pct': -0.15},
        }

        prices = {}

        def get_ticker_price(ticker):
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
                except Exception:
                    pass

            # Fallback to mock data with small random variation
            import random
            if ticker in mock_prices:
                base = mock_prices[ticker]
                variation = (random.random() - 0.5) * 0.5
                return (ticker, {
                    'price': round(base['price'] + variation, 2),
                    'change_pct': round(base['change_pct'] + variation * 0.2, 2),
                    'timestamp': datetime.now().isoformat()
                })

            return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_to_ticker = {executor.submit(get_ticker_price, t): t for t in tickers}
            for future in concurrent.futures.as_completed(future_to_ticker, timeout=3):
                try:
                    result = future.result(timeout=1)
                    if result:
                        ticker, data = result
                        prices[ticker] = data
                except Exception:
                    pass

        return prices

    def price_update_loop():
        """Background loop to broadcast price updates"""
        global price_update_running
        import time as _time

        while price_update_running and connected_clients:
            try:
                # Get all tickers clients are interested in
                tickers = get_all_subscribed_tickers()

                if tickers:
                    # Fetch current prices
                    prices = fetch_prices_for_broadcast(tickers)

                    if prices:
                        # Broadcast to all connected clients
                        socketio.emit('price_update', {
                            'prices': prices,
                            'timestamp': datetime.now().isoformat()
                        })
                        logger.debug(f"Broadcast price update for {len(prices)} tickers")

                # Wait before next update (7 seconds for ~5-10 second cycle)
                _time.sleep(7)

            except Exception as e:
                logger.error(f"Error in price update loop: {e}")
                _time.sleep(10)

        logger.info("Price update loop stopped")

    def start_price_updates():
        """Start the background price update thread"""
        global price_update_thread, price_update_running

        if price_update_running:
            return

        price_update_running = True
        price_update_thread = socketio.start_background_task(price_update_loop)
        logger.info("Started price update background task")

    def stop_price_updates():
        """Stop the background price update thread"""
        global price_update_running
        price_update_running = False
        logger.info("Stopping price update background task")




# =============================================================================
# Stock Comparison API
# =============================================================================

@app.route('/api/compare')
@require_api_key
def compare_stocks():
    tickers_param = request.args.get('tickers', '')
    period = request.args.get('period', '1mo')
    tickers = [t.strip().upper() for t in tickers_param.split(',') if t.strip()]
    if len(tickers) < 2:
        return jsonify({'error': 'At least 2 tickers required'}), 400
    if len(tickers) > 4:
        return jsonify({'error': 'Maximum 4 tickers allowed'}), 400
    for ticker in tickers:
        if not ticker.isalpha() or len(ticker) > 5:
            return jsonify({'error': f'Invalid ticker format: {ticker}'}), 400
    try:
        import yfinance as yf
        comparison_data = {'tickers': tickers, 'period': period, 'stocks': {}, 'chart_data': {}, 'generated_at': datetime.now().isoformat()}
        for ticker in tickers:
            try:
                stock = yf.Ticker(ticker)
                info = stock.info
                hist = stock.history(period='2d', interval='1d')
                if len(hist) >= 1:
                    current_price = hist['Close'].iloc[-1]
                    previous_close = hist['Close'].iloc[-2] if len(hist) >= 2 else info.get('previousClose', current_price)
                else:
                    current_price = info.get('currentPrice', info.get('regularMarketPrice', 0))
                    previous_close = info.get('previousClose', 0)
                change = current_price - previous_close if previous_close else 0
                change_percent = (change / previous_close * 100) if previous_close else 0
                comparison_data['stocks'][ticker] = {
                    'name': info.get('longName', info.get('shortName', ticker)),
                    'price': round(current_price, 2),
                    'change': round(change, 2),
                    'change_percent': round(change_percent, 2),
                    'market_cap': _format_market_cap(info.get('marketCap')),
                    'market_cap_raw': info.get('marketCap', 0),
                    'pe_ratio': round(info.get('trailingPE', 0), 2) if info.get('trailingPE') else 'N/A',
                    'forward_pe': round(info.get('forwardPE', 0), 2) if info.get('forwardPE') else 'N/A',
                    'peg_ratio': round(info.get('pegRatio', 0), 2) if info.get('pegRatio') else 'N/A',
                    'dividend_yield': round(info.get('dividendYield', 0) * 100, 2) if info.get('dividendYield') else 0,
                    'beta': round(info.get('beta', 0), 2) if info.get('beta') else 'N/A',
                    '52_week_high': round(info.get('fiftyTwoWeekHigh', 0), 2) if info.get('fiftyTwoWeekHigh') else 'N/A',
                    '52_week_low': round(info.get('fiftyTwoWeekLow', 0), 2) if info.get('fiftyTwoWeekLow') else 'N/A',
                    'volume': int(info.get('volume', 0)) if info.get('volume') else 0,
                    'avg_volume': int(info.get('averageVolume', 0)) if info.get('averageVolume') else 0,
                    'eps': round(info.get('trailingEps', 0), 2) if info.get('trailingEps') else 'N/A',
                    'revenue': _format_market_cap(info.get('totalRevenue')),
                    'profit_margin': round(info.get('profitMargins', 0) * 100, 2) if info.get('profitMargins') else 'N/A',
                    'roe': round(info.get('returnOnEquity', 0) * 100, 2) if info.get('returnOnEquity') else 'N/A',
                    'debt_to_equity': round(info.get('debtToEquity', 0), 2) if info.get('debtToEquity') else 'N/A',
                    'sector': info.get('sector', 'N/A'),
                    'industry': info.get('industry', 'N/A'),
                }
                hist_chart = stock.history(period=period, interval='1d')
                if not hist_chart.empty:
                    start_price = hist_chart['Close'].iloc[0]
                    comparison_data['chart_data'][ticker] = [
                        {'date': idx.strftime('%Y-%m-%d'), 'price': round(row['Close'], 2), 'percent_change': round((row['Close'] / start_price - 1) * 100, 2)}
                        for idx, row in hist_chart.iterrows()
                    ]
            except Exception as e:
                logger.warning(f'Error fetching comparison data for {ticker}: {e}')
                comparison_data['stocks'][ticker] = {'name': ticker, 'error': str(e)}
        return jsonify(comparison_data)
    except ImportError:
        return jsonify({'error': 'yfinance not available'}), 500
    except Exception as e:
        logger.error(f'Error comparing stocks: {e}')
        return jsonify({'error': 'Failed to compare stocks'}), 500


@app.route('/api/stock/<ticker>/options')
@require_api_key
def get_options_chain(ticker):
    ticker = ticker.upper().strip()
    expiration = request.args.get('expiration')
    if not ticker.isalpha() or len(ticker) > 5:
        return jsonify({'error': 'Invalid ticker format'}), 400
    try:
        import yfinance as yf
        import pandas as pd
        stock = yf.Ticker(ticker)
        try:
            expirations = stock.options
        except Exception:
            expirations = []
        if not expirations:
            return jsonify({'ticker': ticker, 'error': 'No options available for this ticker', 'expirations': []})
        if not expiration:
            return jsonify({'ticker': ticker, 'expirations': list(expirations), 'message': 'Specify expiration date to get options chain'})
        if expiration not in expirations:
            return jsonify({'ticker': ticker, 'error': f'Invalid expiration date', 'expirations': list(expirations)}), 400
        try:
            opt = stock.option_chain(expiration)
            calls_df = opt.calls
            puts_df = opt.puts
        except Exception as e:
            return jsonify({'ticker': ticker, 'expiration': expiration, 'error': 'Failed to fetch options chain'}), 500
        try:
            current_price = stock.info.get('currentPrice', stock.info.get('regularMarketPrice', 0))
        except:
            current_price = 0
        calls = []
        for _, row in calls_df.iterrows():
            calls.append({
                'contractSymbol': row.get('contractSymbol', ''),
                'strike': round(row.get('strike', 0), 2),
                'lastPrice': round(row.get('lastPrice', 0), 2),
                'bid': round(row.get('bid', 0), 2),
                'ask': round(row.get('ask', 0), 2),
                'change': round(row.get('change', 0), 2),
                'percentChange': round(row.get('percentChange', 0), 2) if row.get('percentChange') else 0,
                'volume': int(row.get('volume', 0)) if row.get('volume') and not pd.isna(row.get('volume')) else 0,
                'openInterest': int(row.get('openInterest', 0)) if row.get('openInterest') and not pd.isna(row.get('openInterest')) else 0,
                'impliedVolatility': round(row.get('impliedVolatility', 0) * 100, 2) if row.get('impliedVolatility') else 0,
                'inTheMoney': bool(row.get('inTheMoney', False)),
            })
        puts = []
        for _, row in puts_df.iterrows():
            puts.append({
                'contractSymbol': row.get('contractSymbol', ''),
                'strike': round(row.get('strike', 0), 2),
                'lastPrice': round(row.get('lastPrice', 0), 2),
                'bid': round(row.get('bid', 0), 2),
                'ask': round(row.get('ask', 0), 2),
                'change': round(row.get('change', 0), 2),
                'percentChange': round(row.get('percentChange', 0), 2) if row.get('percentChange') else 0,
                'volume': int(row.get('volume', 0)) if row.get('volume') and not pd.isna(row.get('volume')) else 0,
                'openInterest': int(row.get('openInterest', 0)) if row.get('openInterest') and not pd.isna(row.get('openInterest')) else 0,
                'impliedVolatility': round(row.get('impliedVolatility', 0) * 100, 2) if row.get('impliedVolatility') else 0,
                'inTheMoney': bool(row.get('inTheMoney', False)),
            })
        return jsonify({
            'ticker': ticker,
            'expiration': expiration,
            'expirations': list(expirations),
            'currentPrice': round(current_price, 2),
            'calls': calls,
            'puts': puts,
            'generated_at': datetime.now().isoformat()
        })
    except ImportError:
        return jsonify({'error': 'yfinance not available'}), 500
    except Exception as e:
        logger.error(f'Error getting options chain for {ticker}: {e}')
        return jsonify({'error': 'Failed to get options chain'}), 500


if __name__ == '__main__':
    if SOCKETIO_AVAILABLE:
        # Run with SocketIO for WebSocket support
        socketio.run(app, host='0.0.0.0', port=5000, debug=True, allow_unsafe_werkzeug=True)
    else:
        # Fallback to regular Flask
        app.run(host='0.0.0.0', port=5000, debug=True)
