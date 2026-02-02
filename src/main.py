"""
News Sentinel Bot - Main runner
"""

import os
import sys
import json
import yaml
import logging
import argparse
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from database import Database, Article, CompanyMention
from scraper import ScraperManager
from company_extractor import CompanyExtractor
from pattern_detector import PatternDetector
from alerts import AlertManager
from logging_config import setup_logging, get_logger

# Path to last scrape timestamp file
LAST_SCRAPE_FILE = Path(__file__).parent.parent / "data" / "last_scrape.json"


def record_last_scrape_time():
    """Record the timestamp of a successful scrape to a file"""
    try:
        LAST_SCRAPE_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {"last_scrape": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}
        with open(LAST_SCRAPE_FILE, "w") as f:
            json.dump(data, f)
    except OSError as e:
        get_logger(__name__).warning("Failed to record last scrape time", extra={"error": str(e)})


class NewsSentinelBot:
    """Main bot class"""

    def __init__(self, config_path: str = "config/settings.yaml"):
        self.config_path = config_path
        self.config = self._load_config()

        # Initialize components
        self.db = Database(self.config["database"]["path"])
        self.scraper_manager = ScraperManager(config_path)
        self.company_extractor = CompanyExtractor(self.config["companies"]["watchlist"])
        self.pattern_detector = PatternDetector(self.db, self.config["patterns"])
        self.alert_manager = AlertManager(self.config["alerts"], self.db)

        self.logger = get_logger(__name__)

    def _load_config(self) -> dict:
        """Load configuration from YAML"""
        with open(self.config_path) as f:
            return yaml.safe_load(f)

    def run(self, dry_run: bool = False):
        """Run one cycle of the bot"""
        self.logger.info("Starting News Sentinel Bot cycle", extra={"dry_run": dry_run})

        # Step 1: Scrape articles
        self.logger.info("Scraping articles", extra={"step": 1})
        articles = self.scraper_manager.scrape_all()

        if not articles:
            self.logger.warning("No articles found")
            return

        # Step 2: Process articles
        self.logger.info("Processing articles", extra={"step": 2, "total_articles": len(articles)})
        new_articles = 0
        mentions_count = 0

        for article_data in articles:
            # Extract companies
            matches = self.company_extractor.extract(article_data.content)

            if not matches:
                continue

            # Analyze sentiment
            sentiment_score = None
            if article_data.content:
                sentiment_score = self.pattern_detector.sentiment_analyzer.analyze(
                    article_data.content
                )

            # Prepare mentions JSON
            mentions = json.dumps([m.ticker for m in matches])

            # Create article object
            article = Article(
                id=None,
                url=article_data.url,
                title=article_data.title,
                content=article_data.content,
                source=article_data.source,
                published_at=article_data.published_at,
                scraped_at=datetime.now(),
                sentiment_score=sentiment_score,
                mentions=mentions,
            )

            # Save article
            if not dry_run:
                article_id = self.db.save_article(article)
            else:
                article_id = 1  # Fake ID for dry run

            if article_id:
                new_articles += 1

                # Save company mentions
                for match in matches:
                    mention = CompanyMention(
                        id=None,
                        company_ticker=match.ticker,
                        company_name=match.name,
                        article_id=article_id,
                        mentioned_at=article_data.published_at or datetime.now(),
                        context=match.context[:500],  # Limit context size
                    )

                    if not dry_run:
                        if self.db.save_company_mention(mention):
                            mentions_count += 1
                    else:
                        mentions_count += 1

        self.logger.info(
            "Articles processed", extra={"new_articles": new_articles, "mentions": mentions_count}
        )

        # Step 3: Detect patterns
        self.logger.info("Detecting patterns", extra={"step": 3})
        alerts = self.pattern_detector.detect_all_patterns()

        self.logger.info("Patterns detected", extra={"patterns_found": len(alerts)})

        # Step 4: Send alerts
        if alerts and not dry_run:
            self.logger.info("Sending alerts", extra={"step": 4, "alert_count": len(alerts)})
            self.alert_manager.send_alerts(alerts)
        elif dry_run and alerts:
            self.logger.info(
                "DRY RUN - Would send alerts", extra={"step": 4, "alert_count": len(alerts)}
            )
            for alert in alerts:
                self.alert_manager._console_alert(alert)

        # Step 5: Cleanup
        if not dry_run:
            self.logger.info(
                "Cleaning up old data",
                extra={"step": 5, "retention_days": self.config["database"]["retention_days"]},
            )
            self.db.cleanup_old_data(self.config["database"]["retention_days"])

        # Log stats
        stats = self.db.get_stats()
        self.logger.info(
            "Cycle complete",
            extra={
                "total_articles": stats["total_articles"],
                "total_mentions": stats["total_mentions"],
                "total_alerts": stats["total_alerts"],
                "articles_24h": stats["articles_24h"],
            },
        )

        # Record successful scrape timestamp
        if not dry_run:
            record_last_scrape_time()
            self.logger.info("Recorded last scrape timestamp")

    def show_status(self):
        """Show current bot status"""
        stats = self.db.get_stats()

        print("\n" + "=" * 50)
        print("News Sentinel Bot - Status")
        print("=" * 50)
        print(f"Total articles: {stats['total_articles']}")
        print(f"Total mentions: {stats['total_mentions']}")
        print(f"Total alerts: {stats['total_alerts']}")
        print(f"Articles (24h): {stats['articles_24h']}")

        # Recent alerts
        alerts = self.alert_manager.get_recent_alerts(10)
        if alerts:
            print("\nRecent alerts:")
            for alert in alerts:
                print(f"  [{alert['severity'].upper()}] {alert['message']}")

        # Top mentioned companies
        top_companies = self.db.get_mention_counts(hours=24)
        if top_companies:
            print("\nTop mentioned (24h):")
            for company in top_companies[:5]:
                print(
                    f"  {company['company_name']} ({company['company_ticker']}): "
                    f"{company['count']} mentions"
                )

        print("=" * 50 + "\n")

    def add_company(self, ticker: str, names: str):
        """Add a company to the watchlist"""
        name_list = [n.strip() for n in names.split(",")]
        self.company_extractor.add_company(ticker.upper(), name_list)
        print(f"Added {ticker.upper()}: {name_list}")

    def reset_alerts(self):
        """Clear all alerts (use with caution)"""
        print("This will clear all alerts. Press Ctrl+C to cancel...")
        import time

        time.sleep(3)

        with self.db.get_connection() as conn:
            conn.execute("DELETE FROM alerts")
            conn.commit()
        print("Alerts cleared")


