# News Sentinel Bot - Docker Image
# Production-ready container for news scraping and monitoring

# =============================================================================
# Stage 1: Build dependencies
# =============================================================================
FROM python:3.12-slim AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# =============================================================================
# Stage 2: Production image
# =============================================================================
FROM python:3.12-slim

# Labels for container identification
LABEL maintainer="News Sentinel Bot"
LABEL description="News scraping and monitoring bot with web dashboard"
LABEL version="1.0"

# Security: Create non-root user
RUN groupadd --gid 1000 newsbot && \
    useradd --uid 1000 --gid 1000 --shell /bin/bash --create-home newsbot

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Set environment variables
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="/app/src" \
    # Flask configuration
    FLASK_APP=web/app.py \
    FLASK_ENV=production \
    # Application defaults
    NEWS_SENTINEL_CONFIG=/app/config/settings.yaml \
    NEWS_SENTINEL_LOG_LEVEL=INFO

# Set working directory
WORKDIR /app

# Create necessary directories with correct permissions
RUN mkdir -p /app/data /app/logs /app/config && \
    chown -R newsbot:newsbot /app

# Copy application code
COPY --chown=newsbot:newsbot src/ /app/src/
COPY --chown=newsbot:newsbot web/ /app/web/
COPY --chown=newsbot:newsbot config/ /app/config/
COPY --chown=newsbot:newsbot requirements.txt /app/

# Copy entrypoint script
COPY --chown=newsbot:newsbot docker-entrypoint.sh /app/
RUN chmod +x /app/docker-entrypoint.sh

# Switch to non-root user
USER newsbot

# Expose web dashboard port
EXPOSE 5000

# Health check for the web dashboard
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5000/ || exit 1

# Default entrypoint
ENTRYPOINT ["/app/docker-entrypoint.sh"]

# Default command (can be overridden)
CMD ["scraper"]
