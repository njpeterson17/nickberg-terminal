# Nickberg Terminal

A financial news monitoring bot that scrapes major news sources to detect coverage patterns like volume spikes, sentiment shifts, and momentum building for tracked companies.

## Key Features

- **Multi-source scraping**: Reuters, Bloomberg, CNBC, WSJ, Financial Times, and 20+ other financial news sources
- **Pattern detection**: Volume spikes, sentiment shifts, momentum building, and negative news clusters
- **Real-time alerts**: Console, file logging, Telegram, and webhook notifications
- **Web dashboard**: Live monitoring with charts, alerts, and manual bot triggers
- **API access**: RESTful API with authentication for programmatic access
- **SQLite storage**: Persistent storage with automatic cleanup and retention policies

## Quick Start

### Prerequisites

- Python 3.11 or higher
- pip package manager

### Installation

```bash
# Clone the repository
git clone https://github.com/njpeterson17/nickberg-terminal.git
cd nickberg-terminal

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Basic Usage

```bash
# Run the scraper once
python src/main.py run

# Run in dry-run mode (no database writes)
python src/main.py run --dry-run

# Check bot status
python src/main.py status

# Add a company to the watchlist
python src/main.py watchlist add TSLA "Tesla,Elon Musk,Model 3"

# Clear all alerts
python src/main.py reset-alerts
```

### Web Dashboard

Launch the web dashboard for real-time monitoring:

```bash
./start-web.sh
```

Open http://localhost:5000 in your browser.

Dashboard features:
- Real-time stats and charts
- Active alerts with one-click acknowledgment
- Mention timeline visualization
- Sentiment analysis dashboard
- Recent articles feed with filtering
- Manual bot trigger

## Configuration

Configuration is managed via `config/settings.yaml`.

### Environment Variables

Create a `.env` file or export these variables:

| Variable | Description | Required |
|----------|-------------|----------|
| `NICKBERG_TELEGRAM_TOKEN` | Telegram bot token for alerts | No |
| `NICKBERG_TELEGRAM_CHAT_ID` | Telegram chat ID for alerts | No |
| `NICKBERG_WEBHOOK_URL` | Webhook URL for external integrations | No |
| `NICKBERG_API_KEY` | API key for dashboard authentication | No |
| `LOG_LEVEL` | Logging level (DEBUG, INFO, WARNING, ERROR) | No |
| `LOG_FORMAT` | Log output format | No |

### Settings File Structure

```yaml
# config/settings.yaml

# Scraping settings
scraping:
  delay_min: 0.1
  delay_max: 0.5
  timeout: 30
  max_retries: 3

# News sources (enable/disable as needed)
sources:
  reuters:
    enabled: true
    name: "Reuters"
    rss_feeds:
      - "https://www.reutersagency.com/feed/..."

# Pattern detection thresholds
patterns:
  volume_spike_threshold: 3.0  # 3x normal coverage
  min_articles_for_alert: 3
  windows:
    short: 6    # hours
    medium: 24  # hours
    long: 168   # hours (7 days)

# Company watchlist
companies:
  watchlist:
    AAPL: ["Apple", "AAPL"]
    TSLA: ["Tesla", "TSLA", "Elon Musk"]
  auto_detect: true
  auto_detect_threshold: 5

# Alert configuration
alerts:
  console: true
  file:
    enabled: true
    path: "logs/alerts.log"
  telegram:
    enabled: false
  webhook:
    enabled: false

# Database settings
database:
  path: "data/nickberg.db"
  retention_days: 30
```

### Adding Companies to Watchlist

Edit `config/settings.yaml`:

```yaml
companies:
  watchlist:
    TICKER: ["Company Name", "Alternate Name", "CEO Name"]
```

Or use the CLI:

```bash
python src/main.py watchlist add NVDA "Nvidia,Jensen Huang"
```

### Adding/Removing News Sources

Edit `config/settings.yaml`:

```yaml
sources:
  your_source:
    enabled: true  # Set to false to disable
    name: "Your Source"
    rss_feeds:
      - "https://example.com/feed.xml"
```

## Docker Deployment

### Build and Run

```bash
# Build the image
docker build -t nickberg-terminal .

# Run the scraper
docker run -d \
  --name nickberg-terminal \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  -v $(pwd)/config:/app/config \
  -e NICKBERG_API_KEY=your-api-key \
  -e NICKBERG_TELEGRAM_TOKEN=your-token \
  -e NICKBERG_TELEGRAM_CHAT_ID=your-chat-id \
  nickberg-terminal scraper

# Run the web dashboard
docker run -d \
  --name nickberg-terminal-web \
  -p 5000:5000 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/config:/app/config \
  -e NICKBERG_API_KEY=your-api-key \
  nickberg-terminal web