def main():
    parser = argparse.ArgumentParser(description="News Sentinel Bot")
    parser.add_argument(
        "-c", "--config", default="config/settings.yaml", help="Path to config file"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run the bot")
    run_parser.add_argument("--dry-run", action="store_true", help="Run without saving to database")

    # Status command
    subparsers.add_parser("status", help="Show bot status")

    # Watchlist commands
    watchlist_parser = subparsers.add_parser("watchlist", help="Manage watchlist")
    watchlist_sub = watchlist_parser.add_subparsers(dest="watchlist_cmd")

    add_parser = watchlist_sub.add_parser("add", help="Add company")
    add_parser.add_argument("ticker", help="Stock ticker")
    add_parser.add_argument("names", help="Company names (comma-separated)")

    # Schedule command (for cron)
    subparsers.add_parser("schedule", help="Run on schedule")

    # Reset command
    subparsers.add_parser("reset-alerts", help="Clear all alerts")

    args = parser.parse_args()

    # Setup logging
    setup_logging(verbose=args.verbose)

    # Change to bot directory
    bot_dir = Path(__file__).parent.parent
    os.chdir(bot_dir)

    # Create bot instance
    bot = NewsSentinelBot(args.config)

    # Execute command
    if args.command == "run":
        bot.run(dry_run=args.dry_run)
    elif args.command == "status":
        bot.show_status()
    elif args.command == "watchlist":
        if args.watchlist_cmd == "add":
            bot.add_company(args.ticker, args.names)
    elif args.command == "schedule":
        # Run once (designed to be called by cron/systemd timer)
        bot.run()
    elif args.command == "reset-alerts":
        bot.reset_alerts()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
