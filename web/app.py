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
