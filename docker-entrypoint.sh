#!/bin/bash
set -e

# News Sentinel Bot - Docker Entrypoint Script
# Handles different run modes: scraper, web, or custom commands

# =============================================================================
# Helper Functions
# =============================================================================

log_info() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] INFO: $1"
}

log_error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $1" >&2
}

wait_for_db() {
    # Wait for database file to be accessible (relevant when using shared volumes)
    local db_path="${NEWS_SENTINEL_DB_PATH:-/app/data/news_sentinel.db}"
    local db_dir=$(dirname "$db_path")

    if [ ! -d "$db_dir" ]; then
        log_info "Creating database directory: $db_dir"
        mkdir -p "$db_dir"
    fi

    log_info "Database path: $db_path"
}

# =============================================================================
# Environment Validation
# =============================================================================

validate_environment() {
    log_info "Validating environment..."

    # Check if Telegram is enabled but credentials are missing
    if [ "${NEWS_BOT_TELEGRAM_ENABLED:-false}" = "true" ]; then
        if [ -z "$NEWS_BOT_TELEGRAM_TOKEN" ] || [ -z "$NEWS_BOT_TELEGRAM_CHAT_ID" ]; then
            log_error "Telegram is enabled but NEWS_BOT_TELEGRAM_TOKEN or NEWS_BOT_TELEGRAM_CHAT_ID is not set"
            exit 1
        fi
        log_info "Telegram notifications: enabled"
    else
        log_info "Telegram notifications: disabled"
    fi

    # Check webhook configuration
    if [ "${NEWS_BOT_WEBHOOK_ENABLED:-false}" = "true" ]; then
        if [ -z "$NEWS_BOT_WEBHOOK_URL" ]; then
            log_error "Webhook is enabled but NEWS_BOT_WEBHOOK_URL is not set"
            exit 1
        fi
        log_info "Webhook notifications: enabled"
    else
        log_info "Webhook notifications: disabled"
    fi

    # Log API key status (don't log the actual key!)
    if [ -n "$NEWS_SENTINEL_API_KEY" ]; then
        log_info "API authentication: enabled"
    else
        log_info "API authentication: disabled (unauthenticated access allowed)"
    fi
}

# =============================================================================
# Run Modes
# =============================================================================

run_scraper() {
    log_info "Starting News Sentinel Bot scraper..."

    wait_for_db

    # Determine run mode: once, continuous, or schedule
    local mode="${SCRAPER_MODE:-once}"
    local interval="${SCRAPER_INTERVAL_SECONDS:-900}"  # Default: 15 minutes

    case "$mode" in
        once)
            log_info "Running scraper once..."
            python /app/src/main.py run
            ;;
        continuous)
            log_info "Running scraper in continuous mode (interval: ${interval}s)..."
            while true; do
                log_info "Starting scraper cycle..."
                python /app/src/main.py run || log_error "Scraper cycle failed"
                log_info "Sleeping for ${interval} seconds..."
                sleep "$interval"
            done
            ;;
        schedule)
            log_info "Running scraper on schedule..."
            python /app/src/main.py schedule
            ;;
        *)
            log_error "Unknown SCRAPER_MODE: $mode. Use 'once', 'continuous', or 'schedule'"
            exit 1
            ;;
    esac
}

run_web() {
    log_info "Starting News Sentinel Bot web dashboard..."

    wait_for_db

    local host="${WEB_HOST:-0.0.0.0}"
    local port="${WEB_PORT:-5000}"
    local workers="${WEB_WORKERS:-4}"

    log_info "Web dashboard listening on ${host}:${port}"

    # Use gunicorn for production, flask dev server for development
    if [ "${FLASK_ENV:-production}" = "development" ]; then
        log_info "Running in development mode (Flask dev server)"
        cd /app && python -m flask run --host="$host" --port="$port"
    else
        log_info "Running in production mode (Gunicorn with ${workers} workers)"
        cd /app && gunicorn \
            --bind "${host}:${port}" \
            --workers "$workers" \
            --timeout 120 \
            --access-logfile - \
            --error-logfile - \
            --capture-output \
            "web.app:app"
    fi
}

run_status() {
    log_info "Checking bot status..."
    wait_for_db
    python /app/src/main.py status
}

# =============================================================================
# Main Entry Point
# =============================================================================

main() {
    log_info "News Sentinel Bot starting..."
    log_info "Python version: $(python --version)"
    log_info "Working directory: $(pwd)"

    validate_environment

    # Handle different commands
    case "${1:-scraper}" in
        scraper)
            run_scraper
            ;;
        web)
            run_web
            ;;
        status)
            run_status
            ;;
        bash|sh)
            log_info "Starting shell..."
            exec /bin/bash
            ;;
        *)
            # Pass through any other command
            log_info "Executing custom command: $*"
            exec "$@"
            ;;
    esac
}

# Run main function with all arguments
main "$@"
