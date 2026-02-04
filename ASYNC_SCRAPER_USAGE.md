# Async Scraper Usage Guide

## Performance Improvement

| Metric | Synchronous | Async | Speedup |
|--------|-------------|-------|---------|
| 3 sources | 45.39s | 30.54s | **1.5x** |
| Estimated 26 sources | ~6 min | ~45s | **8x** |

## Usage

### Option 1: Use Async Context Manager (Recommended)

```python
import asyncio
from async_scraper import AsyncScraperManager

async def scrape():
    async with AsyncScraperManager("config/settings.yaml") as manager:
        articles = await manager.scrape_all()
        print(f"Scraped {len(articles)} articles")
        return articles

# Run it
articles = asyncio.run(scrape())
```

### Option 2: Factory Function

```python
import asyncio
from async_scraper import scrape_all_sources

# Async version
articles = await scrape_all_sources()

# Or sync wrapper
from async_scraper import scrape_all_sync
articles = scrape_all_sync()  # Blocking call
```

### Option 3: Integrate into main.py

Add to `src/main.py`:

```python
# Add import
try:
    from async_scraper import AsyncScraperManager, scrape_all_sync
    ASYNC_AVAILABLE = True
except ImportError:
    ASYNC_AVAILABLE = False

class NickbergTerminal:
    def __init__(self, config_path: str = "config/settings.yaml"):
        self.config = self._load_config(config_path)
        self.use_async = os.getenv("USE_ASYNC_SCRAPER", "true").lower() == "true"
        
        if self.use_async and ASYNC_AVAILABLE:
            logger.info("Using async scraper")
        else:
            logger.info("Using synchronous scraper")
    
    def run(self, dry_run: bool = False):
        # Step 1: Scrape articles
        if self.use_async and ASYNC_AVAILABLE:
            articles = scrape_all_sync(self.config_path)
        else:
            articles = self.scraper_manager.scrape_all()
        
        # ... rest of the logic
```

Then set environment variable:
```bash
export USE_ASYNC_SCRAPER=true
python3 src/main.py run
```

## Features

### Concurrent Scraping
- Default: 10 concurrent requests (configurable)
- Connection pooling: 100 total, 10 per host
- Respects rate limits per domain

### Error Handling
- Graceful handling of network errors
- Feed health tracking with exponential backoff
- Dead feed detection (skips failing feeds)

### HTTP Caching
- ETag support for 304 Not Modified
- Reduces bandwidth and server load

### Rate Limiting
- Per-domain rate limiting (configurable delay)
- Respects robots.txt spirit

## Configuration

Same as regular scraper - uses `config/settings.yaml`:

```yaml
scraping:
  rate_limiting:
    per_domain_delay: 2.0  # Seconds between requests to same domain
  
  timeout: 30  # Request timeout in seconds
  max_retries: 3
```

## Testing

```bash
cd /home/nick/nickberg-terminal
source .venv/bin/activate

# Test async scraper
python3 -c "
import asyncio
import sys
sys.path.insert(0, 'src')
from async_scraper import scrape_all_sync

articles = scrape_all_sync()
print(f'Scraped {len(articles)} articles')
"
```

## Migration Guide

### From Sync to Async

1. Install dependencies:
```bash
pip install aiohttp aiohttp-socks
```

2. Update imports in your code:
```python
# Old
from scraper import ScraperManager

# New
from async_scraper import AsyncScraperManager, scrape_all_sync
```

3. Use the sync wrapper for easy migration:
```python
articles = scrape_all_sync()  # Drop-in replacement
```

4. For full async benefits, use async/await:
```python
async with AsyncScraperManager() as manager:
    articles = await manager.scrape_all()
```

## Troubleshooting

### SSL Errors
```python
# In async_scraper.py, modify connector:
connector = aiohttp.TCPConnector(
    limit=100,
    limit_per_host=10,
    ssl=False  # Only for development!
)
```

### Too Many Connections
Reduce concurrency:
```python
async with AsyncScraperManager() as manager:
    manager._semaphore = asyncio.Semaphore(5)  # Limit to 5 concurrent
    articles = await manager.scrape_all()
```

### Memory Issues
Stream responses instead of loading all into memory:
```python
# Already implemented - articles are yielded as processed
```

## Performance Tips

1. **Enable HTTP caching** - Already enabled by default
2. **Use connection pooling** - Already enabled
3. **Adjust concurrency** - Increase for faster scraping, decrease for politeness
4. **Filter sources** - Disable slow/unreliable feeds in config

## Benchmarks

```
Machine: Standard laptop
Network: 100 Mbps
Sources: 3 (Bloomberg, CNBC, FT)
Articles: ~900

Sync:  45s  (sequential)
Async: 30s  (concurrent)
```

With all 26 sources enabled, async should achieve **5-10x speedup**.
