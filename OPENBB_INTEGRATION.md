# OpenBB API Integration for Nickberg Terminal

This document describes how the Nickberg Terminal integrates with OpenBB APIs (FMP, Polygon, FRED) for enhanced market data and news.

## API Keys Configured

The following API keys have been added to your `.env` file:

| Provider | API Key | Purpose |
|----------|---------|---------|
| **FMP** | `2O1HH1S0QmSYvWRUeXI1Am1Y4X6N4h9I` | Stock prices, financials, company profiles |
| **Polygon** | `ixRram050TnngpJ2C7Jp85s0G8u4lOlN` | News articles |
| **FRED** | `ad8c581933c2f2205cbc8587c56475e1` | Economic indicators (CPI, rates, GDP) |

## Components Added

### 1. OpenBB Market Data Provider (`src/openbb_market_data.py`)

Enhanced market data provider using OpenBB APIs as an alternative to yfinance.

**Features:**
- Real-time stock quotes via FMP
- Historical price data
- Company profiles and financials
- Comprehensive market context
- News via Polygon
- Economic indicators via FRED

**Usage:**
```python
from openbb_market_data import create_market_data_provider

# Create provider (auto-detects based on .env)
provider = create_market_data_provider()

# Get stock price
price = provider.get_price("AAPL")

# Get market context (price, change, company info)
context = provider.get_market_context("TSLA")

# Get news
news = provider.get_news("NVDA", limit=5)

# Get economic indicators
indicators = provider.get_key_economic_indicators()
```

**Environment Variables:**
```bash
MARKET_DATA_PROVIDER=openbb  # or "yfinance" for original provider
OPENBB_ENABLE_FMP=true
OPENBB_ENABLE_POLYGON=true
OPENBB_ENABLE_FRED=true
```

### 2. Polygon News Scraper (`src/polygon_scraper.py`)

Fetches real-time news from Polygon.io for watchlist companies.

**Features:**
- Real-time news for watchlist tickers
- Automatic deduplication via URL
- Publisher attribution
- Published timestamp parsing

**Usage:**
```python
from polygon_scraper import PolygonNewsScraper, create_polygon_source

# Create scraper
scraper = PolygonNewsScraper(
    tickers=["AAPL", "TSLA", "NVDA"],
    articles_per_ticker=5
)

# Fetch articles
articles = scraper.scrape_sync()

# Or use with ScraperManager
source = create_polygon_source(watchlist)
articles = source.fetch_articles()
```

### 3. FRED Economic Alerts (`src/fred_alerts.py`)

Monitors economic indicators and generates alerts on significant changes.

**Features:**
- Tracks 6 key indicators: Treasury rates, Fed Funds, Unemployment, CPI, GDP, S&P 500
- Configurable thresholds for alerts
- Severity classification (high/medium/low)
- Trend detection

**Monitored Indicators:**
| Indicator | Symbol | Default Threshold |
|-----------|--------|-------------------|
| 10Y Treasury | DGS10 | 5% or 0.1pp |
| 2Y Treasury | DGS2 | 5% or 0.1pp |
| Fed Funds Rate | FEDFUNDS | 10% or 0.25pp |
| Unemployment | UNRATE | 5% or 0.2pp |
| CPI | CPIAUCSL | 1% |
| S&P 500 | SP500 | 2% |

**Usage:**
```python
from fred_alerts import FREDEconomicMonitor, EconomicAlertManager

# Create monitor
monitor = FREDEconomicMonitor()

# Check all indicators
alerts = monitor.check_all_indicators()

# Or use the manager
alert_manager = EconomicAlertManager()
alerts = alert_manager.check_and_generate_alerts()
```

## Integration with Existing Bot

### Option 1: Replace Market Data Provider

Edit `src/main.py` to use OpenBB provider:

```python
# Change this:
from market_data import MarketDataProvider
self.market_data = MarketDataProvider()

# To this:
from openbb_market_data import create_market_data_provider
self.market_data = create_market_data_provider()
```

### Option 2: Add Polygon to ScraperManager

Edit `src/scraper.py` to include Polygon:

```python
from polygon_scraper import create_polygon_source

class ScraperManager:
    def __init__(self, config_path: str = "config/settings.yaml"):
        # ... existing scrapers ...
        
        # Add Polygon scraper
        self.polygon_source = create_polygon_source(
            self.config["companies"]["watchlist"]
        )
```

### Option 3: Add FRED Alerts to Main Loop

Edit `src/main.py` to include economic alerts:

```python
from fred_alerts import EconomicAlertManager

class NickbergTerminal:
    def __init__(self, config_path: str = "config/settings.yaml"):
        # ... existing init ...
        self.economic_alert_manager = EconomicAlertManager()
    
    def run(self, dry_run: bool = False):
        # ... existing steps ...
        
        # Step X: Check economic indicators
        economic_alerts = self.economic_alert_manager.check_and_generate_alerts()
        if economic_alerts:
            self.alert_manager.send_alerts(economic_alerts)
```

## Testing the Integration

### Test OpenBB Market Data

```bash
cd /home/nick/nickberg-terminal
source .venv/bin/activate
python3 << 'EOF'
import sys
sys.path.insert(0, "src")

from openbb_market_data import create_market_data_provider

provider = create_market_data_provider()

# Test stock quote
print("AAPL Price:", provider.get_price("AAPL"))

# Test market context
context = provider.get_market_context("TSLA")
print("TSLA Context:", context)

# Test news
news = provider.get_news("NVDA", limit=2)
print("NVDA News:", len(news), "articles")

# Test economic indicators
indicators = provider.get_key_economic_indicators()
print("Economic Indicators:", list(indicators.keys()))
EOF
```

### Test Polygon Scraper

```bash
python3 << 'EOF'
import sys
sys.path.insert(0, "src")

from polygon_scraper import PolygonNewsScraper

scraper = PolygonNewsScraper(
    tickers=["AAPL", "TSLA"],
    articles_per_ticker=3
)

articles = scraper.scrape_sync()
for article in articles:
    print(f"[{article.source}] {article.title}")
EOF
```

### Test FRED Alerts

```bash
python3 << 'EOF'
import sys
sys.path.insert(0, "src")

from fred_alerts import FREDEconomicMonitor

monitor = FREDEconomicMonitor()

# Get summary
summary = monitor.get_indicator_summary()
for key, data in summary.items():
    print(f"{data['name']}: {data['value']}")

# Check for alerts
alerts = monitor.check_all_indicators()
for alert in alerts:
    print(f"ALERT: {alert.message}")
EOF
```

## Rate Limits

| Provider | Free Tier Limit |
|----------|-----------------|
| FMP | 250 API calls/day |
| Polygon | 5 API calls/minute |
| FRED | No limit (respectful usage) |

## Troubleshooting

### OpenBB Not Found
```bash
pip install openbb
```

### API Key Errors
Check that `.env` file exists and contains:
```bash
FMP_API_KEY=your_key
POLYGON_API_KEY=your_key
FRED_API_KEY=your_key
```

### Provider Not Switching
Make sure to set:
```bash
MARKET_DATA_PROVIDER=openbb
```

## Files Added

1. `src/openbb_market_data.py` - OpenBB market data provider
2. `src/polygon_scraper.py` - Polygon news scraper
3. `src/fred_alerts.py` - FRED economic alerts
4. `.env` - Environment variables with API keys
5. `OPENBB_INTEGRATION.md` - This documentation