```

### Volume Mounts

| Mount | Description |
|-------|-------------|
| `/app/data` | SQLite database and cache files |
| `/app/logs` | Log files |
| `/app/config` | Configuration files |

### Docker Commands

| Command | Description |
|---------|-------------|
| `scraper` | Run the news scraper (default) |
| `web` | Start the web dashboard |
| `schedule` | Run once (for cron/scheduler) |

## API Documentation

All API endpoints (except `/health` and `/metrics`) require authentication when `NICKBERG_API_KEY` is set.

### Authentication

Include the API key in requests via:
- Header: `X-API-Key: your-api-key`
- Query parameter: `?api_key=your-api-key`

### Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Web dashboard (HTML) |
| `/health` | GET | Health check (no auth required) |
| `/metrics` | GET | Prometheus metrics (no auth required) |
| `/api/stats` | GET | Database statistics |
| `/api/alerts` | GET | Recent unacknowledged alerts |
| `/api/alerts/<id>/ack` | POST | Acknowledge an alert |
| `/api/timeline` | GET | Mention timeline data |
| `/api/companies/top` | GET | Top mentioned companies |
| `/api/companies/all` | GET | All companies with stats |
| `/api/articles` | GET | Recent articles |
| `/api/sentiment` | GET | Sentiment distribution |
| `/api/sources` | GET | Source distribution |
| `/api/config` | GET | Current bot configuration |
| `/api/run` | POST | Trigger bot run |

### Example API Calls

```bash
# Get stats
curl -H "X-API-Key: your-api-key" http://localhost:5000/api/stats

# Get recent alerts
curl -H "X-API-Key: your-api-key" http://localhost:5000/api/alerts?limit=10

# Acknowledge an alert
curl -X POST -H "X-API-Key: your-api-key" http://localhost:5000/api/alerts/1/ack

# Get top companies
curl -H "X-API-Key: your-api-key" http://localhost:5000/api/companies/top?limit=5

# Trigger bot run
curl -X POST -H "X-API-Key: your-api-key" http://localhost:5000/api/run

# Health check (no auth)
curl http://localhost:5000/health

# Prometheus metrics (no auth)
curl http://localhost:5000/metrics
```

## Architecture Overview

### Components

```
nickberg-terminal/
├── src/
│   ├── main.py              # CLI entry point and orchestration
│   ├── scraper.py           # Multi-source RSS feed scraper
│   ├── company_extractor.py # Entity extraction from articles
│   ├── pattern_detector.py  # Pattern detection algorithms
│   ├── alerts.py            # Alert management and delivery
│   ├── database.py          # SQLite data layer
│   └── logging_config.py    # Logging configuration
├── web/
│   ├── app.py               # Flask web application
│   ├── templates/           # HTML templates
│   └── static/              # CSS, JS, images
├── config/
│   └── settings.yaml        # Configuration file
├── data/                    # SQLite database (gitignored)
└── logs/                    # Log files (gitignored)
```

### Data Flow

```
RSS Feeds → Scraper → Company Extractor → Pattern Detector → Alerts
                ↓              ↓                  ↓            ↓
             Articles      Mentions           Patterns      Telegram
                ↓              ↓                  ↓         Webhook
              SQLite ←←←←←←←←←←←←←←←←←←←←←←←←←←←←←         Console
                ↓                                            File
            Web Dashboard / API
```

### Pattern Types

| Pattern | Description | Trigger |
|---------|-------------|---------|
| **Volume Spike** | Unusual increase in coverage | 3x+ normal article count |
| **Sentiment Shift** | Significant tone change | Large positive/negative swing |
| **Momentum Building** | Increasing coverage trend | Sustained growth over days |
| **Negative Cluster** | Concentrated bad news | Multiple negative articles in short window |

## Development

### Setup Development Environment

```bash
# Install all dependencies (including dev)
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src --cov-report=term-missing

# Run specific test file
pytest tests/test_database.py -v
```

### Linting and Formatting

```bash
# Check code style
ruff check src/ tests/

# Format code
ruff format src/ tests/

# Type checking
mypy src/ --ignore-missing-imports

# Security scan
bandit -r src/ -ll -ii
```

### Pre-commit Hooks

```bash
# Install pre-commit hooks
pre-commit install

# Run manually
pre-commit run --all-files
```

### Contributing Guidelines

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Write tests for new functionality
4. Ensure all tests pass: `pytest`
5. Ensure code passes linting: `ruff check src/ tests/`
6. Commit changes with clear messages
7. Push to your fork and submit a pull request

## Scheduled Running

### Using Cron

```bash
# Run every 15 minutes
*/15 * * * * cd /path/to/nickberg-terminal && /path/to/.venv/bin/python src/main.py schedule >> logs/cron.log 2>&1
```

### Using Systemd Timer

Create `/etc/systemd/system/nickberg.service`:

```ini
[Unit]
Description=Nickberg Terminal

[Service]
Type=oneshot
WorkingDirectory=/path/to/nickberg-terminal
ExecStart=/path/to/.venv/bin/python src/main.py schedule
User=your-user
```

Create `/etc/systemd/system/nickberg.timer`:

```ini
[Unit]
Description=Run Nickberg Terminal every 15 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=15min

[Install]
WantedBy=timers.target
```

Enable: `sudo systemctl enable --now nickberg.timer`

## Troubleshooting

**No articles found?**
- Check RSS feed URLs in config
- Verify internet connectivity
- Some sites may block scrapers (rate limiting is built-in)

**Too many/few alerts?**
- Adjust `volume_spike_threshold` (default: 3.0)
- Change `min_articles_for_alert` (default: 3)

**Database issues?**
- Delete `data/nickberg.db` to reset
- Check disk space and permissions

**API authentication failing?**
- Verify `NICKBERG_API_KEY` is set
- Check header format: `X-API-Key: your-key`

## License

MIT License - see LICENSE file for details.
