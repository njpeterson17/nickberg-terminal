"""
Nickberg Terminal - Main runner
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
from config_validator import validate_config
from backtester import Backtester

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


class NickbergTerminal:
    """Main bot class"""

    def __init__(self, config_path: str = "config/settings.yaml"):
        self.config_path = config_path
        self.config = self._load_config()

        # Initialize components
        self.db = Database(self.config["database"]["path"])

        # Merge database preferences with config file (database overrides config)
        self._merge_database_preferences()

        self.scraper_manager = ScraperManager(config_path)
        self.company_extractor = CompanyExtractor(self.config["companies"]["watchlist"])
        self.pattern_detector = PatternDetector(self.db, self.config["patterns"])
        self.alert_manager = AlertManager(self.config["alerts"], self.db)

        self.logger = get_logger(__name__)

    def _load_config(self) -> dict:
        """Load configuration from YAML"""
        with open(self.config_path) as f:
            return yaml.safe_load(f)

    def _merge_database_preferences(self):
        """
        Merge database preferences with config file settings.

        Database preferences override config file settings to allow runtime
        configuration changes via the web UI without modifying the config file.
        """
        try:
            # Get all preferences from database
            db_prefs = self.db.get_all_preferences()

            if not db_prefs:
                return  # No database preferences, use config file as-is

            # Merge watchlist (database overrides config)
            db_watchlist = db_prefs.get("watchlist")
            if db_watchlist and isinstance(db_watchlist, dict):
                self.config["companies"]["watchlist"] = db_watchlist

            # Merge thresholds into patterns config
            db_thresholds = db_prefs.get("thresholds")
            if db_thresholds and isinstance(db_thresholds, dict):
                if "volume_spike" in db_thresholds:
                    self.config["patterns"]["volume_spike_threshold"] = db_thresholds[
                        "volume_spike"
                    ]
                if "min_articles" in db_thresholds:
                    self.config["patterns"]["min_articles_for_alert"] = db_thresholds[
                        "min_articles"
                    ]
                if "sentiment_shift" in db_thresholds:
                    self.config["patterns"]["sentiment_shift_threshold"] = db_thresholds[
                        "sentiment_shift"
                    ]

            # Merge alert channel settings
            db_channels = db_prefs.get("alert_channels")
            if db_channels and isinstance(db_channels, dict):
                if "telegram" in db_channels:
                    if "telegram" not in self.config["alerts"]:
                        self.config["alerts"]["telegram"] = {}
                    self.config["alerts"]["telegram"]["enabled"] = db_channels["telegram"]
                if "webhook" in db_channels:
                    if "webhook" not in self.config["alerts"]:
                        self.config["alerts"]["webhook"] = {}
                    self.config["alerts"]["webhook"]["enabled"] = db_channels["webhook"]
                if "file" in db_channels:
                    if "file" not in self.config["alerts"]:
                        self.config["alerts"]["file"] = {}
                    self.config["alerts"]["file"]["enabled"] = db_channels["file"]
                if "console" in db_channels:
                    self.config["alerts"]["console"] = db_channels["console"]

            # Store severity routing and company preferences in config for alert manager
            db_routing = db_prefs.get("severity_routing")
            if db_routing:
                self.config["alerts"]["severity_routing"] = db_routing

            db_company_prefs = db_prefs.get("company_preferences")
            if db_company_prefs:
                self.config["alerts"]["company_preferences"] = db_company_prefs

        except Exception as e:
            # Log but don't fail - use config file values if database fails
            logger = get_logger(__name__)
            logger.warning("Failed to merge database preferences", extra={"error": str(e)})

    def run(self, dry_run: bool = False):
        """Run one cycle of the bot"""
        self.logger.info("Starting Nickberg Terminal cycle", extra={"dry_run": dry_run})

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
        print("Nickberg Terminal - Status")
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


def run_backtest(args, bot: NickbergTerminal):
    """Run backtesting with the provided arguments."""
    logger = get_logger(__name__)

    # Parse dates
    try:
        start_date = datetime.strptime(args.start, "%Y-%m-%d")
        end_date = datetime.strptime(args.end, "%Y-%m-%d")
    except ValueError as e:
        print(f"Error: Invalid date format. Use YYYY-MM-DD. ({e})")
        sys.exit(1)

    if start_date > end_date:
        print("Error: Start date must be before end date.")
        sys.exit(1)

    # Create backtester
    backtester = Backtester(bot.db, bot.config)

    print(f"\nRunning backtest from {args.start} to {args.end}...")
    print(f"Interval: {args.interval} hours\n")

    # Run backtest
    report = backtester.run(
        start_date=start_date,
        end_date=end_date,
        interval_hours=args.interval,
    )

    # Print summary
    backtester.print_summary()

    # Export results if output path provided
    if args.output:
        # Determine format from file extension if not explicitly set
        output_format = args.format
        if args.output.endswith(".csv"):
            output_format = "csv"
        elif args.output.endswith(".json"):
            output_format = "json"

        if backtester.export_results(args.output, format=output_format):
            print(f"Results exported to: {args.output}")
        else:
            print(f"Failed to export results to: {args.output}")
            sys.exit(1)
    else:
        # Print JSON to stdout if no output file
        print("\nFull report (JSON):")
        print(json.dumps(backtester.generate_report(), indent=2))


def main():
    parser = argparse.ArgumentParser(description="Nickberg Terminal")
    parser.add_argument(
        "-c", "--config", default="config/settings.yaml", help="Path to config file"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument(
        "--skip-validation", action="store_true", help="Skip configuration validation"
    )

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

    # Validate command
    subparsers.add_parser("validate", help="Validate configuration file")

    # Backtest command
    backtest_parser = subparsers.add_parser("backtest", help="Run backtesting on historical data")
    backtest_parser.add_argument(
        "--start",
        required=True,
        help="Start date for backtest (YYYY-MM-DD)",
    )
    backtest_parser.add_argument(
        "--end",
        required=True,
        help="End date for backtest (YYYY-MM-DD)",
    )
    backtest_parser.add_argument(
        "--output",
        "-o",
        help="Output file path for results (JSON or CSV based on extension)",
    )
    backtest_parser.add_argument(
        "--interval",
        type=int,
        default=6,
        help="Interval in hours between pattern detection checks (default: 6)",
    )
    backtest_parser.add_argument(
        "--format",
        choices=["json", "csv"],
        default="json",
        help="Output format (default: json)",
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging(verbose=args.verbose)
    logger = get_logger(__name__)

    # Change to bot directory
    bot_dir = Path(__file__).parent.parent
    os.chdir(bot_dir)

    # Handle validate command separately (before creating bot)
    if args.command == "validate":
        result = validate_config(args.config)
        if result.is_valid:
            print(f"Configuration file '{args.config}' is valid.")
            sys.exit(0)
        else:
            print(result, file=sys.stderr)
            sys.exit(1)

    # Validate configuration on startup (unless skipped)
    if not args.skip_validation:
        result = validate_config(args.config)
        if not result.is_valid:
            print(result, file=sys.stderr)
            sys.exit(1)
        logger.info("Configuration validation passed", extra={"config_path": args.config})

    # Create bot instance
    bot = NickbergTerminal(args.config)

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
    elif args.command == "backtest":
        run_backtest(args, bot)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
