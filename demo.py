#!/usr/bin/env python3
"""
Quick demo of News Sentinel Bot pattern detection
"""

import sys

sys.path.insert(0, "src")

from datetime import datetime, timedelta
from company_extractor import CompanyExtractor, SentimentAnalyzer
from pattern_detector import PatternDetector, PatternAlert

# Demo watchlist
WATCHLIST = {
    "AAPL": ["Apple", "AAPL"],
    "TSLA": ["Tesla", "TSLA", "Elon Musk"],
    "NVDA": ["Nvidia", "NVDA"],
    "META": ["Meta", "Facebook", "META"],
}

# Demo articles (simulating news feed)
DEMO_ARTICLES = [
    {
        "title": "Apple Stock Surges to Record High on Strong Earnings",
        "content": "Apple Inc (AAPL) shares soared 8% today after reporting quarterly earnings that beat expectations. The iPhone maker reported revenue of $89.5 billion.",
        "source": "Reuters",
        "hours_ago": 1,
    },
    {
        "title": "Tesla Plunges on Production Concerns",
        "content": "Tesla stock plunged 12% today amid concerns about Model 3 production delays. Elon Musk addressed investors about manufacturing challenges.",
        "source": "Bloomberg",
        "hours_ago": 2,
    },
    {
        "title": "Apple Unveils New AI Features for iPhone",
        "content": "Apple announced groundbreaking AI capabilities for the upcoming iPhone 16. Analysts say this could drive significant upgrade cycle.",
        "source": "CNBC",
        "hours_ago": 3,
    },
    {
        "title": "Tesla Faces Investigation Over Autopilot Safety",
        "content": "Federal regulators opened an investigation into Tesla Autopilot following recent accidents. The probe could impact Tesla stock significantly.",
        "source": "Reuters",
        "hours_ago": 4,
    },
    {
        "title": "Nvidia Rally Continues on AI Boom",
        "content": "Nvidia shares reached new highs as demand for AI chips continues to surge. The company is dominating the data center market.",
        "source": "Bloomberg",
        "hours_ago": 5,
    },
    {
        "title": "Tesla Announces Major Layoffs",
        "content": "Tesla is cutting 10% of its workforce amid slowing demand. Elon Musk cited economic headwinds in an internal memo.",
        "source": "CNBC",
        "hours_ago": 6,
    },
    {
        "title": "Apple Expands into New Markets",
        "content": "Apple is reportedly planning to enter the automotive space with Project Titan. The company hired former Tesla executives.",
        "source": "Reuters",
        "hours_ago": 8,
    },
]


def main():
    print("=" * 60)
    print("📰 News Sentinel Bot - Demo")
    print("=" * 60)

    # Initialize extractor
    extractor = CompanyExtractor(WATCHLIST)

    # Initialize sentiment analyzer
    sentiment = SentimentAnalyzer(
        positive_words=["surge", "rally", "soar", "boom", "beat", "strong", "high", "gain"],
        negative_words=["plunge", "crash", "investigation", "layoffs", "concern", "delay", "probe"],
    )

    print("\n📝 Processing demo articles...")
    print("-" * 60)

    # Track mentions per company
    mentions = {}
    sentiments = {}

    for article in DEMO_ARTICLES:
        print(f"\n📄 {article['title']}")
        print(f"   Source: {article['source']} | {article['hours_ago']}h ago")

        # Extract companies
        matches = extractor.extract(article["content"])

        if matches:
            print(f"   🏢 Mentions: {', '.join(m.ticker for m in matches)}")

            # Track mentions
            for match in matches:
                if match.ticker not in mentions:
                    mentions[match.ticker] = []
                    sentiments[match.ticker] = []
                mentions[match.ticker].append(article["title"])
                sentiments[match.ticker].append(sentiment.analyze(article["content"]))

        # Analyze sentiment
        score = sentiment.analyze(article["content"])
        emoji = "😊" if score > 0.2 else "😞" if score < -0.2 else "😐"
        print(f"   {emoji} Sentiment: {score:+.2f}")

    print("\n" + "=" * 60)
    print("📊 Summary & Pattern Detection")
    print("=" * 60)

    # Show mention counts
    print("\n📈 Mention Counts (last 8 hours):")
    for ticker, articles in sorted(mentions.items(), key=lambda x: -len(x[1])):
        count = len(articles)
        print(f"   {ticker}: {count} articles")
        for title in articles:
            print(f"      • {title[:50]}...")

    # Show sentiment averages
    print("\n🎭 Average Sentiment:")
    for ticker, scores in sentiments.items():
        avg = sum(scores) / len(scores)
        emoji = "🟢" if avg > 0.2 else "🔴" if avg < -0.2 else "⚪"
        print(f"   {emoji} {ticker}: {avg:+.2f}")

    # Pattern detection
    print("\n🚨 Pattern Alerts:")
    alerts = []

    # Check for volume spikes
    for ticker, articles in mentions.items():
        if len(articles) >= 3:
            alert = PatternAlert(
                pattern_type="volume_spike",
                ticker=ticker,
                company_name=WATCHLIST[ticker][0],
                severity="high",
                message=f"{WATCHLIST[ticker][0]} ({ticker}): {len(articles)} articles in 8h",
                details={"articles": len(articles), "timeframe": "8h"},
            )
            alerts.append(alert)

    # Check for negative clusters
    for ticker, scores in sentiments.items():
        negative_count = sum(1 for s in scores if s < -0.2)
        if negative_count >= 2:
            alert = PatternAlert(
                pattern_type="negative_cluster",
                ticker=ticker,
                company_name=WATCHLIST[ticker][0],
                severity="high",
                message=f"{WATCHLIST[ticker][0]} ({ticker}): Negative news cluster detected",
                details={"negative_articles": negative_count},
            )
            alerts.append(alert)

    # Check for sentiment shifts
    for ticker, scores in sentiments.items():
        if len(scores) >= 2:
            recent = scores[: len(scores) // 2]
            older = scores[len(scores) // 2 :]
            recent_avg = sum(recent) / len(recent)
            older_avg = sum(older) / len(older) if older else 0
            shift = recent_avg - older_avg

            if abs(shift) > 0.3:
                direction = "positive" if shift > 0 else "negative"
                alert = PatternAlert(
                    pattern_type="sentiment_shift",
                    ticker=ticker,
                    company_name=WATCHLIST[ticker][0],
                    severity="medium",
                    message=f"{WATCHLIST[ticker][0]} ({ticker}): {direction.title()} sentiment shift",
                    details={"shift": round(shift, 2)},
                )
                alerts.append(alert)

    # Display alerts
    if alerts:
        for alert in alerts:
            emoji_map = {"high": "🚨", "medium": "⚠️", "low": "ℹ️"}
            type_emoji = {"volume_spike": "📈", "sentiment_shift": "🎭", "negative_cluster": "⚡"}

            emoji = emoji_map.get(alert.severity, "•")
            icon = type_emoji.get(alert.pattern_type, "📰")

            print(f"\n   {emoji} {icon} [{alert.severity.upper()}] {alert.pattern_type}")
            print(f"      {alert.message}")
    else:
        print("   No significant patterns detected")

    print("\n" + "=" * 60)
    print("Demo complete! Run 'python src/main.py run' for live data.")
    print("=" * 60)


if __name__ == "__main__":
    main()
