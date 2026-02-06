/**
 * Nickberg Terminal - Dashboard JavaScript
 */

// Check if Chart.js is available
const isChartAvailable = typeof Chart !== 'undefined';
if (!isChartAvailable) {
    console.warn('Chart.js not loaded - charts will be disabled');
}

// Global chart instance
let mainChart = null;
let sentimentChart = null;
let currentChartType = 'mentions';

// Price cache
let priceCache = {};
const PRICE_CACHE_TTL = 60000; // 1 minute

// =============================================================================
// Preload Cache for Watchlist Data
// =============================================================================
let preloadedStockData = {};
let preloadInProgress = false;

// =============================================================================
// Lazy Loading State for Stock Modal Tabs
// =============================================================================
const tabLoadState = {
    overview: false,
    valuation: false,
    financials: false,
    ownership: false,
    analysts: false,
    news: false,
    chart: false
};

// Current stock data for lazy loading
let currentStockData = null;

// =============================================================================
// Search Debounce
// =============================================================================
let searchDebounceTimer = null;
const SEARCH_DEBOUNCE_MS = 300;

// =============================================================================
// Web Worker for Background Preloading
// =============================================================================
let preloadWorker = null;

/**
 * Initialize the Web Worker for background data fetching
 */
function initPreloadWorker() {
    if (typeof Worker === 'undefined') {
        console.warn('[Worker] Web Workers not supported, using fallback');
        return false;
    }

    try {
        preloadWorker = new Worker('/static/js/preload-worker.js');

        preloadWorker.onmessage = function(e) {
            const { type, data } = e.data;

            switch (type) {
                case 'preload_complete':
                    console.log('[Worker] Preload complete');
                    preloadedStockData = { ...preloadedStockData, ...data };
                    preloadInProgress = false;
                    break;

                case 'price_update':
                    // Update price cache with worker data
                    if (data.price) {
                        priceCache[data.ticker] = data.price;
                    }
                    break;

                case 'batch_prices':
                    // Update multiple prices from worker
                    Object.assign(priceCache, data);
                    updatePriceDisplay(data);
                    break;

                case 'batch_error':
                case 'price_error':
                    console.warn('[Worker] Price fetch error:', data.error);
                    break;
            }
        };

        preloadWorker.onerror = function(error) {
            console.error('[Worker] Error:', error);
        };

        console.log('[Worker] Preload worker initialized');
        return true;
    } catch (error) {
        console.warn('[Worker] Failed to initialize:', error);
        return false;
    }
}

/**
 * Use Web Worker to preload data if available, otherwise use fallback
 */
function preloadViaWorker(tickers) {
    if (preloadWorker) {
        preloadWorker.postMessage({
            type: 'preload',
            data: { tickers }
        });
        return true;
    }
    return false;
}

// Settings state
let settingsState = {
    watchlist: {},
    alert_channels: {},
    severity_routing: {},
    thresholds: {},
    company_preferences: {},
    isDirty: false
};

// Debounce timer for auto-save
let saveDebounceTimer = null;

// =============================================================================
// WebSocket Real-Time Price Updates
// =============================================================================
let socket = null;
let wsConnected = false;
let wsReconnectAttempts = 0;
const WS_MAX_RECONNECT_ATTEMPTS = 5;
const WS_RECONNECT_DELAY = 3000;
let subscribedTickers = [];
let previousPrices = {}; // Track previous prices for flash animation

// Initialize dashboard
document.addEventListener('DOMContentLoaded', () => {
    // Hide any chart elements (replaced with ticker)
    hideChartElements();

    initDashboard();
    setupEventListeners();
    setupSettingsEventListeners();
    initTicker();
    initMobileFeatures();

    // Initialize Web Worker for background preloading
    initPreloadWorker();

    // Initialize WebSocket connection for real-time price updates
    initWebSocket();

    // Preload watchlist data in background after initial load
    setTimeout(() => {
        preloadWatchlistData();
    }, 2000);

    // Auto-refresh every 60 seconds
    setInterval(refreshData, 60000);

    // Refresh prices every 60 seconds
    setInterval(() => {
        const tickers = Array.from(document.querySelectorAll('.company-item'))
            .map(el => el.dataset.ticker)
            .filter(Boolean);

        if (tickers.length > 0) {
            loadPrices(tickers);
        }
    }, 60000);

    // Refresh preloaded data every 5 minutes
    setInterval(() => {
        preloadWatchlistData();
    }, 300000);
});

// Mobile-specific features
function initMobileFeatures() {
    // Detect touch devices
    const isTouchDevice = window.matchMedia('(pointer: coarse)').matches;
    
    if (isTouchDevice) {
        document.body.classList.add('touch-device');
        
        // Add swipe gesture support for tabs
        let touchStartX = 0;
        let touchEndX = 0;
        
        document.addEventListener('touchstart', (e) => {
            touchStartX = e.changedTouches[0].screenX;
        }, { passive: true });
        
        document.addEventListener('touchend', (e) => {
            touchEndX = e.changedTouches[0].screenX;
            handleSwipe();
        }, { passive: true });
        
        function handleSwipe() {
            const swipeThreshold = 50;
            const diff = touchStartX - touchEndX;
            
            if (Math.abs(diff) > swipeThreshold) {
                const tabs = document.querySelectorAll('.nav-tab');
                const activeTab = document.querySelector('.nav-tab.active');
                const currentIndex = Array.from(tabs).indexOf(activeTab);
                
                if (diff > 0 && currentIndex < tabs.length - 1) {
                    // Swipe left - next tab
                    tabs[currentIndex + 1].click();
                } else if (diff < 0 && currentIndex > 0) {
                    // Swipe right - previous tab
                    tabs[currentIndex - 1].click();
                }
            }
        }
    }
    
    // Handle viewport changes
    window.addEventListener('resize', debounce(() => {
        adjustLayoutForScreenSize();
    }, 250));
    
    // Initial layout adjustment
    adjustLayoutForScreenSize();
}

// Adjust layout based on screen size
function adjustLayoutForScreenSize() {
    const width = window.innerWidth;
    const isMobile = width <= 768;
    
    // Adjust chart heights for mobile
    if (isMobile && typeof Chart !== 'undefined') {
        Chart.defaults.responsive = true;
        Chart.defaults.maintainAspectRatio = false;
    }
}

// Debounce utility for performance
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

async function initDashboard() {
    await loadAllData();
    initBlueskyFeed();
    // Delay economic calendar init to ensure DOM is ready
    setTimeout(initEconomicCalendar, 100);
}

// Bluesky Financial Accounts to follow
const BLUESKY_FINANCIAL_ACCOUNTS = [
    // Options & Flow
    { handle: 'unusualwhales.bsky.social', name: 'Unusual Whales', display: 'Unusual Whales' },
    { handle: 'spotgamma.bsky.social', name: 'SpotGamma', display: 'SpotGamma' },
    { handle: 'ladebackk.bsky.social', name: 'Assad Tannous', display: 'Assad Tannous' },
    { handle: 'jarvisflow.bsky.social', name: 'JarvisFlow', display: 'JarvisFlow' },
    { handle: 'darkpoolmarkets.bsky.social', name: 'Dark Pool', display: 'Dark Pool' },
    { handle: 'chriswhittall.bsky.social', name: 'Chris Whittall', display: 'Chris Whittall' },
    
    // Macro & Markets
    { handle: 'strazza.bsky.social', name: 'Strazza', display: 'Strazza' },
    { handle: 'carnage4life.bsky.social', name: 'Carnage4Life', display: 'Carnage4Life' },
    { handle: 'truflation.bsky.social', name: 'Truflation', display: 'Truflation' },
    { handle: 'quiverquant.bsky.social', name: 'Quiver Quantitative', display: 'Quiver Quant' },
    { handle: 'federalreserve.gov', name: 'Federal Reserve', display: 'Fed Reserve' },
    { handle: 'elerianm.bsky.social', name: 'Mohamed El-Erian', display: 'El-Erian' },
    { handle: 'claudia-sahm.bsky.social', name: 'Claudia Sahm', display: 'Claudia Sahm' },
    { handle: 'josephpolitano.bsky.social', name: 'Joey Politano', display: 'Joey Politano' },
    { handle: 'darioperkins.bsky.social', name: 'Dario Perkins', display: 'Dario Perkins' },
    
    // News & Media
    { handle: 'benzinga.bsky.social', name: 'Benzinga', display: 'Benzinga' },
    { handle: 'marketwatch.bsky.social', name: 'MarketWatch', display: 'MarketWatch' },
    { handle: 'morningbrew.bsky.social', name: 'Morning Brew', display: 'Morning Brew' },
    { handle: 'theblock.bsky.social', name: 'The Block', display: 'The Block' },
    { handle: 'coindesk.bsky.social', name: 'CoinDesk', display: 'CoinDesk' },
    { handle: 'bloomberg.com', name: 'Bloomberg', display: 'Bloomberg' },
    { handle: 'reuters.com', name: 'Reuters', display: 'Reuters' },
    { handle: 'financialtimes.com', name: 'Financial Times', display: 'FT' },
    { handle: 'cnbc.com', name: 'CNBC', display: 'CNBC' },
    { handle: 'wsj.com', name: 'WSJ', display: 'WSJ' },
    
    // Trading & Technical Analysis
    { handle: 'stocktwits.bsky.social', name: 'StockTwits', display: 'StockTwits' },
    { handle: 'markminervini.bsky.social', name: 'Mark Minervini', display: 'Mark Minervini' },
    { handle: 'tradingview.bsky.social', name: 'TradingView', display: 'TradingView' },
    { handle: '0dte.bsky.social', name: '0DTE', display: '0DTE' },
    { handle: 'cboe.bsky.social', name: 'CBOE', display: 'CBOE' },

    { handle: 'brianferoldi.bsky.social', name: 'Brian Feroldi', display: 'Brian Feroldi' },

    { handle: 'mindmathmoney.com', name: 'Mind Math Money', display: 'MMM' },
    { handle: 'dkellercmt.bsky.social', name: 'David Keller', display: 'David Keller' },
    { handle: 'martialchartsfx.bsky.social', name: 'Martial Charts', display: 'Martial Charts' },
    { handle: 'intradaytrader.bsky.social', name: 'Intraday Trader', display: 'Intraday' },
    { handle: 'jamtrades.bsky.social', name: 'jam trades', display: 'jam trades' },
    
    // Sentiment & Data
    { handle: 'sentiment.bsky.social', name: 'Sentiment', display: 'Sentiment' },
    { handle: 'fintwit.bsky.social', name: 'FinTwit', display: 'FinTwit' },

    { handle: 'finchat.bsky.social', name: 'FinChat', display: 'FinChat' },
    { handle: 'sentimentrader.bsky.social', name: 'SentimenTrader', display: 'SentimenTrader' },
    { handle: 'topdowncharts.bsky.social', name: 'Topdown Charts', display: 'Topdown Charts' },
    { handle: 'marketsentiment.bsky.social', name: 'Market Sentiment', display: 'Mkt Sentiment' },
    { handle: 'hmeisler.bsky.social', name: 'Helene Meisler', display: 'Helene Meisler' },
    
    // Crypto & Web3
    { handle: 'sassal0x.bsky.social', name: 'sassal.eth', display: 'sassal' },
    { handle: 'dcinvestor.bsky.social', name: 'DCinvestor', display: 'DCinvestor' },
    { handle: 'cryptocobain.bsky.social', name: 'Crypto Cobain', display: 'Cobain' },
    { handle: 'calle.bsky.social', name: 'Calle', display: 'Calle' },
    { handle: 'phenotype.dev', name: 'Mark Glasgow', display: 'Phenotype' },
    { handle: 'apoorv.xyz', name: 'Apoorv Lathey', display: 'Apoorv' }
];

// Cache for Bluesky posts
let blueskyCache = {
    posts: [],
    timestamp: 0,
    dids: {}  // Cache for DID lookups
};

const BLUESKY_CACHE_TTL = 120000; // 2 minutes

// Initialize Bluesky Feed
async function initBlueskyFeed() {
    const feedContainer = document.getElementById('blueskyFeedFull');
    if (!feedContainer) return;
    
    // Load posts immediately
    await loadBlueskyFeed(feedContainer);
    
    // Refresh every 2 minutes
    setInterval(() => {
        loadBlueskyFeed(feedContainer);
    }, BLUESKY_CACHE_TTL);
}

// Load Bluesky posts from financial accounts
async function loadBlueskyFeed(container) {
    // Check cache first
    if (Date.now() - blueskyCache.timestamp < BLUESKY_CACHE_TTL && blueskyCache.posts.length > 0) {
        renderBlueskyPosts(container, blueskyCache.posts);
        return;
    }
    
    try {
        const allPosts = [];
        
        // Fetch posts from each account
        for (const account of BLUESKY_FINANCIAL_ACCOUNTS) {
            try {
                const posts = await fetchBlueskyPosts(account.handle, 5);
                allPosts.push(...posts);
            } catch (error) {
                console.warn(`Failed to fetch posts for ${account.handle}:`, error);
            }
        }
        
        // Sort by date (newest first)
        allPosts.sort((a, b) => new Date(b.indexedAt) - new Date(a.indexedAt));
        
        // Take top 120 posts
        const topPosts = allPosts.slice(0, 120);
        
        // Update cache
        blueskyCache.posts = topPosts;
        blueskyCache.timestamp = Date.now();
        
        // Render
        renderBlueskyPosts(container, topPosts);
        
    } catch (error) {
        console.error('Error loading Bluesky feed:', error);
        showBlueskyError(container, 'Failed to load Bluesky feed. Will retry shortly.');
    }
}

// Fetch posts from a specific Bluesky account
async function fetchBlueskyPosts(handle, limit = 5) {
    // First, resolve the handle to a DID
    let did = blueskyCache.dids[handle];
    if (!did) {
        const resolveResponse = await fetch(`https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle?handle=${encodeURIComponent(handle)}`);
        if (!resolveResponse.ok) throw new Error(`Failed to resolve handle: ${handle}`);
        const resolveData = await resolveResponse.json();
        did = resolveData.did;
        blueskyCache.dids[handle] = did;
    }
    
    // Fetch author's feed
    const feedResponse = await fetch(`https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?actor=${encodeURIComponent(did)}&limit=${limit}`);
    if (!feedResponse.ok) throw new Error(`Failed to fetch feed for: ${handle}`);
    const feedData = await feedResponse.json();
    
    // Get account info
    const account = BLUESKY_FINANCIAL_ACCOUNTS.find(a => a.handle === handle) || { name: handle, display: handle };
    
    // Process posts
    return feedData.feed.map(item => {
        const post = item.post;
        return {
            uri: post.uri,
            cid: post.cid,
            text: post.record?.text || '',
            createdAt: post.record?.createdAt,
            indexedAt: post.indexedAt,
            author: {
                did: post.author.did,
                handle: post.author.handle,
                displayName: post.author.displayName || account.display,
                avatar: post.author.avatar,
                name: account.name
            },
            embed: post.embed,
            replyCount: post.replyCount || 0,
            repostCount: post.repostCount || 0,
            likeCount: post.likeCount || 0,
            isRepost: item.reason?.$type === 'app.bsky.feed.defs#reasonRepost',
            repostedBy: item.reason?.by
        };
    });
}

// Helper: Convert Bluesky AT URI to web URL
function getBlueskyPostUrl(uri, handle) {
    // at://did:plc:xyz/app.bsky.feed.post/123 → https://bsky.app/profile/handle/post/123
    const match = uri.match(/at:\/\/did:plc:([^/]+)\/app\.bsky\.feed\.post\/(.+)/);
    if (match) {
        return `https://bsky.app/profile/${handle}/post/${match[2]}`;
    }
    return `https://bsky.app/profile/${handle}`;
}

// Render Bluesky posts to the container - vertically scrolling
function renderBlueskyPosts(container, posts) {
    if (!posts || posts.length === 0) {
        container.innerHTML = `
            <div class="bluesky-empty">
                <i class="fas fa-cloud"></i>
                <span>No posts available</span>
            </div>
        `;
        return;
    }
    
    // Build posts HTML
    const postsHtml = posts.map(post => {
        const timeAgo = formatTimeAgo(new Date(post.indexedAt));
        const avatarUrl = post.author.avatar || '';
        const initial = (post.author.displayName || post.author.handle).charAt(0).toUpperCase();
        const postUrl = getBlueskyPostUrl(post.uri, post.author.handle);
        
        // Highlight stock tickers (clickable)
        const highlightedText = highlightTickersClickable(escapeHtml(post.text));
        
        // Compact engagement stats
        const engagementHtml = [];
        if (post.likeCount > 0) engagementHtml.push(`<i class="far fa-heart"></i> ${formatCount(post.likeCount)}`);
        if (post.repostCount > 0) engagementHtml.push(`<i class="fas fa-retweet"></i> ${formatCount(post.repostCount)}`);
        
        return `
            <a href="${postUrl}" target="_blank" rel="noopener" class="bluesky-post-link">
                <div class="bluesky-post" data-uri="${post.uri}">
                    <div class="bluesky-post-header">
                        <div class="bluesky-avatar">
                            ${avatarUrl ? `<img src="${avatarUrl}" alt="" loading="lazy">` : initial}
                        </div>
                        <div class="bluesky-user-info">
                            <div class="bluesky-display-name">${escapeHtml(post.author.displayName || post.author.handle)}</div>
                            <div class="bluesky-meta">
                                <span class="bluesky-handle">@${post.author.handle}</span>
                                <span class="bluesky-dot">·</span>
                                <span class="bluesky-timestamp">${timeAgo}</span>
                            </div>
                        </div>
                    </div>
                    <div class="bluesky-post-content">${highlightedText}</div>
                    ${engagementHtml.length > 0 ? `<div class="bluesky-engagement">${engagementHtml.join(' ')}</div>` : ''}
                </div>
            </a>
        `;
    }).join('');
    
    // Wrap in scrolling container with duplicated content for seamless loop
    container.innerHTML = `
        <div class="bluesky-scroll-container">
            <div class="bluesky-scroll-content">
                ${postsHtml}
                ${postsHtml}
            </div>
        </div>
    `;
}

// Show error state
function showBlueskyError(container, message) {
    container.innerHTML = `
        <div class="bluesky-error">
            <i class="fas fa-exclamation-circle"></i>
            <span>${message}</span>
        </div>
    `;
}

// Helper: Format time ago
function formatTimeAgo(date) {
    const now = new Date();
    const diff = now - date;
    const minutes = Math.floor(diff / 60000);
    const hours = Math.floor(diff / 3600000);
    const days = Math.floor(diff / 86400000);
    
    if (minutes < 1) return 'now';
    if (minutes < 60) return `${minutes}m`;
    if (hours < 24) return `${hours}h`;
    if (days < 7) return `${days}d`;
    return date.toLocaleDateString();
}

// Helper: Format count (1.2k, 3.4M, etc.)
function formatCount(count) {
    if (!count || count === 0) return '';
    if (count < 1000) return count.toString();
    if (count < 1000000) return (count / 1000).toFixed(1) + 'k';
    return (count / 1000000).toFixed(1) + 'M';
}

// Helper: Escape HTML
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Helper: Highlight stock tickers ($TSLA, $AAPL, etc.)
function highlightTickers(text) {
    if (!text) return '';
    // Match $TICKER patterns
    return text.replace(/\$([A-Za-z]{1,5})/g, '<span class="ticker">$$$1</span>');
}

function setupEventListeners() {
    // Refresh button
    document.getElementById('refreshBtn').addEventListener('click', () => {
        refreshData();
    });
    
    // Run bot button
    document.getElementById('runBotBtn').addEventListener('click', runBot);
    
    // Command line input
    const commandInput = document.getElementById('commandInput');
    if (commandInput) {
        commandInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                executeCommand();
            }
        });
        // Auto-focus on page load
        commandInput.focus();
    }
}

// Execute command from command line
async function executeCommand() {
    console.log('[Command] executeCommand called');
    const input = document.getElementById('commandInput');
    if (!input) {
        console.error('[Command] Input element not found!');
        return;
    }
    
    const command = input.value.trim().toUpperCase();
    console.log(`[Command] Raw input: "${input.value}", Processed: "${command}"`);
    
    if (!command) {
        console.log('[Command] Empty command, ignoring');
        return;
    }
    
    // Check if it's a ticker symbol (1-5 uppercase letters)
    const tickerRegex = /^[A-Z]{1,5}$/;
    const isTicker = tickerRegex.test(command);
    console.log(`[Command] Is ticker: ${isTicker}, Regex test: ${tickerRegex.test(command)}`);
    
    if (isTicker) {
        console.log(`[Command] Looking up stock: ${command}`);
        await lookupStock(command);
    } else if (command === 'HELP' || command === '?') {
        console.log('[Command] Showing help');
        showHelpOverlay();
    } else if (command === 'REFRESH' || command === 'R') {
        console.log('[Command] Refreshing data');
        refreshData();
    } else if (command === 'CLEAR') {
        console.log('[Command] Clearing input');
        input.value = '';
        return; // Don't clear again below
    } else {
        console.log(`[Command] Unknown command: ${command}`);
        showToast(`Unknown command: ${command}`, 'error');
    }
    
    // Clear input after command execution
    input.value = '';
    console.log('[Command] Input cleared');
}

// Look up a stock ticker
async function lookupStock(symbol) {
    try {
        console.log(`[Stock Lookup] Opening modal for ${symbol}`);
        showToast(`Looking up ${symbol}...`, 'info');
        
        // Open the stock detail modal
        await openStockModal(symbol);
        
        // Also add to watchlist
        await addToWatchlist(symbol);
        
        showToast(`${symbol} loaded successfully`, 'success');
    } catch (error) {
        console.error('Error looking up stock:', error);
        showToast(`Error looking up ${symbol}`, 'error');
    }
}

// Display stock details in the panel
function displayStockDetails(data) {
    const panel = document.getElementById('stockDetailsPanel');
    if (!panel) {
        console.error('[Stock Details] Panel not found');
        return;
    }
    
    console.log('[Stock Details] Displaying data for', data.ticker);
    
    // Update main info
    document.getElementById('detailSymbol').textContent = data.ticker;
    document.getElementById('detailName').textContent = data.name || data.ticker;
    
    // Update price
    const priceEl = document.getElementById('detailPrice');
    priceEl.textContent = `$${data.price.toFixed(2)}`;
    
    // Update change
    const changeEl = document.getElementById('detailChange');
    const isUp = data.change >= 0;
    const arrow = isUp ? '▲' : '▼';
    changeEl.textContent = `${arrow} ${data.change >= 0 ? '+' : ''}${data.change_amount.toFixed(2)} (${data.change >= 0 ? '+' : ''}${data.change.toFixed(2)}%)`;
    changeEl.className = `stock-detail-change ${isUp ? 'up' : 'down'}`;
    
    // Update stats
    document.getElementById('detailHigh').textContent = data.day_high.toFixed(2);
    document.getElementById('detailLow').textContent = data.day_low.toFixed(2);
    document.getElementById('detailVolume').textContent = formatVolume(data.volume);
    document.getElementById('detailAvgVol').textContent = formatVolume(data.avg_volume);
    document.getElementById('detailMarketCap').textContent = data.market_cap;
    document.getElementById('detailPE').textContent = data.pe_ratio;
    
    // Update mentions
    document.getElementById('detailMentionsCount').textContent = data.mentions_count;
    const mentionsList = document.getElementById('detailMentionsList');
    const mentionsSection = document.getElementById('detailMentionsSection');
    
    if (data.mentions && data.mentions.length > 0) {
        mentionsSection.style.display = 'block';
        mentionsList.innerHTML = data.mentions.map(mention => `
            <div class="stock-mention-item">
                <span class="stock-mention-title" title="${mention.title}">${mention.title}</span>
                <span class="stock-mention-source">${mention.source}</span>
                <span class="stock-mention-sentiment ${mention.sentiment}">${mention.sentiment.toUpperCase()}</span>
            </div>
        `).join('');
    } else {
        mentionsSection.style.display = 'none';
    }
    
    // Show panel
    panel.style.display = 'block';
    
    // Scroll to panel
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// Close stock details panel
function closeStockDetails() {
    const panel = document.getElementById('stockDetailsPanel');
    if (panel) {
        panel.style.display = 'none';
    }
    // Focus back on command input
    const input = document.getElementById('commandInput');
    if (input) input.focus();
}

// Format volume numbers
function formatVolume(num) {
    if (num >= 1000000000) {
        return (num / 1000000000).toFixed(2) + 'B';
    } else if (num >= 1000000) {
        return (num / 1000000).toFixed(1) + 'M';
    } else if (num >= 1000) {
        return (num / 1000).toFixed(1) + 'K';
    }
    return num.toString();
}

// Add ticker to watchlist
async function addToWatchlist(symbol) {
    try {
        const response = await fetchWithTimeout('/api/watchlist', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                action: 'add',
                ticker: symbol,
                names: [symbol]
            })
        });
        if (response.ok) {
            console.log(`[Watchlist] Added ${symbol}`);
            await loadMarketMonitor();
        }
    } catch (error) {
        console.error('Error adding to watchlist:', error);
    }
}

// Helper function for fetch with timeout and error handling
async function fetchWithTimeout(url, options = {}, timeout = 10000) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeout);
    
    try {
        const response = await fetch(url, {
            ...options,
            signal: controller.signal
        });
        clearTimeout(timeoutId);
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        return response;
    } catch (error) {
        clearTimeout(timeoutId);
        
        if (error.name === 'AbortError') {
            throw new Error('Request timed out - server may be slow or unreachable');
        } else if (error.name === 'TypeError' && error.message.includes('fetch')) {
            throw new Error('Cannot connect to server - please check if the backend is running on port 5000');
        }
        
        throw error;
    }
}

async function loadAllData() {
    try {
        showStatus('loading');
        
        // Test connection first
        try {
            await fetchWithTimeout('/api/stats', {}, 5000);
        } catch (connError) {
            console.error('Connection test failed:', connError);
            showToast(connError.message, 'error');
            showStatus('error');
            return;
        }
        
        await Promise.all([
            loadStats(),
            loadMarketMonitor(),
            loadArticles(),
            loadSentiment()
        ]);
        
        updateLastUpdated();
        showStatus('ready');
    } catch (error) {
        console.error('Error loading data:', error);
        showToast('Error loading data: ' + error.message, 'error');
        showStatus('error');
    }
}

async function refreshData() {
    const btn = document.getElementById('refreshBtn');
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Refreshing...';
    
    await loadAllData();
    
    btn.disabled = false;
    btn.innerHTML = '<i class="fas fa-sync-alt"></i> Refresh';
    showToast('Data refreshed', 'success');
}

// Load statistics
async function loadStats() {
    const response = await fetchWithTimeout('/api/stats');
    const stats = await response.json();
    
    document.getElementById('totalArticles').textContent = formatNumber(stats.total_articles);
    document.getElementById('totalMentions').textContent = formatNumber(stats.total_mentions);
    document.getElementById('totalAlerts').textContent = formatNumber(stats.total_alerts);
    document.getElementById('articles24h').textContent = formatNumber(stats.articles_24h);
}

// Load Market Monitor data
async function loadMarketMonitor() {
    try {
        // Fetch index data (using SPY, QQQ, DIA as proxies)
        const indices = ['SPY', 'QQQ', 'DIA', 'IWM'];
        const indexData = await fetchLatestPrices(indices);
        
        // Map to index names
        const indexMap = {
            'SPY': { name: 'SPX', label: 'S&P 500' },
            'QQQ': { name: 'IXIC', label: 'Nasdaq' },
            'DIA': { name: 'DJI', label: 'Dow Jones' },
            'IWM': { name: 'RUT', label: 'Russell 2000' }
        };
        
        const indicesList = document.getElementById('indicesList');
        if (indicesList) {
            indicesList.innerHTML = Object.entries(indexMap).map(([etf, info]) => {
                const data = indexData[etf] || {};
                const price = data.price || '--.--';
                const change = data.change_pct || 0;
                const isUp = change > 0;
                const isDown = change < 0;
                const changeClass = isUp ? 'up' : isDown ? 'down' : 'flat';
                const arrow = isUp ? '▲' : isDown ? '▼' : '—';
                const changeStr = change ? `${arrow}${Math.abs(change).toFixed(2)}%` : '--.--';
                
                return `
                    <div class="market-item" title="${info.label}" onclick="openStockModal('${etf}')" style="cursor: pointer;">
                        <span class="market-symbol">${info.name}</span>
                        <span class="market-price">${typeof price === 'number' ? price.toFixed(2) : price}</span>
                        <span class="market-change ${changeClass}">${changeStr}</span>
                    </div>
                `;
            }).join('');
        }
        
        // Load movers (from watchlist or defaults)
        await loadMarketMovers();
        
    } catch (error) {
        console.error('Error loading market monitor:', error);
    }
}

// Market movers data
let currentMoversTab = 'gainers';

async function loadMarketMovers() {
    try {
        // Get watchlist prices
        const watchlistResponse = await fetchWithTimeout('/api/watchlist');
        const watchlist = await watchlistResponse.json();
        const tickers = Object.keys(watchlist).length > 0 ? Object.keys(watchlist) : 
            ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'NVDA', 'META', 'NFLX', 'AMD', 'CRM'];
        
        const prices = await fetchLatestPrices(tickers);
        
        // Calculate movers
        const stocks = Object.entries(prices)
            .filter(([_, data]) => data && data.price)
            .map(([symbol, data]) => ({
                symbol,
                price: data.price,
                change: data.change_pct || 0,
                volume: Math.random() * 10000000 // Mock volume since we don't have real data
            }));
        
        // Sort by different criteria based on tab
        let sorted = [];
        if (currentMoversTab === 'gainers') {
            sorted = stocks.filter(s => s.change > 0).sort((a, b) => b.change - a.change).slice(0, 5);
        } else if (currentMoversTab === 'losers') {
            sorted = stocks.filter(s => s.change < 0).sort((a, b) => a.change - b.change).slice(0, 5);
        } else {
            sorted = stocks.sort((a, b) => b.volume - a.volume).slice(0, 5);
        }
        
        const container = document.getElementById('marketMovers');
        if (container) {
            if (sorted.length === 0) {
                container.innerHTML = '<div class="empty-state">No data available</div>';
                return;
            }
            
            container.innerHTML = sorted.map((stock, i) => {
                const isUp = stock.change > 0;
                const isDown = stock.change < 0;
                const changeClass = isUp ? 'up' : isDown ? 'down' : 'flat';
                const arrow = isUp ? '▲' : isDown ? '▼' : '—';
                const changeStr = `${arrow}${Math.abs(stock.change).toFixed(2)}%`;
                
                return `
                    <div class="mover-item">
                        <span class="mover-rank">${i + 1}</span>
                        <span class="mover-symbol ticker-clickable" onclick="event.stopPropagation(); openStockModal('${stock.symbol}')">${stock.symbol}</span>
                        <span class="mover-name">$${stock.price.toFixed(2)}</span>
                        <span class="mover-change ${changeClass}">${changeStr}</span>
                    </div>
                `;
            }).join('');
        }
    } catch (error) {
        console.error('Error loading market movers:', error);
    }
}

// Market tab switching
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.market-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.market-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            currentMoversTab = tab.dataset.tab;
            loadMarketMovers();
        });
    });
});

// Load alerts
async function loadAlerts() {
    const response = await fetchWithTimeout('/api/alerts');
    const alerts = await response.json();
    
    document.getElementById('alertCount').textContent = alerts.length;
    
    const container = document.getElementById('alertsList');
    
    if (alerts.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <i class="fas fa-check-circle"></i>
                <p>No active alerts</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = alerts.map(alert => `
        <div class="alert-item ${alert.severity}">
            <div class="alert-header">
                <span class="alert-type">${formatAlertType(alert.type)}</span>
                <span class="alert-severity ${alert.severity}">${alert.severity}</span>
            </div>
            <div class="alert-message">${alert.message}</div>
            <div class="alert-meta">
                <span>${timeAgo(alert.created_at)}</span>
                <div class="alert-actions">
                    ${Object.entries(alert.details).map(([k, v]) => 
                        `<span>${k}: ${v}</span>`
                    ).join('')}
                    <button onclick="acknowledgeAlert(${alert.id})">Ack</button>
                </div>
            </div>
        </div>
    `).join('');
}

// Acknowledge alert
async function acknowledgeAlert(id) {
    try {
        await fetchWithTimeout(`/api/alerts/${id}/ack`, { method: 'POST' });
        loadAlerts();
        showToast('Alert acknowledged', 'success');
    } catch (error) {
        showToast('Error acknowledging alert', 'error');
    }
}

// Load trending keywords
async function loadTrendingKeywords() {
    try {
        const response = await fetchWithTimeout('/api/trending-keywords?hours=24&limit=15');
        const data = await response.json();

        const container = document.getElementById('trendingKeywords');
        if (!container) return;

        if (!data.keywords || data.keywords.length === 0) {
            container.innerHTML = '<span class="text-muted">No trending keywords</span>';
            return;
        }

        // Find max count for relative sizing
        const maxCount = Math.max(...data.keywords.map(k => k.count));

        container.innerHTML = data.keywords.map((kw, idx) => {
            const isHot = idx < 3; // Top 3 are "hot"
            return `
                <span class="keyword-tag ${isHot ? 'hot' : ''}">
                    ${kw.keyword}
                    <span class="keyword-count">${kw.count}</span>
                </span>
            `;
        }).join('');

    } catch (error) {
        console.error('Error loading trending keywords:', error);
        const container = document.getElementById('trendingKeywords');
        if (container) {
            container.innerHTML = '<span class="text-muted">Error loading keywords</span>';
        }
    }
}

// Load top companies
async function loadTopCompanies() {
    const response = await fetchWithTimeout('/api/companies/top?limit=10');
    const companies = await response.json();
    
    // Full panel (if exists - index.html)
    const container = document.getElementById('topCompanies');
    // Compact panel (inside Market Monitor - bloomberg-dashboard.html)
    const containerCompact = document.getElementById('topCompaniesCompact');
    
    if (companies.length === 0) {
        const emptyHtml = '<div class="empty-state">NO DATA</div>';
        if (container) container.innerHTML = emptyHtml;
        if (containerCompact) containerCompact.innerHTML = emptyHtml;
        return;
    }
    
    // Get tickers for price fetching
    const tickers = companies.map(c => c.company_ticker);
    
    const html = companies.map((company, index) => `
        <div class="company-item" data-ticker="${company.company_ticker}" onclick="openStockModal('${company.company_ticker}')" style="cursor: pointer;">
            <div class="company-info">
                <span class="company-rank ${index < 3 ? 'top' : ''}">${index + 1}</span>
                <div class="company-details">
                    <span class="company-name">${company.company_name}</span>
                    <span class="company-ticker">${company.company_ticker}</span>
                </div>
            </div>
            <div class="company-stats">
                <span class="company-count">${company.count}</span>
                <div class="company-price">
                    <span class="loading">...</span>
                </div>
            </div>
        </div>
    `).join('');
    
    // Populate both containers
    if (container) container.innerHTML = html;
    if (containerCompact) containerCompact.innerHTML = html;
    
    // Fetch prices after rendering
    await loadPrices(tickers);
}

// Fetch prices for companies
async function loadPrices(tickers) {
    if (!tickers || tickers.length === 0) return;
    
    try {
        const response = await fetchWithTimeout(`/api/prices?tickers=${tickers.join(',')}`);
        const prices = await response.json();
        
        // Update cache
        priceCache = {
            data: prices,
            timestamp: Date.now()
        };
        
        // Update display
        updatePriceDisplay(prices);
    } catch (error) {
        console.error('Error loading prices:', error);
    }
}

// Update price display in Top Companies panel
function updatePriceDisplay(prices) {
    // Update all company items across both containers
    document.querySelectorAll('#topCompanies .company-item, #topCompaniesCompact .company-item').forEach(item => {
        const ticker = item.dataset.ticker;
        const priceEl = item.querySelector('.company-price');
        
        if (!ticker || !priceEl) return;
        
        if (!prices || !prices[ticker]) {
            priceEl.innerHTML = '<span class="na">N/A</span>';
            return;
        }
        
        const price = prices[ticker];
        const changeClass = price.change_pct > 0 ? 'up' : price.change_pct < 0 ? 'down' : 'neutral';
        const changeIcon = price.change_pct > 0 ? '▲' : price.change_pct < 0 ? '▼' : '−';
        
        priceEl.innerHTML = `
            <span class="price">$${price.price.toFixed(2)}</span>
            <span class="change ${changeClass}">
                ${changeIcon} ${Math.abs(price.change_pct || 0).toFixed(2)}%
            </span>
        `;
    });
}

// =============================================================================
// WebSocket Real-Time Price Updates
// =============================================================================

/**
 * Initialize WebSocket connection for real-time price updates
 */
function initWebSocket() {
    // Check if Socket.IO is available
    if (typeof io === 'undefined') {
        console.warn('[WebSocket] Socket.IO not available, falling back to polling');
        updateConnectionStatus('polling');
        return;
    }

    try {
        // Connect to Socket.IO server
        socket = io({
            transports: ['websocket', 'polling'],
            reconnection: true,
            reconnectionAttempts: WS_MAX_RECONNECT_ATTEMPTS,
            reconnectionDelay: WS_RECONNECT_DELAY
        });

        // Connection established
        socket.on('connect', () => {
            console.log('[WebSocket] Connected to server');
            wsConnected = true;
            wsReconnectAttempts = 0;
            updateConnectionStatus('connected');

            // Subscribe to prices for current watchlist
            subscribeToWatchlistPrices();
        });

        // Connection status from server
        socket.on('connection_status', (data) => {
            console.log('[WebSocket] Connection status:', data);
        });

        // Subscription confirmed
        socket.on('subscription_confirmed', (data) => {
            console.log('[WebSocket] Subscribed to tickers:', data.tickers);
            subscribedTickers = data.tickers;
        });

        // Real-time price updates
        socket.on('price_update', (data) => {
            console.log('[WebSocket] Price update received for', Object.keys(data.prices).length, 'tickers');
            handleRealtimePriceUpdate(data.prices);
        });

        // Disconnection
        socket.on('disconnect', (reason) => {
            console.warn('[WebSocket] Disconnected:', reason);
            wsConnected = false;
            updateConnectionStatus('disconnected');
        });

        // Connection error
        socket.on('connect_error', (error) => {
            console.error('[WebSocket] Connection error:', error.message);
            wsReconnectAttempts++;
            updateConnectionStatus('error');

            // Fall back to polling if max reconnect attempts reached
            if (wsReconnectAttempts >= WS_MAX_RECONNECT_ATTEMPTS) {
                console.warn('[WebSocket] Max reconnect attempts reached, falling back to polling');
                updateConnectionStatus('polling');
            }
        });

        // Reconnection attempt
        socket.on('reconnect_attempt', (attempt) => {
            console.log('[WebSocket] Reconnection attempt:', attempt);
            updateConnectionStatus('reconnecting');
        });

        // Successful reconnection
        socket.on('reconnect', () => {
            console.log('[WebSocket] Reconnected successfully');
            wsConnected = true;
            wsReconnectAttempts = 0;
            updateConnectionStatus('connected');
            subscribeToWatchlistPrices();
        });

    } catch (error) {
        console.error('[WebSocket] Initialization error:', error);
        updateConnectionStatus('error');
    }
}

/**
 * Subscribe to price updates for the current watchlist
 */
function subscribeToWatchlistPrices() {
    if (!socket || !wsConnected) return;

    // Collect all tickers from the page
    const tickers = new Set();

    // From top companies
    document.querySelectorAll('.company-item[data-ticker]').forEach(el => {
        tickers.add(el.dataset.ticker);
    });

    // From market indices
    ['SPY', 'QQQ', 'DIA', 'IWM'].forEach(t => tickers.add(t));

    // From stock ticker
    document.querySelectorAll('.stock-ticker-item[data-ticker]').forEach(el => {
        if (el.dataset.ticker) tickers.add(el.dataset.ticker);
    });

    const tickerList = Array.from(tickers);
    if (tickerList.length > 0) {
        console.log('[WebSocket] Subscribing to prices:', tickerList);
        socket.emit('subscribe_prices', { tickers: tickerList });
    }
}

/**
 * Handle real-time price updates from WebSocket
 */
function handleRealtimePriceUpdate(prices) {
    if (!prices) return;

    Object.entries(prices).forEach(([ticker, data]) => {
        const prevPrice = previousPrices[ticker]?.price;
        const newPrice = data.price;
        const direction = prevPrice ? (newPrice > prevPrice ? 'up' : newPrice < prevPrice ? 'down' : null) : null;

        // Update all price displays for this ticker
        updateTickerPriceElements(ticker, data, direction);

        // Store for next comparison
        previousPrices[ticker] = data;
    });

    // Also update the price cache for other functions
    priceCache = { ...priceCache, ...prices };
}

/**
 * Update all price display elements for a specific ticker
 */
function updateTickerPriceElements(ticker, data, direction) {
    // Update company items in top companies panel
    document.querySelectorAll(`.company-item[data-ticker="${ticker}"]`).forEach(item => {
        const priceEl = item.querySelector('.company-price');
        if (priceEl) {
            const changeClass = data.change_pct > 0 ? 'up' : data.change_pct < 0 ? 'down' : 'neutral';
            const changeIcon = data.change_pct > 0 ? '▲' : data.change_pct < 0 ? '▼' : '−';

            priceEl.innerHTML = `
                <span class="price">$${data.price.toFixed(2)}</span>
                <span class="change ${changeClass}">
                    ${changeIcon} ${Math.abs(data.change_pct || 0).toFixed(2)}%
                </span>
            `;

            // Add flash animation
            if (direction) {
                priceEl.classList.remove('price-flash-up', 'price-flash-down');
                void priceEl.offsetWidth; // Force reflow
                priceEl.classList.add(direction === 'up' ? 'price-flash-up' : 'price-flash-down');
            }
        }
    });

    // Update market indices
    document.querySelectorAll(`.market-item`).forEach(item => {
        const symbolEl = item.querySelector('.market-symbol');
        if (!symbolEl) return;

        // Map ETFs to index symbols
        const etfToIndex = { 'SPY': 'SPX', 'QQQ': 'IXIC', 'DIA': 'DJI', 'IWM': 'RUT' };
        const indexSymbol = etfToIndex[ticker];
        if (indexSymbol && symbolEl.textContent === indexSymbol) {
            const priceEl = item.querySelector('.market-price');
            const changeEl = item.querySelector('.market-change');

            if (priceEl) {
                priceEl.textContent = data.price.toFixed(2);
                if (direction) {
                    priceEl.classList.remove('price-flash-up', 'price-flash-down');
                    void priceEl.offsetWidth;
                    priceEl.classList.add(direction === 'up' ? 'price-flash-up' : 'price-flash-down');
                }
            }

            if (changeEl) {
                const isUp = data.change_pct > 0;
                const isDown = data.change_pct < 0;
                const arrow = isUp ? '▲' : isDown ? '▼' : '—';
                changeEl.textContent = `${arrow}${Math.abs(data.change_pct).toFixed(2)}%`;
                changeEl.className = `market-change ${isUp ? 'up' : isDown ? 'down' : 'flat'}`;
            }
        }
    });

    // Update stock ticker tape
    document.querySelectorAll(`.stock-ticker-item`).forEach(item => {
        const symbolEl = item.querySelector('.stock-symbol');
        if (symbolEl && symbolEl.textContent === ticker) {
            const priceEl = item.querySelector('.stock-price');
            const changeEl = item.querySelector('.stock-change');

            if (priceEl) {
                priceEl.textContent = data.price.toFixed(2);
                if (direction) {
                    priceEl.classList.remove('price-flash-up', 'price-flash-down');
                    void priceEl.offsetWidth;
                    priceEl.classList.add(direction === 'up' ? 'price-flash-up' : 'price-flash-down');
                }
            }

            if (changeEl) {
                const isUp = data.change_pct > 0;
                const isDown = data.change_pct < 0;
                const arrow = isUp ? '▲' : isDown ? '▼' : '—';
                const changeVal = Math.abs(data.change_pct).toFixed(2);
                changeEl.innerHTML = `${arrow}${changeVal}%`;
                changeEl.className = `stock-change ${isUp ? 'up' : isDown ? 'down' : 'flat'}`;
            }
        }
    });
}

/**
 * Update connection status indicator in the UI
 */
function updateConnectionStatus(status) {
    // Create or update status indicator
    let indicator = document.getElementById('wsConnectionStatus');
    if (!indicator) {
        indicator = document.createElement('div');
        indicator.id = 'wsConnectionStatus';
        indicator.className = 'ws-connection-status';
        document.body.appendChild(indicator);
    }

    const statusConfig = {
        connected: { icon: 'fas fa-plug', text: 'Live', class: 'connected' },
        disconnected: { icon: 'fas fa-plug', text: 'Disconnected', class: 'disconnected' },
        reconnecting: { icon: 'fas fa-sync-alt fa-spin', text: 'Reconnecting...', class: 'reconnecting' },
        error: { icon: 'fas fa-exclamation-triangle', text: 'Connection Error', class: 'error' },
        polling: { icon: 'fas fa-clock', text: 'Polling', class: 'polling' }
    };

    const config = statusConfig[status] || statusConfig.disconnected;
    indicator.innerHTML = `<i class="${config.icon}"></i> <span>${config.text}</span>`;
    indicator.className = `ws-connection-status ${config.class}`;

    // Auto-hide connected status after 3 seconds
    if (status === 'connected') {
        setTimeout(() => {
            indicator.classList.add('minimized');
        }, 3000);
    } else {
        indicator.classList.remove('minimized');
    }
}

/**
 * Manually refresh WebSocket subscription (call after watchlist changes)
 */
function refreshWebSocketSubscription() {
    if (socket && wsConnected) {
        subscribeToWatchlistPrices();
    }
}

// Articles pagination state
let articlesState = {
    articles: [],
    offset: 0,
    limit: 50,
    loading: false,
    hasMore: true,
    filters: {
        sources: null,
        tickers: null,
        search: null,
        fromDate: null,
        toDate: null,
        sentiment: null
    }
};

// Load articles (initial load with infinite scroll support)
async function loadArticles() {
    // Reset state for fresh load
    articlesState.offset = 0;
    articlesState.articles = [];
    articlesState.hasMore = true;
    
    const container = document.getElementById('articlesList');
    container.innerHTML = '<div class="loading-indicator"><i class="fas fa-spinner fa-spin"></i> Loading articles...</div>';
    
    await loadMoreArticles();
    setupInfiniteScroll();
    setupArticleFilters();
}

// Load more articles (for pagination/infinite scroll)
async function loadMoreArticles() {
    if (articlesState.loading || !articlesState.hasMore) return;
    
    articlesState.loading = true;
    const container = document.getElementById('articlesList');
    
    // Show loading indicator at bottom if we already have articles
    if (articlesState.articles.length > 0) {
        const existingLoader = container.querySelector('.articles-loading-more');
        if (!existingLoader) {
            const loader = document.createElement('div');
            loader.className = 'articles-loading-more';
            loader.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Loading more...';
            container.appendChild(loader);
        }
    }
    
    try {
        // Build query URL
        const params = new URLSearchParams();
        params.append('limit', articlesState.limit);
        params.append('offset', articlesState.offset);
        
        if (articlesState.filters.sources) {
            params.append('sources', articlesState.filters.sources.join(','));
        }
        if (articlesState.filters.tickers) {
            params.append('tickers', articlesState.filters.tickers.join(','));
        }
        if (articlesState.filters.search) {
            params.append('search', articlesState.filters.search);
        }
        if (articlesState.filters.fromDate) {
            params.append('from_date', articlesState.filters.fromDate);
        }
        if (articlesState.filters.toDate) {
            params.append('to_date', articlesState.filters.toDate);
        }
        if (articlesState.filters.sentiment) {
            params.append('sentiment', articlesState.filters.sentiment);
        }
        
        const response = await fetchWithTimeout(`/api/articles?${params}`);
        const data = await response.json();
        
        // Remove loading indicator
        const loader = container.querySelector('.articles-loading-more');
        if (loader) loader.remove();
        
        // Handle both old format (array) and new format (object with metadata)
        let newArticles, total, hasMore;
        if (Array.isArray(data)) {
            // Old format - backward compatibility
            newArticles = data;
            total = null;
            hasMore = newArticles.length === articlesState.limit;
        } else {
            // New format with metadata
            newArticles = data.articles || [];
            total = data.total;
            hasMore = data.has_more;
        }
        
        // Clear loading message on first load
        if (articlesState.offset === 0) {
            container.innerHTML = '';
        }
        
        if (newArticles.length === 0 && articlesState.articles.length === 0) {
            container.innerHTML = '<div class="empty-state">No articles found</div>';
            return;
        }
        
        // Append new articles
        articlesState.articles.push(...newArticles);
        articlesState.hasMore = hasMore;
        articlesState.offset += newArticles.length;
        
        // Render only new articles
        renderArticlesAppend(newArticles);
        
        // Update filter options on first load
        if (articlesState.offset === newArticles.length) {
            updateArticleFilterOptions();
        }
        
        // Show "Load More" button if we have more but user prefers manual loading
        if (hasMore && !document.getElementById('loadMoreBtn')) {
            const loadMoreBtn = document.createElement('button');
            loadMoreBtn.id = 'loadMoreBtn';
            loadMoreBtn.className = 'load-more-btn';
            loadMoreBtn.innerHTML = '<i class="fas fa-chevron-down"></i> Load More';
            loadMoreBtn.onclick = () => loadMoreArticles();
            container.appendChild(loadMoreBtn);
        } else if (!hasMore && document.getElementById('loadMoreBtn')) {
            const btn = document.getElementById('loadMoreBtn');
            btn.innerHTML = '<i class="fas fa-check"></i> All articles loaded';
            btn.disabled = true;
        }
        
    } catch (error) {
        console.error('Error loading articles:', error);
        if (articlesState.articles.length === 0) {
            container.innerHTML = '<div class="empty-state">Error loading articles</div>';
        } else {
            showToast('Error loading more articles', 'error');
        }
    } finally {
        articlesState.loading = false;
    }
}

// Render articles and append to container
function renderArticlesAppend(articles) {
    const container = document.getElementById('articlesList');
    
    // Remove load more button if exists (will re-add at end)
    const loadMoreBtn = document.getElementById('loadMoreBtn');
    if (loadMoreBtn) loadMoreBtn.remove();
    
    const articlesHtml = articles.map(article => `
        <div class="article-item" data-article-id="${article.id}">
            <div class="article-header">
                <div class="article-title">
                    <a href="${article.url}" target="_blank" rel="noopener">
                        ${article.title}
                    </a>
                </div>
                <span class="article-source">${article.source}</span>
            </div>
            <div class="article-meta">
                <span>${timeAgo(article.scraped_at)}</span>
                <div>
                    ${article.mentions.map(m => `<span class="mention-badge">${m}</span>`).join('')}
                    ${article.sentiment !== null ? `
                        <span class="sentiment-badge ${getSentimentClass(article.sentiment)}">
                            ${article.sentiment > 0 ? '+' : ''}${article.sentiment.toFixed(2)}
                        </span>
                    ` : ''}
                </div>
            </div>
        </div>
    `).join('');
    
    container.insertAdjacentHTML('beforeend', articlesHtml);
    
    // Re-add load more button if we have more articles
    if (articlesState.hasMore) {
        const btn = document.createElement('button');
        btn.id = 'loadMoreBtn';
        btn.className = 'load-more-btn';
        btn.innerHTML = '<i class="fas fa-chevron-down"></i> Load More';
        btn.onclick = () => loadMoreArticles();
        container.appendChild(btn);
    }
}

// Setup infinite scroll
function setupInfiniteScroll() {
    const container = document.getElementById('articlesList');
    if (!container) return;
    
    // Use Intersection Observer for infinite scroll
    const observerOptions = {
        root: container,
        rootMargin: '100px',
        threshold: 0.1
    };
    
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting && articlesState.hasMore && !articlesState.loading) {
                loadMoreArticles();
            }
        });
    }, observerOptions);
    
    // Create sentinel element at bottom
    let sentinel = document.getElementById('articles-sentinel');
    if (!sentinel) {
        sentinel = document.createElement('div');
        sentinel.id = 'articles-sentinel';
        sentinel.style.height = '10px';
        container.appendChild(sentinel);
    }
    
    observer.observe(sentinel);
}

// Setup article filters with debounced search
function setupArticleFilters() {
    // Source filter
    const filterSelect = document.getElementById('articleFilter');
    if (filterSelect) {
        filterSelect.addEventListener('change', (e) => {
            if (e.target.value === 'all') {
                articlesState.filters.sources = null;
            } else {
                articlesState.filters.sources = [e.target.value];
            }
            // Reload with filter
            articlesState.offset = 0;
            articlesState.articles = [];
            articlesState.hasMore = true;
            document.getElementById('articlesList').innerHTML = '';
            loadMoreArticles();
        });
    }

    // Search input with debounce
    const searchInput = document.getElementById('articleSearch');
    if (searchInput) {
        // Debounced search on input
        searchInput.addEventListener('input', (e) => {
            // Clear existing timer
            if (searchDebounceTimer) {
                clearTimeout(searchDebounceTimer);
            }

            // Set new debounced search
            searchDebounceTimer = setTimeout(() => {
                const searchValue = e.target.value.trim();
                // Only search if at least 2 characters or empty (to clear)
                if (searchValue.length >= 2 || searchValue.length === 0) {
                    articlesState.filters.search = searchValue || null;
                    articlesState.offset = 0;
                    articlesState.articles = [];
                    articlesState.hasMore = true;
                    document.getElementById('articlesList').innerHTML = '<div class="loading-indicator"><i class="fas fa-spinner fa-spin"></i> Searching...</div>';
                    loadMoreArticles();
                }
            }, SEARCH_DEBOUNCE_MS);
        });

        // Immediate search on Enter key
        searchInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                // Clear debounce timer
                if (searchDebounceTimer) {
                    clearTimeout(searchDebounceTimer);
                }
                articlesState.filters.search = e.target.value.trim() || null;
                articlesState.offset = 0;
                articlesState.articles = [];
                articlesState.hasMore = true;
                document.getElementById('articlesList').innerHTML = '<div class="loading-indicator"><i class="fas fa-spinner fa-spin"></i> Searching...</div>';
                loadMoreArticles();
            }
        });
    }
}

// Update filter options based on loaded articles
function updateArticleFilterOptions() {
    const filterSelect = document.getElementById('articleFilter');
    if (!filterSelect) return;
    
    const sources = [...new Set(articlesState.articles.map(a => a.source))];
    const currentValue = filterSelect.value;
    
    filterSelect.innerHTML = '<option value="all">All Sources</option>' + 
        sources.map(s => `<option value="${s}">${s}</option>`).join('');
    
    filterSelect.value = currentValue;
}

// Legacy render function for backward compatibility
function renderArticles(articles) {
    const container = document.getElementById('articlesList');
    container.innerHTML = '';
    renderArticlesAppend(articles);
}

function renderArticles(articles) {
    const container = document.getElementById('articlesList');
    
    container.innerHTML = articles.map(article => `
        <div class="article-item">
            <div class="article-header">
                <div class="article-title">
                    <a href="${article.url}" target="_blank" rel="noopener">
                        ${highlightTickersClickable(escapeHtml(article.title))}
                    </a>
                </div>
                <span class="article-source">${article.source}</span>
            </div>
            <div class="article-meta">
                <span>${timeAgo(article.scraped_at)}</span>
                <div>
                    ${article.mentions.map(m => `<span class="mention-badge ticker-clickable" onclick="event.stopPropagation(); openStockModal('${m}')">${m}</span>`).join('')}
                    ${article.sentiment !== null ? `
                        <span class="sentiment-badge ${getSentimentClass(article.sentiment)}">
                            ${article.sentiment > 0 ? '+' : ''}${article.sentiment.toFixed(2)}
                        </span>
                    ` : ''}
                </div>
            </div>
        </div>
    `).join('');
}

// Load sentiment data
async function loadSentiment() {
    const response = await fetchWithTimeout('/api/sentiment');
    const data = await response.json();
    
    // Update stats
    const statsContainer = document.getElementById('sentimentStats');
    if (statsContainer) {
        statsContainer.innerHTML = `
            <div class="sentiment-stat">
                <span class="sentiment-stat-value positive">${data.positive}</span>
                <span class="sentiment-stat-label">Positive</span>
            </div>
            <div class="sentiment-stat">
                <span class="sentiment-stat-value neutral">${data.neutral}</span>
                <span class="sentiment-stat-label">Neutral</span>
            </div>
            <div class="sentiment-stat">
                <span class="sentiment-stat-value negative">${data.negative}</span>
                <span class="sentiment-stat-label">Negative</span>
            </div>
        `;
    }
    
    // Chart removed - using news ticker instead
    return;
}

// Update main chart - DISABLED (using ticker instead)
async function updateMainChart() {
    console.log('[Dashboard] Main chart disabled - using news ticker');
    return;
    
    // Old chart code disabled
    if (typeof Chart === 'undefined') {
        console.warn('Chart.js not loaded - skipping chart render');
        const container = document.getElementById('mainChart');
        if (container) {
            container.parentElement.innerHTML = '<div class="chart-error">Chart unavailable</div>';
        }
        return;
    }
    
    const ctx = document.getElementById('mainChart').getContext('2d');
    
    if (mainChart) {
        mainChart.destroy();
    }
    
    if (currentChartType === 'mentions') {
        const response = await fetchWithTimeout('/api/timeline?hours=24');
        const data = await response.json();
        renderMentionsChart(ctx, data);
    } else if (currentChartType === 'sentiment') {
        renderSentimentTrendChart(ctx);
    } else if (currentChartType === 'sources') {
        const response = await fetchWithTimeout('/api/sources');
        const data = await response.json();
        renderSourcesChart(ctx, data);
    }
}

function renderMentionsChart(ctx, data) {
    const companies = Object.keys(data).slice(0, 5); // Top 5
    const hours = Array.from(new Set(
        companies.flatMap(t => data[t].data.map(d => d.time))
    )).sort();
    
    const colors = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'];
    
    mainChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: hours.map(h => h.split(' ')[1]), // Show only time
            datasets: companies.map((ticker, i) => ({
                label: ticker,
                data: hours.map(h => {
                    const point = data[ticker].data.find(d => d.time === h);
                    return point ? point.count : 0;
                }),
                borderColor: colors[i],
                backgroundColor: colors[i] + '20',
                tension: 0.4,
                fill: true
            }))
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                intersect: false,
                mode: 'index'
            },
            plugins: {
                legend: {
                    position: 'top',
                    labels: { color: '#94a3b8' }
                }
            },
            scales: {
                x: {
                    grid: { color: '#334155' },
                    ticks: { color: '#94a3b8' }
                },
                y: {
                    grid: { color: '#334155' },
                    ticks: { color: '#94a3b8' },
                    beginAtZero: true
                }
            }
        }
    });
}

async function renderSentimentTrendChart(ctx) {
    // Mock sentiment trend - in real app would fetch from API
    const hours = Array.from({length: 12}, (_, i) => `${i * 2}:00`);
    
    mainChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: hours,
            datasets: [
                {
                    label: 'Positive',
                    data: hours.map(() => Math.floor(Math.random() * 10)),
                    backgroundColor: '#10b981'
                },
                {
                    label: 'Negative',
                    data: hours.map(() => Math.floor(Math.random() * 5)),
                    backgroundColor: '#ef4444'
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    labels: { color: '#94a3b8' }
                }
            },
            scales: {
                x: {
                    grid: { color: '#334155' },
                    ticks: { color: '#94a3b8' },
                    stacked: true
                },
                y: {
                    grid: { color: '#334155' },
                    ticks: { color: '#94a3b8' },
                    stacked: true,
                    beginAtZero: true
                }
            }
        }
    });
}

function renderSourcesChart(ctx, data) {
    mainChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: data.map(d => d.source),
            datasets: [{
                label: 'Articles (24h)',
                data: data.map(d => d.count),
                backgroundColor: '#3b82f6'
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false }
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { color: '#94a3b8' }
                },
                y: {
                    grid: { color: '#334155' },
                    ticks: { color: '#94a3b8' },
                    beginAtZero: true
                }
            }
        }
    });
}

// Run bot manually
async function runBot() {
    const btn = document.getElementById('runBotBtn');
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Running...';
    showStatus('running');
    
    try {
        const response = await fetchWithTimeout('/api/run', { method: 'POST' }, 60000);
        const result = await response.json();
        
        if (result.success) {
            showToast('Bot run completed', 'success');
            await loadAllData();
        } else {
            showToast('Bot run failed: ' + result.error, 'error');
        }
    } catch (error) {
        showToast('Error running bot', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-play"></i> Run Now';
        showStatus('ready');
    }
}

// Utility functions
function formatNumber(num) {
    if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
    if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
    return num.toString();
}

function formatAlertType(type) {
    return type.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
}

function timeAgo(dateString) {
    const date = new Date(dateString);
    const seconds = Math.floor((new Date() - date) / 1000);
    
    const intervals = {
        year: 31536000,
        month: 2592000,
        week: 604800,
        day: 86400,
        hour: 3600,
        minute: 60
    };
    
    for (const [unit, secondsInUnit] of Object.entries(intervals)) {
        const interval = Math.floor(seconds / secondsInUnit);
        if (interval >= 1) {
            return `${interval} ${unit}${interval > 1 ? 's' : ''} ago`;
        }
    }
    return 'Just now';
}

function getSentimentClass(score) {
    if (score > 0.2) return 'positive';
    if (score < -0.2) return 'negative';
    return 'neutral';
}

function updateLastUpdated() {
    const now = new Date();
    document.getElementById('lastUpdated').textContent = 
        'Last updated: ' + now.toLocaleTimeString();
}

function showStatus(status) {
    const indicator = document.getElementById('statusIndicator');
    const statusMap = {
        'ready': { text: 'Ready', class: '', icon: 'fa-circle' },
        'loading': { text: 'Loading...', class: 'running', icon: 'fa-spinner fa-spin' },
        'running': { text: 'Running bot...', class: 'running', icon: 'fa-spinner fa-spin' },
        'error': { text: 'Error', class: '', icon: 'fa-exclamation-circle' }
    };
    
    const s = statusMap[status] || statusMap.ready;
    indicator.className = 'status ' + s.class;
    indicator.innerHTML = `<i class="fas ${s.icon}"></i> ${s.text}`;
}

function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;

    const icons = {
        success: 'fa-check-circle',
        error: 'fa-exclamation-circle',
        info: 'fa-info-circle'
    };

    toast.innerHTML = `<i class="fas ${icons[type]}"></i> ${message}`;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(100%)';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// =============================================================================
// Settings Functions
// =============================================================================

function setupSettingsEventListeners() {
    // Tab navigation
    document.querySelectorAll('.nav-tab').forEach(tab => {
        tab.addEventListener('click', (e) => {
            const tabId = e.currentTarget.dataset.tab;
            switchTab(tabId);
        });
    });

    // Add ticker button
    const addTickerBtn = document.getElementById('addTickerBtn');
    if (addTickerBtn) {
        addTickerBtn.addEventListener('click', addTickerToWatchlist);
    }

    // Enter key on ticker input
    const tickerInput = document.getElementById('tickerInput');
    if (tickerInput) {
        tickerInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                addTickerToWatchlist();
            }
        });
    }

    // Alert channel toggles
    ['Console', 'File', 'Telegram', 'Webhook'].forEach(channel => {
        const toggle = document.getElementById(`channel${channel}`);
        if (toggle) {
            toggle.addEventListener('change', () => {
                updateAlertChannels();
                markSettingsDirty();
            });
        }
    });

    // Routing checkboxes
    document.querySelectorAll('.routing-checkbox').forEach(checkbox => {
        checkbox.addEventListener('change', () => {
            updateSeverityRouting();
            markSettingsDirty();
        });
    });

    // Threshold sliders
    const volumeThreshold = document.getElementById('volumeThreshold');
    if (volumeThreshold) {
        volumeThreshold.addEventListener('input', () => {
            document.getElementById('volumeThresholdValue').textContent = volumeThreshold.value + 'x';
            markSettingsDirty();
        });
    }

    const sentimentThreshold = document.getElementById('sentimentThreshold');
    if (sentimentThreshold) {
        sentimentThreshold.addEventListener('input', () => {
            document.getElementById('sentimentThresholdValue').textContent = sentimentThreshold.value;
            markSettingsDirty();
        });
    }

    const minArticles = document.getElementById('minArticles');
    if (minArticles) {
        minArticles.addEventListener('change', () => {
            markSettingsDirty();
        });
    }

    // Save settings button
    const saveBtn = document.getElementById('saveSettingsBtn');
    if (saveBtn) {
        saveBtn.addEventListener('click', saveAllSettings);
    }
}

function switchTab(tabId) {
    // Update tab buttons
    document.querySelectorAll('.nav-tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.tab === tabId);
    });

    // Update tab content
    document.querySelectorAll('.tab-content').forEach(content => {
        content.classList.remove('active');
    });

    const targetTab = document.getElementById(`${tabId}Tab`);
    if (targetTab) {
        targetTab.classList.add('active');
    }

    // Load settings when switching to settings tab
    if (tabId === 'settings') {
        loadSettings();
    }
}

async function loadSettings() {
    try {
        showStatus('loading');

        // Load all settings in parallel
        const [prefsResponse, watchlistResponse, rulesResponse] = await Promise.all([
            fetchWithTimeout('/api/preferences'),
            fetchWithTimeout('/api/watchlist'),
            fetchWithTimeout('/api/alert-rules')
        ]);

        const preferences = await prefsResponse.json();
        const watchlist = await watchlistResponse.json();
        const rules = await rulesResponse.json();

        // Store in state
        settingsState.watchlist = watchlist;
        settingsState.alert_channels = rules.alert_channels || {};
        settingsState.severity_routing = rules.severity_routing || {};
        settingsState.thresholds = preferences.thresholds || {};
        settingsState.company_preferences = rules.company_preferences || {};
        settingsState.isDirty = false;

        // Populate UI
        renderWatchlistTags();
        populateAlertChannels();
        populateSeverityRouting();
        populateThresholds();
        renderCompanyPreferences();

        showStatus('ready');
    } catch (error) {
        console.error('Error loading settings:', error);
        showToast('Error loading settings', 'error');
        showStatus('error');
    }
}

function renderWatchlistTags() {
    const container = document.getElementById('watchlistTags');
    if (!container) return;

    const watchlist = settingsState.watchlist || {};
    const tickers = Object.keys(watchlist);

    if (tickers.length === 0) {
        container.innerHTML = '<span class="setting-label-hint">No companies in watchlist</span>';
        return;
    }

    container.innerHTML = tickers.map(ticker => `
        <span class="tag" data-ticker="${ticker}">
            <strong>${ticker}</strong>
            <span style="opacity: 0.8; font-size: 11px;">(${(watchlist[ticker] || []).slice(0, 2).join(', ')}${watchlist[ticker]?.length > 2 ? '...' : ''})</span>
            <button class="tag-remove" onclick="removeTickerFromWatchlist('${ticker}')" title="Remove">
                <i class="fas fa-times"></i>
            </button>
        </span>
    `).join('');
}

async function addTickerToWatchlist() {
    const tickerInput = document.getElementById('tickerInput');
    const namesInput = document.getElementById('namesInput');

    const ticker = tickerInput.value.toUpperCase().trim();
    const namesStr = namesInput.value.trim();

    if (!ticker) {
        showToast('Please enter a ticker symbol', 'error');
        tickerInput.focus();
        return;
    }

    if (!namesStr) {
        showToast('Please enter at least one company name', 'error');
        namesInput.focus();
        return;
    }

    const names = namesStr.split(',').map(n => n.trim()).filter(n => n);

    if (names.length === 0) {
        showToast('Please enter valid company names', 'error');
        return;
    }

    try {
        const response = await fetchWithTimeout('/api/watchlist', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'add', ticker, names })
        });

        const result = await response.json();

        if (result.success) {
            settingsState.watchlist = result.watchlist;
            renderWatchlistTags();
            renderCompanyPreferences();
            tickerInput.value = '';
            namesInput.value = '';
            showToast(`Added ${ticker} to watchlist`, 'success');
        } else {
            showToast(result.error || 'Failed to add ticker', 'error');
        }
    } catch (error) {
        console.error('Error adding ticker:', error);
        showToast('Failed to add ticker', 'error');
    }
}

async function removeTickerFromWatchlist(ticker) {
    if (!confirm(`Remove ${ticker} from watchlist?`)) {
        return;
    }

    try {
        const response = await fetchWithTimeout('/api/watchlist', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'remove', ticker })
        });

        const result = await response.json();

        if (result.success) {
            settingsState.watchlist = result.watchlist;
            renderWatchlistTags();
            renderCompanyPreferences();
            showToast(`Removed ${ticker} from watchlist`, 'success');
        } else {
            showToast(result.error || 'Failed to remove ticker', 'error');
        }
    } catch (error) {
        console.error('Error removing ticker:', error);
        showToast('Failed to remove ticker', 'error');
    }
}

function populateAlertChannels() {
    const channels = settingsState.alert_channels || {};

    document.getElementById('channelConsole').checked = channels.console !== false;
    document.getElementById('channelFile').checked = channels.file !== false;
    document.getElementById('channelTelegram').checked = channels.telegram === true;
    document.getElementById('channelWebhook').checked = channels.webhook === true;
}

function updateAlertChannels() {
    settingsState.alert_channels = {
        console: document.getElementById('channelConsole').checked,
        file: document.getElementById('channelFile').checked,
        telegram: document.getElementById('channelTelegram').checked,
        webhook: document.getElementById('channelWebhook').checked
    };
}

function populateSeverityRouting() {
    const routing = settingsState.severity_routing || {};

    ['high', 'medium', 'low'].forEach(severity => {
        const channels = routing[severity] || [];
        ['console', 'file', 'telegram', 'webhook'].forEach(channel => {
            const checkbox = document.querySelector(
                `.routing-checkbox[data-severity="${severity}"][data-channel="${channel}"]`
            );
            if (checkbox) {
                checkbox.checked = channels.includes(channel);
            }
        });
    });
}

function updateSeverityRouting() {
    const routing = {};

    ['high', 'medium', 'low'].forEach(severity => {
        routing[severity] = [];
        ['console', 'file', 'telegram', 'webhook'].forEach(channel => {
            const checkbox = document.querySelector(
                `.routing-checkbox[data-severity="${severity}"][data-channel="${channel}"]`
            );
            if (checkbox && checkbox.checked) {
                routing[severity].push(channel);
            }
        });
    });

    settingsState.severity_routing = routing;
}

function populateThresholds() {
    const thresholds = settingsState.thresholds || {};

    const volumeSlider = document.getElementById('volumeThreshold');
    if (volumeSlider) {
        volumeSlider.value = thresholds.volume_spike || 3.0;
        document.getElementById('volumeThresholdValue').textContent = volumeSlider.value + 'x';
    }

    const minArticlesInput = document.getElementById('minArticles');
    if (minArticlesInput) {
        minArticlesInput.value = thresholds.min_articles || 3;
    }

    const sentimentSlider = document.getElementById('sentimentThreshold');
    if (sentimentSlider) {
        sentimentSlider.value = thresholds.sentiment_shift || 0.3;
        document.getElementById('sentimentThresholdValue').textContent = sentimentSlider.value;
    }
}

function renderCompanyPreferences() {
    const container = document.getElementById('companyPreferences');
    const emptyState = document.getElementById('noCompanyPrefs');
    if (!container) return;

    const watchlist = settingsState.watchlist || {};
    const prefs = settingsState.company_preferences || {};
    const tickers = Object.keys(watchlist);

    if (tickers.length === 0) {
        container.innerHTML = '';
        if (emptyState) emptyState.style.display = 'block';
        return;
    }

    if (emptyState) emptyState.style.display = 'none';

    container.innerHTML = tickers.slice(0, 10).map(ticker => {
        const names = watchlist[ticker] || [];
        const companyPref = prefs[ticker] || {};

        return `
            <div class="company-pref-item">
                <div class="company-pref-header">
                    <div>
                        <span class="company-pref-ticker">${ticker}</span>
                        <span class="company-pref-name">${names[0] || ''}</span>
                    </div>
                </div>
                <div class="company-pref-controls">
                    <div class="company-pref-control">
                        <input type="checkbox" id="mute_${ticker}" ${companyPref.muted ? 'checked' : ''}
                               onchange="updateCompanyPref('${ticker}', 'muted', this.checked)">
                        <label for="mute_${ticker}">Mute alerts</label>
                    </div>
                    <div class="company-pref-control">
                        <label for="priority_${ticker}">Priority:</label>
                        <select id="priority_${ticker}" onchange="updateCompanyPref('${ticker}', 'priority', this.value)">
                            <option value="normal" ${companyPref.priority !== 'high' && companyPref.priority !== 'low' ? 'selected' : ''}>Normal</option>
                            <option value="high" ${companyPref.priority === 'high' ? 'selected' : ''}>High</option>
                            <option value="low" ${companyPref.priority === 'low' ? 'selected' : ''}>Low</option>
                        </select>
                    </div>
                </div>
            </div>
        `;
    }).join('');

    if (tickers.length > 10) {
        container.innerHTML += `
            <div class="company-pref-item" style="text-align: center; color: var(--text-secondary);">
                <i class="fas fa-info-circle"></i> Showing first 10 companies. ${tickers.length - 10} more in watchlist.
            </div>
        `;
    }
}

function updateCompanyPref(ticker, key, value) {
    if (!settingsState.company_preferences[ticker]) {
        settingsState.company_preferences[ticker] = {};
    }
    settingsState.company_preferences[ticker][key] = value;
    markSettingsDirty();
}

function markSettingsDirty() {
    settingsState.isDirty = true;

    const saveBtn = document.getElementById('saveSettingsBtn');
    if (saveBtn) {
        saveBtn.innerHTML = '<i class="fas fa-save"></i> Save Settings *';
    }

    // Debounced auto-save (optional - can be enabled)
    // clearTimeout(saveDebounceTimer);
    // saveDebounceTimer = setTimeout(saveAllSettings, 2000);
}

async function saveAllSettings() {
    const saveBtn = document.getElementById('saveSettingsBtn');

    try {
        saveBtn.disabled = true;
        saveBtn.classList.add('saving');
        saveBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving...';

        // Collect all settings from UI
        updateAlertChannels();
        updateSeverityRouting();

        const thresholds = {
            volume_spike: parseFloat(document.getElementById('volumeThreshold').value),
            min_articles: parseInt(document.getElementById('minArticles').value),
            sentiment_shift: parseFloat(document.getElementById('sentimentThreshold').value)
        };

        // Save preferences
        const prefsResponse = await fetchWithTimeout('/api/preferences', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                thresholds: thresholds
            })
        });

        // Save alert rules
        const rulesResponse = await fetchWithTimeout('/api/alert-rules', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                alert_channels: settingsState.alert_channels,
                severity_routing: settingsState.severity_routing,
                company_preferences: settingsState.company_preferences
            })
        });

        const prefsResult = await prefsResponse.json();
        const rulesResult = await rulesResponse.json();

        if (prefsResult.success !== false && rulesResult.success !== false) {
            settingsState.isDirty = false;
            saveBtn.classList.remove('saving');
            saveBtn.classList.add('saved');
            saveBtn.innerHTML = '<i class="fas fa-check"></i> Saved!';
            showToast('Settings saved successfully', 'success');

            setTimeout(() => {
                saveBtn.classList.remove('saved');
                saveBtn.innerHTML = '<i class="fas fa-save"></i> Save Settings';
            }, 2000);
        } else {
            const errors = [...(prefsResult.errors || []), ...(rulesResult.errors || [])];
            throw new Error(errors.join(', ') || 'Save failed');
        }
    } catch (error) {
        console.error('Error saving settings:', error);
        saveBtn.classList.remove('saving');
        saveBtn.innerHTML = '<i class="fas fa-exclamation-triangle"></i> Error';
        showToast('Error saving settings: ' + error.message, 'error');

        setTimeout(() => {
            saveBtn.innerHTML = '<i class="fas fa-save"></i> Save Settings *';
        }, 2000);
    } finally {
        saveBtn.disabled = false;
    }
}

// ============================================================================
// News Ticker
// ============================================================================

let tickerPaused = false;

function initTicker() {
    console.log('[Ticker] Initializing...');
    
    const tickerEl = document.getElementById('newsTicker');
    const containerEl = document.querySelector('.news-ticker-container');
    
    if (!tickerEl) {
        console.error('[Ticker] ERROR: newsTicker element not found!');
        return;
    }
    if (!containerEl) {
        console.error('[Ticker] ERROR: news-ticker-container element not found!');
        return;
    }
    
    console.log('[Ticker] Elements found, container visible:', containerEl.offsetHeight > 0);
    
    // Load initial ticker data
    updateTicker();
    
    // Update every 30 seconds
    setInterval(updateTicker, 30000);
    
    // Pause button
    const pauseBtn = document.getElementById('pauseTicker');
    if (pauseBtn) {
        pauseBtn.addEventListener('click', toggleTickerPause);
    }
    
    console.log('[Ticker] Initialization complete');
}

// Default watchlist for ticker
const DEFAULT_TICKERS = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'NVDA', 'META', 'SPY', 'QQQ'];

async function updateTicker() {
    try {
        // Fetch watchlist and build ticker stock items
        let tickers = [...DEFAULT_TICKERS];
        
        // Try to get watchlist from settings
        try {
            const watchlistResponse = await fetchWithTimeout('/api/watchlist');
            const watchlist = await watchlistResponse.json();
            if (watchlist && Object.keys(watchlist).length > 0) {
                tickers = Object.keys(watchlist).slice(0, 15); // Max 15 stocks
            }
        } catch (e) {
            console.log('[Ticker] Using default watchlist');
        }
        
        // Fetch prices for all tickers
        const prices = await fetchLatestPrices(tickers);
        
        // Build stock ticker items
        const items = [];
        
        Object.entries(prices).forEach(([symbol, data]) => {
            if (data && data.price) {
                items.push({
                    symbol: symbol,
                    price: data.price,
                    change: data.change_pct || 0,
                    type: 'stock'
                });
            }
        });
        
        // If no prices, show default message
        if (items.length === 0) {
            tickers.forEach(symbol => {
                items.push({
                    symbol: symbol,
                    price: null,
                    change: null,
                    type: 'stock'
                });
            });
        }
        
        renderStockTicker(items);
        
    } catch (error) {
        console.error('Error updating ticker:', error);
        // Show fallback on error
        renderStockTicker(DEFAULT_TICKERS.map(s => ({ symbol: s, price: null, change: null, type: 'stock' })));
    }
}

// Price cache variables (priceCache and PRICE_CACHE_TTL already declared at top of file)
let lastPriceFetch = 0;

// Mock prices for fallback when API fails
const MOCK_PRICES = {
    'AAPL': { price: 185.92, change_pct: 1.25 },
    'MSFT': { price: 420.55, change_pct: 0.85 },
    'GOOGL': { price: 175.98, change_pct: -0.45 },
    'AMZN': { price: 178.35, change_pct: 1.12 },
    'TSLA': { price: 248.50, change_pct: -2.30 },
    'NVDA': { price: 875.28, change_pct: 3.45 },
    'META': { price: 505.20, change_pct: 0.95 },
    'NFLX': { price: 628.75, change_pct: -0.85 },
    'AMD': { price: 162.45, change_pct: 1.85 },
    'CRM': { price: 295.30, change_pct: -0.35 },
    'SPY': { price: 520.50, change_pct: 0.65 },
    'QQQ': { price: 445.25, change_pct: 0.95 },
    'DIA': { price: 390.80, change_pct: 0.25 },
    'IWM': { price: 205.40, change_pct: -0.15 },
    'INTC': { price: 43.25, change_pct: -1.20 },
    'DIS': { price: 112.50, change_pct: 0.45 },
    'BA': { price: 205.75, change_pct: -0.65 },
    'JPM': { price: 195.80, change_pct: 0.35 }
};

async function fetchLatestPrices(tickers) {
    if (!tickers || tickers.length === 0) return {};
    
    // Check cache first
    const now = Date.now();
    if (now - lastPriceFetch < PRICE_CACHE_TTL && Object.keys(priceCache).length > 0) {
        console.log('[Prices] Using cached data');
        // Return cached data for requested tickers
        const result = {};
        tickers.forEach(ticker => {
            if (priceCache[ticker]) {
                result[ticker] = priceCache[ticker];
            } else if (MOCK_PRICES[ticker]) {
                // Add some random variation to mock prices
                const base = MOCK_PRICES[ticker];
                const variation = (Math.random() - 0.5) * 0.5;
                result[ticker] = {
                    price: base.price + variation,
                    change_pct: base.change_pct + variation * 0.5
                };
            }
        });
        return result;
    }
    
    try {
        // Try API with shorter timeout to avoid hanging
        const response = await fetchWithTimeout(`/api/prices?tickers=${tickers.join(',')}`, {}, 5000);
        const data = await response.json();
        
        // Update cache
        priceCache = { ...priceCache, ...data };
        lastPriceFetch = now;
        
        // Fill in any missing tickers with mock data
        tickers.forEach(ticker => {
            if (!data[ticker] && MOCK_PRICES[ticker]) {
                data[ticker] = MOCK_PRICES[ticker];
            }
        });
        
        return data;
    } catch (error) {
        console.warn('[Prices] API failed, using mock data:', error.message);
        
        // Return mock data for requested tickers
        const result = {};
        tickers.forEach(ticker => {
            if (MOCK_PRICES[ticker]) {
                // Add some random variation
                const base = MOCK_PRICES[ticker];
                const variation = (Math.random() - 0.5) * 0.5;
                result[ticker] = {
                    price: base.price + variation,
                    change_pct: base.change_pct + variation * 0.5
                };
            } else {
                // Generate random price for unknown tickers
                result[ticker] = {
                    price: 100 + Math.random() * 200,
                    change_pct: (Math.random() - 0.5) * 5
                };
            }
        });
        
        return result;
    }
}

function renderStockTicker(items) {
    const ticker = document.getElementById('newsTicker');
    if (!ticker) {
        console.error('[Ticker] ERROR: Cannot render - newsTicker element not found');
        return;
    }
    
    console.log(`[Ticker] Rendering ${items.length} stock items`);
    
    if (items.length === 0) {
        ticker.innerHTML = `
            <span class="stock-ticker-item">
                <span class="stock-symbol">LOADING</span>
            </span>
        `;
        return;
    }
    
    // Duplicate items for seamless loop
    const allItems = [...items, ...items];
    
    ticker.innerHTML = allItems.map(item => {
        const symbol = item.symbol;
        const price = item.price ? item.price.toFixed(2) : '--.--';
        const change = item.change !== null && item.change !== undefined ? item.change : 0;
        const changeVal = Math.abs(change).toFixed(2);
        const isUp = change > 0;
        const isDown = change < 0;
        const arrow = isUp ? '▲' : isDown ? '▼' : '—';
        const changeClass = isUp ? 'up' : isDown ? 'down' : 'flat';
        
        return `
            <span class="stock-ticker-item" onclick="openStockModal('${symbol}')" style="cursor: pointer;" title="Click to view ${symbol} details">
                <span class="stock-symbol">${symbol}</span>
                <span class="stock-price">${price}</span>
                <span class="stock-change ${changeClass}">
                    ${arrow}${changeVal}%
                </span>
            </span>
            <span class="stock-separator"></span>
        `;
    }).join('');
}

// Legacy function - kept for compatibility
function renderTicker(items) {
    renderStockTicker(items);
}

function toggleTickerPause() {
    tickerPaused = !tickerPaused;
    const ticker = document.querySelector('.ticker-content');
    const btn = document.getElementById('pauseTicker');
    
    if (ticker) {
        ticker.style.animationPlayState = tickerPaused ? 'paused' : 'running';
    }
    
    if (btn) {
        btn.innerHTML = tickerPaused ? '<i class="fas fa-play"></i>' : '<i class="fas fa-pause"></i>';
    }
}

// ============================================================================
// Bloomberg Keyboard Shortcuts
// ============================================================================

const KEYBOARD_SHORTCUTS = {
    // Navigation
    'r': { action: 'refresh', description: 'Refresh data', category: 'navigation' },
    'a': { action: 'alerts', description: 'Jump to Alerts', category: 'navigation' },
    'e': { action: 'economic', description: 'Economic Calendar', category: 'navigation' },
    'n': { action: 'news', description: 'Jump to News feed', category: 'navigation' },
    't': { action: 'ticker', description: 'Jump to Ticker', category: 'navigation' },
    's': { action: 'settings', description: 'Open Settings', category: 'navigation' },
    'w': { action: 'watchlist', description: 'Focus Watchlist', category: 'navigation' },
    'm': { action: 'market', description: 'Market Overview', category: 'navigation' },

    // Search
    '/': { action: 'search', description: 'Open Search', category: 'search' },
    'k': { action: 'search', description: 'Open Search (Ctrl+K)', category: 'search' },

    // Help
    '?': { action: 'help', description: 'Show shortcuts help', category: 'help' },
    'h': { action: 'help', description: 'Show shortcuts help', category: 'help' },

    // Quick actions (number keys)
    '1': { action: 'quick1', description: 'Refresh data', category: 'quick' },
    '2': { action: 'quick2', description: 'Toggle Watchlist', category: 'quick' },
    '3': { action: 'quick3', description: 'View Top Gainers', category: 'quick' },
    '4': { action: 'quick4', description: 'View Top Losers', category: 'quick' },
    '5': { action: 'quick5', description: 'View Most Active', category: 'quick' },
    '6': { action: 'quick6', description: 'Open Bluesky Feed', category: 'quick' },
    '7': { action: 'quick7', description: 'Open Dashboard', category: 'quick' },
    '8': { action: 'quick8', description: 'Open Settings', category: 'quick' },
    '9': { action: 'quick9', description: 'Run Bot', category: 'quick' },
};

let searchMode = false;
let helpVisible = false;

// Navigation state for arrow key navigation
let navigationState = {
    activePanel: null,
    selectedIndex: -1,
    items: []
};

function initKeyboardShortcuts() {
    document.addEventListener('keydown', handleKeydown);
    console.log('⌨️  Keyboard shortcuts initialized. Press ? for help.');
}

function handleKeydown(e) {
    // Don't trigger shortcuts when typing in inputs
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') {
        if (e.key === 'Escape') {
            e.target.blur(); // Unfocus input on Escape
            searchMode = false;
        }
        return;
    }

    // Handle Ctrl+K for search
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        openSearch();
        return;
    }

    // Handle Escape key to close overlays/modals
    if (e.key === 'Escape') {
        e.preventDefault();
        closeAllOverlays();
        return;
    }

    // Handle arrow key navigation
    if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
        if (navigationState.activePanel && navigationState.items.length > 0) {
            e.preventDefault();
            navigateList(e.key === 'ArrowDown' ? 1 : -1);
            return;
        }
    }

    // Handle Enter key to select highlighted item
    if (e.key === 'Enter') {
        if (navigationState.activePanel && navigationState.selectedIndex >= 0) {
            e.preventDefault();
            selectNavigatedItem();
            return;
        }
    }

    const key = e.key.toLowerCase();
    const shortcut = KEYBOARD_SHORTCUTS[key];

    if (!shortcut) return;

    // Handle help first
    if (shortcut.action === 'help') {
        e.preventDefault();
        toggleHelpOverlay();
        return;
    }

    // Close help if open
    if (helpVisible && shortcut.action !== 'help') {
        hideHelpOverlay();
    }

    // Execute shortcut
    switch (shortcut.action) {
        case 'refresh':
        case 'quick1':
            e.preventDefault();
            refreshData();
            showToast('Refreshing...', 'info');
            break;

        case 'alerts':
            e.preventDefault();
            focusPanel('alertsList');
            highlightPanel('.alerts-panel, .panel:has(#alertsList)');
            showToast('Alerts focused', 'info');
            break;

        case 'economic':
            e.preventDefault();
            handleEconomicCalendarShortcut();
            break;

        case 'news':
            e.preventDefault();
            focusPanelWithNavigation('articlesList', '.article-item');
            highlightPanel('.articles-panel');
            showToast('News feed focused - use arrows to navigate', 'info');
            break;

        case 'ticker':
            e.preventDefault();
            focusPanel('newsTicker');
            highlightPanel('.news-ticker-container');
            break;

        case 'settings':
        case 'quick8':
            e.preventDefault();
            switchTab('settings');
            showToast('Settings opened', 'info');
            break;

        case 'search':
            e.preventDefault();
            openSearch();
            break;

        case 'watchlist':
        case 'quick2':
            e.preventDefault();
            focusPanelWithNavigation('topCompaniesCompact', '.company-item, .mover-item');
            highlightPanel('.panel:has(#topCompaniesCompact), .panel:has(#marketMovers)');
            showToast('Watchlist focused - use arrows to navigate', 'info');
            break;

        case 'market':
            e.preventDefault();
            focusPanelWithNavigation('marketMovers', '.mover-item');
            highlightPanel('.panel:has(#marketMovers)');
            showToast('Market overview focused', 'info');
            break;

        case 'quick3':
            e.preventDefault();
            switchMarketTab('gainers');
            break;

        case 'quick4':
            e.preventDefault();
            switchMarketTab('losers');
            break;

        case 'quick5':
            e.preventDefault();
            switchMarketTab('active');
            break;

        case 'quick6':
            e.preventDefault();
            focusPanel('blueskyFeedFull');
            highlightPanel('.bluesky-panel-full');
            showToast('Bluesky feed focused', 'info');
            break;

        case 'quick7':
            e.preventDefault();
            switchTab('dashboard');
            showToast('Dashboard opened', 'info');
            break;

        case 'quick9':
            e.preventDefault();
            runBot();
            break;

        case 'timeframe':
            e.preventDefault();
            setChartTimeframe(shortcut.value);
            break;
    }
}

// Close all open overlays and modals
function closeAllOverlays() {
    let closed = false;

    // Close help overlay
    if (helpVisible) {
        hideHelpOverlay();
        closed = true;
    }

    // Close search overlay
    const searchOverlay = document.getElementById('searchOverlay');
    if (searchOverlay && searchOverlay.classList.contains('active')) {
        closeSearchOverlay();
        closed = true;
    }

    // Close stock modal
    const stockModal = document.getElementById('stockDetailModal');
    if (stockModal && stockModal.style.display !== 'none') {
        closeStockModal();
        closed = true;
    }

    // Close stock details panel
    const stockDetails = document.getElementById('stockDetailsPanel');
    if (stockDetails && stockDetails.style.display !== 'none') {
        closeStockDetails();
        closed = true;
    }

    // Reset navigation state
    clearNavigationState();

    if (!closed) {
        // Focus command input as fallback
        const commandInput = document.getElementById('commandInput');
        if (commandInput) {
            commandInput.focus();
        }
    }
}

// Focus panel with navigation enabled
function focusPanelWithNavigation(panelId, itemSelector) {
    const panel = document.getElementById(panelId);
    if (!panel) return;

    // Scroll panel into view
    panel.scrollIntoView({ behavior: 'smooth', block: 'center' });

    // Set up navigation state
    navigationState.activePanel = panelId;
    navigationState.items = Array.from(panel.querySelectorAll(itemSelector));
    navigationState.selectedIndex = -1;

    // Highlight first item
    if (navigationState.items.length > 0) {
        navigationState.selectedIndex = 0;
        updateNavigationHighlight();
    }
}

// Navigate through list items
function navigateList(direction) {
    if (!navigationState.items || navigationState.items.length === 0) return;

    // Remove previous highlight
    clearNavigationHighlight();

    // Update index
    navigationState.selectedIndex += direction;

    // Wrap around
    if (navigationState.selectedIndex < 0) {
        navigationState.selectedIndex = navigationState.items.length - 1;
    } else if (navigationState.selectedIndex >= navigationState.items.length) {
        navigationState.selectedIndex = 0;
    }

    // Apply new highlight
    updateNavigationHighlight();
}

// Update navigation highlight
function updateNavigationHighlight() {
    const item = navigationState.items[navigationState.selectedIndex];
    if (item) {
        item.classList.add('keyboard-nav-selected');
        item.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
}

// Clear navigation highlight
function clearNavigationHighlight() {
    navigationState.items.forEach(item => {
        item.classList.remove('keyboard-nav-selected');
    });
}

// Clear navigation state
function clearNavigationState() {
    clearNavigationHighlight();
    navigationState.activePanel = null;
    navigationState.selectedIndex = -1;
    navigationState.items = [];
}

// Select the currently navigated item
function selectNavigatedItem() {
    const item = navigationState.items[navigationState.selectedIndex];
    if (!item) return;

    // Try to find ticker or trigger click
    const ticker = item.dataset.ticker ||
                   item.querySelector('.company-ticker, .mover-symbol')?.textContent?.trim();

    if (ticker) {
        openStockModal(ticker);
    } else {
        // Fallback: click the item
        item.click();
    }
}

// Switch market movers tab
function switchMarketTab(tab) {
    const tabBtn = document.querySelector(`.market-tab[data-tab="${tab}"]`);
    if (tabBtn) {
        tabBtn.click();
        showToast(`Showing ${tab}`, 'info');
    }
}

function focusPanel(elementId) {
    const element = document.getElementById(elementId);
    if (element) {
        element.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
}

function highlightPanel(selector) {
    // Try multiple selector strategies for compatibility
    let panel = document.querySelector(selector);
    
    // Fallback: try to find by looking for parent panel
    if (!panel && selector.includes('#')) {
        const id = selector.replace('#', '');
        const element = document.getElementById(id);
        if (element) {
            panel = element.closest('.panel') || element.parentElement;
        }
    }
    
    if (panel) {
        panel.classList.add('keyboard-focus');
        setTimeout(() => panel.classList.remove('keyboard-focus'), 1000);
    }
    
    return panel;
}

function openSearch() {
    // Open the advanced search overlay
    openSearchOverlay();
}

function setChartTimeframe(timeframe) {
    const map = {
        '1h': 1,
        '6h': 6,
        '24h': 24,
        '7d': 168
    };
    
    const hours = map[timeframe] || 24;
    
    // Update chart data
    updateMainChartWithTimeframe(hours);
    
    // Show feedback
    showToast(`Chart: Last ${timeframe}`, 'info');
}

async function updateMainChartWithTimeframe(hours) {
    try {
        const response = await fetchWithTimeout(`/api/timeline?hours=${hours}`);
        const data = await response.json();
        
        // Destroy existing chart
        if (mainChart) {
            mainChart.destroy();
        }
        
        // Render new chart
        const ctx = document.getElementById('mainChart').getContext('2d');
        renderMentionsChart(ctx, data);
        
    } catch (error) {
        console.error('Error updating chart:', error);
    }
}

// Help Overlay
function toggleHelpOverlay() {
    if (helpVisible) {
        hideHelpOverlay();
    } else {
        showHelpOverlay();
    }
}

function showHelpOverlay() {
    helpVisible = true;
    
    let overlay = document.getElementById('keyboardHelpOverlay');
    if (!overlay) {
        overlay = createHelpOverlay();
    }
    
    overlay.style.display = 'flex';
}

function hideHelpOverlay() {
    helpVisible = false;
    const overlay = document.getElementById('keyboardHelpOverlay');
    if (overlay) {
        overlay.style.display = 'none';
    }
}

function createHelpOverlay() {
    const overlay = document.createElement('div');
    overlay.id = 'keyboardHelpOverlay';
    overlay.className = 'help-overlay';

    // Group shortcuts by category
    const navigationShortcuts = Object.entries(KEYBOARD_SHORTCUTS)
        .filter(([key, val]) => val.category === 'navigation')
        .map(([key, val]) => `
            <div class="help-row">
                <span class="help-key">${key.toUpperCase()}</span>
                <span class="help-desc">${val.description}</span>
            </div>
        `).join('');

    const quickActions = Object.entries(KEYBOARD_SHORTCUTS)
        .filter(([key, val]) => val.category === 'quick')
        .map(([key, val]) => `
            <div class="help-row">
                <span class="help-key">${key}</span>
                <span class="help-desc">${val.description}</span>
            </div>
        `).join('');

    overlay.innerHTML = `
        <div class="help-content">
            <div class="help-header">
                <h2><i class="fas fa-keyboard"></i> KEYBOARD SHORTCUTS</h2>
                <button class="help-close" onclick="hideHelpOverlay()"><i class="fas fa-times"></i></button>
            </div>
            <div class="help-columns">
                <div class="help-column">
                    <div class="help-section">
                        <h3>NAVIGATION</h3>
                        ${navigationShortcuts}
                    </div>
                    <div class="help-section">
                        <h3>SEARCH</h3>
                        <div class="help-row"><span class="help-key">/</span><span class="help-desc">Open Search Overlay</span></div>
                        <div class="help-row"><span class="help-key">Ctrl+K</span><span class="help-desc">Open Search Overlay</span></div>
                        <div class="help-row"><span class="help-key">ESC</span><span class="help-desc">Close Any Modal/Overlay</span></div>
                    </div>
                </div>
                <div class="help-column">
                    <div class="help-section">
                        <h3>QUICK ACTIONS (1-9)</h3>
                        ${quickActions}
                    </div>
                    <div class="help-section">
                        <h3>LIST NAVIGATION</h3>
                        <div class="help-row"><span class="help-key"><i class="fas fa-arrow-up"></i></span><span class="help-desc">Navigate Up</span></div>
                        <div class="help-row"><span class="help-key"><i class="fas fa-arrow-down"></i></span><span class="help-desc">Navigate Down</span></div>
                        <div class="help-row"><span class="help-key">Enter</span><span class="help-desc">Select/Open Item</span></div>
                    </div>
                    <div class="help-section">
                        <h3>HELP</h3>
                        <div class="help-row"><span class="help-key">?</span><span class="help-desc">Show This Help</span></div>
                        <div class="help-row"><span class="help-key">H</span><span class="help-desc">Show This Help</span></div>
                    </div>
                </div>
            </div>
            <div class="help-footer">
                <span>Press <kbd>?</kbd> or <kbd>H</kbd> to toggle help</span>
                <span class="help-separator">|</span>
                <span>Press <kbd>ESC</kbd> to close</span>
            </div>
        </div>
    `;

    // Close on escape or clicking outside
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) hideHelpOverlay();
    });

    document.body.appendChild(overlay);
    return overlay;
}

// Initialize on load
document.addEventListener('DOMContentLoaded', initKeyboardShortcuts);

// Hide any chart elements (chart replaced with news ticker)
function hideChartElements() {
    console.log('[Dashboard] Hiding/removing ALL chart elements...');
    
    // Remove ALL canvas elements (not just hide)
    const canvases = document.querySelectorAll('canvas');
    console.log(`[Dashboard] Found ${canvases.length} canvas elements - removing all`);
    canvases.forEach(canvas => {
        console.log(`[Dashboard] Removing canvas: ${canvas.id || 'unnamed'}`);
        canvas.remove();
    });
    
    // Hide any chart containers
    const chartContainers = document.querySelectorAll('.chart-container, .mini-chart, .sentiment-chart-container');
    console.log(`[Dashboard] Found ${chartContainers.length} chart containers - hiding all`);
    chartContainers.forEach(container => {
        container.style.display = 'none';
        container.style.visibility = 'hidden';
    });
    
    // Ensure ticker is visible and at correct position
    const ticker = document.querySelector('.news-ticker-container');
    if (ticker) {
        ticker.style.display = 'flex';
        ticker.style.visibility = 'visible';
        ticker.style.opacity = '1';
        ticker.style.zIndex = '9999';
        console.log('[Dashboard] Ticker is visible');
    } else {
        console.error('[Dashboard] Ticker element NOT FOUND!');
    }
}

// Make functions globally accessible
window.hideHelpOverlay = hideHelpOverlay;
window.toggleHelpOverlay = toggleHelpOverlay;
window.executeCommand = executeCommand;
window.lookupStock = lookupStock;
window.closeStockDetails = closeStockDetails;
window.showEventDetails = showEventDetails;
window.closeAllOverlays = closeAllOverlays;
window.switchMarketTab = switchMarketTab;
window.handleEconomicCalendarShortcut = handleEconomicCalendarShortcut;


// ============================================================================
// Economic Calendar
// ============================================================================

// Major US economic events - recurring schedule with approximate times
const ECONOMIC_EVENTS_SCHEDULE = [
    // Weekly events
    { day: 3, time: '08:30', name: 'Initial Jobless Claims', country: 'US', impact: 'medium', type: 'employment' },
    
    // Monthly events (approximate dates - will be filtered by actual dates)
    { name: 'Nonfarm Payrolls', country: 'US', impact: 'high', type: 'employment', dayOfMonth: 1 },
    { name: 'Unemployment Rate', country: 'US', impact: 'high', type: 'employment', dayOfMonth: 1 },
    { name: 'CPI (MoM)', country: 'US', impact: 'high', type: 'inflation', dayOfMonth: 10 },
    { name: 'CPI (YoY)', country: 'US', impact: 'high', type: 'inflation', dayOfMonth: 10 },
    { name: 'PPI (MoM)', country: 'US', impact: 'medium', type: 'inflation', dayOfMonth: 12 },
    { name: 'Core CPI', country: 'US', impact: 'high', type: 'inflation', dayOfMonth: 10 },
    { name: 'Retail Sales', country: 'US', impact: 'medium', type: 'economic', dayOfMonth: 15 },
    { name: 'Industrial Production', country: 'US', impact: 'low', type: 'economic', dayOfMonth: 15 },
    { name: 'Housing Starts', country: 'US', impact: 'medium', type: 'housing', dayOfMonth: 17 },
    { name: 'Building Permits', country: 'US', impact: 'medium', type: 'housing', dayOfMonth: 17 },
    { name: 'FOMC Meeting', country: 'US', impact: 'high', type: 'interest-rate', dayOfMonth: 18, notes: '8x per year' },
    { name: 'Fed Interest Rate Decision', country: 'US', impact: 'high', type: 'interest-rate', dayOfMonth: 18, notes: '8x per year' },
    { name: 'GDP (QoQ)', country: 'US', impact: 'high', type: 'gdp', dayOfMonth: 25, notes: 'Quarterly' },
    { name: 'Trade Balance', country: 'US', impact: 'low', type: 'economic', dayOfMonth: 5 },
    { name: 'Consumer Confidence', country: 'US', impact: 'medium', type: 'sentiment', dayOfMonth: 25 },
    { name: 'ISM Manufacturing', country: 'US', impact: 'medium', type: 'economic', dayOfMonth: 1 },
    { name: 'ISM Services', country: 'US', impact: 'medium', type: 'economic', dayOfMonth: 3 },
    { name: 'Personal Income', country: 'US', impact: 'low', type: 'economic', dayOfMonth: 28 },
    { name: 'PCE Price Index', country: 'US', impact: 'high', type: 'inflation', dayOfMonth: 28 },
    { name: 'Core PCE', country: 'US', impact: 'high', type: 'inflation', dayOfMonth: 28 },
    { name: 'Durable Goods Orders', country: 'US', impact: 'medium', type: 'economic', dayOfMonth: 25 },
    { name: 'New Home Sales', country: 'US', impact: 'medium', type: 'housing', dayOfMonth: 23 },
    { name: 'Existing Home Sales', country: 'US', impact: 'medium', type: 'housing', dayOfMonth: 20 },
    { name: 'PMI Composite', country: 'US', impact: 'medium', type: 'economic', dayOfMonth: 22 },
    { name: 'Factory Orders', country: 'US', impact: 'low', type: 'economic', dayOfMonth: 3 },
    { name: 'Business Inventories', country: 'US', impact: 'low', type: 'economic', dayOfMonth: 15 },
    { name: 'Capacity Utilization', country: 'US', impact: 'low', type: 'economic', dayOfMonth: 15 },
    { name: 'Current Account', country: 'US', impact: 'low', type: 'economic', dayOfMonth: 20, notes: 'Quarterly' },
    { name: 'Philadelphia Fed', country: 'US', impact: 'medium', type: 'economic', dayOfMonth: 18 },
    { name: 'Empire State Manufacturing', country: 'US', impact: 'medium', type: 'economic', dayOfMonth: 15 },
    { name: 'Chicago PMI', country: 'US', impact: 'medium', type: 'economic', dayOfMonth: 28 },
    { name: 'Michigan Consumer Sentiment', country: 'US', impact: 'medium', type: 'sentiment', dayOfMonth: 10 },
    { name: 'Michigan Inflation Expectations', country: 'US', impact: 'medium', type: 'inflation', dayOfMonth: 10 },
];

// Mock historical data for events (for realistic display)
const MOCK_EVENT_HISTORY = {
    'Nonfarm Payrolls': { previous: '256K', forecast: '185K', actual: null },
    'Unemployment Rate': { previous: '4.1%', forecast: '4.2%', actual: null },
    'CPI (MoM)': { previous: '0.3%', forecast: '0.2%', actual: null },
    'CPI (YoY)': { previous: '2.9%', forecast: '2.7%', actual: null },
    'Core CPI': { previous: '0.3%', forecast: '0.3%', actual: null },
    'PPI (MoM)': { previous: '0.2%', forecast: '0.1%', actual: null },
    'Retail Sales': { previous: '0.4%', forecast: '0.3%', actual: null },
    'FOMC Meeting': { previous: '4.50%', forecast: '4.50%', actual: null },
    'Fed Interest Rate Decision': { previous: '4.50%', forecast: '4.50%', actual: null },
    'GDP (QoQ)': { previous: '2.8%', forecast: '2.3%', actual: null },
    'Initial Jobless Claims': { previous: '217K', forecast: '215K', actual: null },
    'Housing Starts': { previous: '1.36M', forecast: '1.35M', actual: null },
    'Building Permits': { previous: '1.48M', forecast: '1.46M', actual: null },
    'Consumer Confidence': { previous: '104.1', forecast: '105.0', actual: null },
    'ISM Manufacturing': { previous: '49.2', forecast: '49.5', actual: null },
    'ISM Services': { previous: '52.7', forecast: '52.5', actual: null },
    'PCE Price Index': { previous: '0.2%', forecast: '0.2%', actual: null },
    'Core PCE': { previous: '0.1%', forecast: '0.2%', actual: null },
    'Durable Goods Orders': { previous: '-0.8%', forecast: '0.5%', actual: null },
    'New Home Sales': { previous: '698K', forecast: '680K', actual: null },
    'Existing Home Sales': { previous: '4.15M', forecast: '4.10M', actual: null },
    'Philadelphia Fed': { previous: '-10.6', forecast: '-5.0', actual: null },
    'Empire State Manufacturing': { previous: '-12.4', forecast: '-8.0', actual: null },
    'Michigan Consumer Sentiment': { previous: '73.0', forecast: '74.0', actual: null },
};

// Initialize Economic Calendar
function initEconomicCalendar(retryCount = 0) {
    console.log('[EconCalendar] initEconomicCalendar() called, retry:', retryCount);
    console.log('[EconCalendar] ECONOMIC_EVENTS_SCHEDULE length:', typeof ECONOMIC_EVENTS_SCHEDULE !== 'undefined' ? ECONOMIC_EVENTS_SCHEDULE.length : 'UNDEFINED!');
    
    // Wait a bit for DOM to be ready, then load
    setTimeout(() => {
        loadEconomicCalendar();
    }, 500);
    
    // Refresh calendar every 5 minutes
    setInterval(loadEconomicCalendar, 300000);
}

// Load and display economic calendar
async function loadEconomicCalendar() {
    console.log('[EconCalendar] loadEconomicCalendar() called');
    
    // Try economicCalendar first (in Market Monitor), then economicCalendarStrip (old location)
    let container = document.getElementById('economicCalendar') || document.getElementById('economicCalendarStrip');
    console.log('[EconCalendar] Container found:', !!container);
    
    if (!container) {
        console.error('[EconCalendar] ERROR: Container not found!');
        return;
    }
    
    try {
        // Try to fetch from API first
        let events = [];
        try {
            console.log('[EconCalendar] Trying API...');
            const response = await fetchWithTimeout('/api/economic-calendar', {}, 5000);
            const data = await response.json();
            // API returns { events: [...] } - extract the array
            events = data.events || [];
            console.log('[EconCalendar] Got', events.length, 'events from API');
        } catch (apiError) {
            console.log('[EconCalendar] API unavailable, using generated data:', apiError.message);
            events = generateEconomicEvents();
            console.log('[EconCalendar] Generated', events.length, 'events');
        }
        
        renderEconomicCalendarStrip(container, events);
    } catch (error) {
        console.error('[EconCalendar] Error loading:', error);
        container.innerHTML = `<div class="econ-strip-empty">Unable to load calendar</div>`;
    }
}

// Generate realistic economic events for the next 7 days
function generateEconomicEvents() {
    console.log('[EconCalendar] generateEconomicEvents() called');
    const events = [];
    const now = new Date();
    const currentHour = now.getHours();
    
    // Check if schedule is defined
    if (typeof ECONOMIC_EVENTS_SCHEDULE === 'undefined') {
        console.error('[EconCalendar] ERROR: ECONOMIC_EVENTS_SCHEDULE is undefined!');
        return generateSampleEvents(); // Fallback
    }
    
    console.log('[EconCalendar] Schedule has', ECONOMIC_EVENTS_SCHEDULE.length, 'templates');
    
    // Generate events for next 7 days
    for (let i = 0; i < 7; i++) {
        const date = new Date(now);
        date.setDate(date.getDate() + i);
        
        const dayOfWeek = date.getDay(); // 0 = Sunday, 1 = Monday, etc.
        const dayOfMonth = date.getDate();
        const month = date.getMonth() + 1;
        const year = date.getFullYear();
        const dateStr = `${year}-${month.toString().padStart(2, '0')}-${dayOfMonth.toString().padStart(2, '0')}`;
        
        // Skip weekends
        if (dayOfWeek === 0 || dayOfWeek === 6) continue;
        
        // Add scheduled events that match this date
        ECONOMIC_EVENTS_SCHEDULE.forEach(template => {
            // Check if this event should appear on this day
            let shouldInclude = false;
            
            if (template.day !== undefined && template.day === dayOfWeek) {
                // Weekly event (e.g., Jobless Claims on Thursday = 4)
                shouldInclude = true;
            } else if (template.dayOfMonth !== undefined) {
                // Monthly event - use approximate date with some variance
                const variance = Math.abs(template.dayOfMonth - dayOfMonth);
                if (variance <= 2) {
                    shouldInclude = true;
                }
            }
            
            if (shouldInclude) {
                const history = MOCK_EVENT_HISTORY[template.name] || {};
                const time = template.time || getRandomMarketTime();
                
                events.push({
                    id: `${template.name}-${dateStr}`,
                    name: template.name,
                    country: template.country,
                    date: dateStr,
                    time: time,
                    impact: template.impact,
                    type: template.type,
                    previous: history.previous || null,
                    forecast: history.forecast || null,
                    actual: i === 0 && time <= `${currentHour}:00` ? getMockActual(template.name) : null,
                    notes: template.notes || null
                });
            }
        });
    }
    
    console.log('[EconCalendar] Generated', events.length, 'raw events');
    
    // Sort by date and time
    events.sort((a, b) => {
        const dateA = new Date(`${a.date}T${a.time}`);
        const dateB = new Date(`${b.date}T${b.time}`);
        return dateA - dateB;
    });
    
    return events.slice(0, 15); // Limit to 15 events
}

// Fallback: Generate sample events if main schedule fails
function generateSampleEvents() {
    console.log('[EconCalendar] generateSampleEvents() called - using fallback');
    const events = [];
    const now = new Date();
    
    const sampleEvents = [
        { name: 'Fed Interest Rate Decision', impact: 'high', type: 'interest-rate' },
        { name: 'Nonfarm Payrolls', impact: 'high', type: 'employment' },
        { name: 'CPI (MoM)', impact: 'high', type: 'inflation' },
        { name: 'Initial Jobless Claims', impact: 'medium', type: 'employment' },
        { name: 'Retail Sales', impact: 'medium', type: 'economic' },
        { name: 'ISM Manufacturing', impact: 'medium', type: 'economic' }
    ];
    
    // Generate one event per day for next 5 days
    for (let i = 0; i < 5; i++) {
        const date = new Date(now);
        date.setDate(date.getDate() + i);
        
        // Skip weekends
        if (date.getDay() === 0 || date.getDay() === 6) continue;
        
        const year = date.getFullYear();
        const month = String(date.getMonth() + 1).padStart(2, '0');
        const day = String(date.getDate()).padStart(2, '0');
        const dateStr = `${year}-${month}-${day}`;
        
        const template = sampleEvents[i % sampleEvents.length];
        const hour = 8 + Math.floor(Math.random() * 8); // 8am-4pm
        const time = `${hour.toString().padStart(2, '0')}:30`;
        
        events.push({
            id: `${template.name}-${dateStr}`,
            name: template.name,
            country: 'US',
            date: dateStr,
            time: time,
            impact: template.impact,
            type: template.type,
            previous: null,
            forecast: null,
            actual: null,
            notes: null
        });
    }
    
    return events;
}

// Get random market hours time (8:30 AM - 4:00 PM EST)
function getRandomMarketTime() {
    const hours = [8, 9, 10, 13, 14, 16];
    const hour = hours[Math.floor(Math.random() * hours.length)];
    const minute = Math.random() > 0.5 ? '00' : '30';
    return `${hour.toString().padStart(2, '0')}:${minute}`;
}

// Generate mock actual value based on forecast/previous
function getMockActual(eventName) {
    const history = MOCK_EVENT_HISTORY[eventName];
    if (!history || !history.forecast) return null;
    
    // Random variation around forecast
    const forecast = parseFloat(history.forecast);
    if (isNaN(forecast)) return null;
    
    const variation = (Math.random() - 0.5) * 0.4; // ±20% variation
    const actual = forecast * (1 + variation);
    
    // Format similar to forecast
    if (history.forecast.includes('%')) {
        return actual.toFixed(1) + '%';
    } else if (history.forecast.includes('K')) {
        return Math.round(actual) + 'K';
    } else if (history.forecast.includes('M')) {
        return (actual / 1000000).toFixed(2) + 'M';
    }
    return actual.toFixed(1);
}

// Market impact data for major events
const EVENT_IMPACT_DATA = {
    'Nonfarm Payrolls': { avgMove: '±0.8%', description: 'Monthly employment report - major market mover' },
    'Unemployment Rate': { avgMove: '±0.6%', description: 'Percentage of unemployed workers' },
    'CPI (MoM)': { avgMove: '±1.2%', description: 'Consumer Price Index - key inflation gauge' },
    'CPI (YoY)': { avgMove: '±1.2%', description: 'Annual inflation rate' },
    'Core CPI': { avgMove: '±1.0%', description: 'CPI excluding food and energy' },
    'PPI (MoM)': { avgMove: '±0.7%', description: 'Producer Price Index - wholesale inflation' },
    'Fed Interest Rate Decision': { avgMove: '±1.5%', description: 'FOMC rate decision - major volatility expected' },
    'FOMC Meeting': { avgMove: '±1.5%', description: 'Federal Reserve policy meeting' },
    'GDP (QoQ)': { avgMove: '±0.8%', description: 'Quarterly economic growth rate' },
    'Retail Sales': { avgMove: '±0.6%', description: 'Consumer spending indicator' },
    'Initial Jobless Claims': { avgMove: '±0.4%', description: 'Weekly unemployment claims' },
    'PCE Price Index': { avgMove: '±0.8%', description: 'Fed\'s preferred inflation measure' },
    'Core PCE': { avgMove: '±0.9%', description: 'Core PCE - Fed inflation target' },
    'ISM Manufacturing': { avgMove: '±0.7%', description: 'Manufacturing sector health' },
    'ISM Services': { avgMove: '±0.6%', description: 'Services sector health' },
    'Consumer Confidence': { avgMove: '±0.5%', description: 'Consumer sentiment indicator' }
};

// Get timezone abbreviation
function getTimezoneAbbr() {
    const date = new Date();
    const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    // Check if we're in Eastern Time
    if (timeZone.includes('New_York') || timeZone.includes('Eastern')) {
        const isDST = date.getTimezoneOffset() === 240; // EDT is UTC-4 (240 min)
        return isDST ? 'EDT' : 'EST';
    }
    // Return generic offset if not Eastern
    const offset = -date.getTimezoneOffset() / 60;
    return offset >= 0 ? `UTC+${offset}` : `UTC${offset}`;
}

// Get date label (TODAY, TOMORROW, or day name)
function getDateLabel(dateStr, todayStr) {
    if (dateStr === todayStr) return 'TODAY';
    
    const today = new Date(todayStr);
    const tomorrow = new Date(today);
    tomorrow.setDate(tomorrow.getDate() + 1);
    const tomorrowStr = tomorrow.toISOString().split('T')[0];
    
    if (dateStr === tomorrowStr) return 'TOMORROW';
    
    // Return day name for other dates
    const date = new Date(dateStr);
    return date.toLocaleDateString('en-US', { weekday: 'long' }).toUpperCase();
}

// Render economic calendar with date grouping and details
function renderEconomicCalendarStrip(container, events) {
    console.log('[EconCalendar] renderEconomicCalendarStrip() called with', events ? events.length : 0, 'events');
    
    if (!events || events.length === 0) {
        container.innerHTML = `<div class="econ-strip-empty">No events scheduled</div>`;
        return;
    }
    
    const now = new Date();
    const year = now.getFullYear();
    const month = String(now.getMonth() + 1).padStart(2, '0');
    const day = String(now.getDate()).padStart(2, '0');
    const today = `${year}-${month}-${day}`;
    const tzAbbr = getTimezoneAbbr();
    
    // Filter to upcoming events, max 8
    let upcomingEvents = events.filter(e => e.date >= today).slice(0, 8);
    if (upcomingEvents.length === 0) {
        upcomingEvents = events.slice(0, 8);
    }
    
    // Group by date
    let currentDateLabel = null;
    
    const html = upcomingEvents.map((event, index) => {
        const isToday = event.date === today;
        const timeDisplay = formatTime12h(event.time);
        const dateLabel = getDateLabel(event.date, today);
        const showDateHeader = dateLabel !== currentDateLabel;
        currentDateLabel = dateLabel;
        
        const impactDot = event.impact === 'high' ? '●' : event.impact === 'medium' ? '◐' : '○';
        const impactData = EVENT_IMPACT_DATA[event.name] || { avgMove: '', description: 'Economic data release' };
        
        const dateHeader = showDateHeader ? `<div class="econ-date-header">${dateLabel}</div>` : '';
        
        return dateHeader + `
            <div class="econ-item ${event.impact}" data-event-index="${index}" onclick="showEventDetails(${index})">
                <span class="econ-time">${timeDisplay} ${tzAbbr}</span>
                <span class="econ-impact">${impactDot}</span>
                <span class="econ-name">${event.name}</span>
            </div>
            <div id="event-details-${index}" class="econ-details-panel" style="display: none;">
                <div class="econ-details-content">
                    <div class="econ-details-desc">${impactData.description}</div>
                    ${event.forecast ? `<div class="econ-details-forecast">Forecast: ${event.forecast}</div>` : ''}
                    ${event.previous ? `<div class="econ-details-previous">Previous: ${event.previous}</div>` : ''}
                </div>
            </div>
        `;
    }).join('');
    
    container.innerHTML = html;
}

// Show/hide event details
function showEventDetails(index) {
    const detailsPanel = document.getElementById(`event-details-${index}`);
    if (!detailsPanel) return;
    
    // Hide all other detail panels
    document.querySelectorAll('.econ-details-panel').forEach(panel => {
        if (panel.id !== `event-details-${index}`) {
            panel.style.display = 'none';
        }
    });
    
    // Toggle this panel
    const isVisible = detailsPanel.style.display === 'block';
    detailsPanel.style.display = isVisible ? 'none' : 'block';
}

// Format time to 12-hour format
function formatTime12h(time24h) {
    if (!time24h) return '--:--';
    const [hours, minutes] = time24h.split(':');
    const hour = parseInt(hours, 10);
    const ampm = hour >= 12 ? 'PM' : 'AM';
    const hour12 = hour % 12 || 12;
    return `${hour12}:${minutes} ${ampm}`;
}

// Handle economic calendar shortcut (used by 'e' key)
function handleEconomicCalendarShortcut() {
    const container = document.getElementById('economicCalendar');
    if (container) {
        container.scrollIntoView({ behavior: 'smooth', block: 'center' });
        highlightPanel('.economic-calendar-section');
        showToast('Economic calendar focused', 'info');
    }
}




// ============================================================================
// Stock Detail Modal
// ============================================================================

let currentModalTicker = null;
let stockChartInstance = null;
let rsiChartInstance = null;
let macdChartInstance = null;
let currentChartPeriod = '1mo';
// Note: currentChartType already declared at top of file
let currentChartData = null;
let activeIndicators = {
    bollinger: false,
    rsi: false,
    macd: false
};

// Store original modal content for restoration
let originalModalContent = null;

// =============================================================================
// Preload Watchlist Data Functions
// =============================================================================

/**
 * Preload detailed stock data for watchlist stocks in background.
 * This allows instant modal opening for frequently viewed stocks.
 */
async function preloadWatchlistData() {
    if (preloadInProgress) {
        console.log('[Preload] Already in progress, skipping');
        return;
    }

    preloadInProgress = true;
    console.log('[Preload] Starting background preload of watchlist data...');

    try {
        const response = await fetchWithTimeout('/api/preload/watchlist', {}, 15000);
        const data = await response.json();

        if (data.stocks) {
            preloadedStockData = { ...preloadedStockData, ...data.stocks };
            console.log(`[Preload] Preloaded ${data.preloaded} stocks successfully`);
        }
    } catch (error) {
        console.warn('[Preload] Background preload failed:', error.message);
    } finally {
        preloadInProgress = false;
    }
}

/**
 * Check if we have preloaded data for a ticker
 */
function hasPreloadedData(ticker) {
    return ticker in preloadedStockData;
}

/**
 * Get preloaded data for a ticker
 */
function getPreloadedData(ticker) {
    return preloadedStockData[ticker] || null;
}

/**
 * Open the stock detail modal for a given ticker
 * Uses preloaded data if available for instant display
 * @param {string} ticker - The stock ticker symbol
 */
async function openStockModal(ticker) {
    if (!ticker) return;

    ticker = ticker.toUpperCase().trim();
    currentModalTicker = ticker;
    currentChartPeriod = '1mo';

    // Reset lazy load state for tabs
    Object.keys(tabLoadState).forEach(key => tabLoadState[key] = false);

    const modal = document.getElementById('stockDetailModal');
    if (!modal) {
        console.error('[StockModal] Modal element not found');
        return;
    }

    // Save original content if not already saved
    const content = modal.querySelector('.stock-modal-content');
    if (content && !originalModalContent) {
        originalModalContent = content.innerHTML;
    }

    // Show modal immediately
    modal.style.display = 'flex';

    // Check for preloaded data first for instant display
    const preloaded = getPreloadedData(ticker);
    if (preloaded) {
        console.log(`[StockModal] Using preloaded data for ${ticker}`);
        // Show preloaded data immediately while fetching full details
        showPartialData(preloaded);
    } else {
        showModalLoading();
    }

    try {
        // Fetch full stock details
        const response = await fetchWithTimeout(`/api/stock/${ticker}`);
        const data = await response.json();

        if (data.error) {
            showModalError(data.error);
            return;
        }

        // Store for lazy tab loading
        currentStockData = data;

        // Populate modal with full data (only Overview tab initially)
        populateModalData(data);
        tabLoadState.overview = true;

        // Only load chart for overview tab (lazy load others on tab click)
        await loadAndRenderChart(ticker, currentChartPeriod);
        tabLoadState.chart = true;

        // News will be loaded lazily when News tab is clicked

    } catch (error) {
        console.error('[StockModal] Error loading stock data:', error);
        if (!preloaded) {
            showModalError('Failed to load stock data');
        }
    }
}

/**
 * Show partial data from preload while full data loads
 */
function showPartialData(data) {
    const modal = document.getElementById('stockDetailModal');
    if (!modal) return;

    // Restore full modal structure if needed
    const content = modal.querySelector('.stock-modal-content');
    if (content && originalModalContent) {
        content.innerHTML = originalModalContent;
    }

    // Update header info from preloaded data
    const symbolEl = document.getElementById('modalSymbol');
    const nameEl = document.getElementById('modalName');
    const priceEl = document.getElementById('modalPrice');
    const changeEl = document.getElementById('modalChange');

    if (symbolEl) symbolEl.textContent = data.ticker;
    if (nameEl) nameEl.textContent = data.name || data.ticker;
    if (priceEl) priceEl.textContent = `$${data.price}`;

    if (changeEl) {
        const isUp = data.change >= 0;
        const arrow = isUp ? '▲' : '▼';
        changeEl.textContent = `${arrow} ${data.change >= 0 ? '+' : ''}${data.change} (${data.change_percent >= 0 ? '+' : ''}${data.change_percent}%)`;
        changeEl.className = `stock-modal-change ${isUp ? 'up' : 'down'}`;
    }

    // Show loading indicators for other fields
    const loadingFields = ['modalMarketCap', 'modalVolume', 'modalAvgVol', 'modalBeta'];
    loadingFields.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.innerHTML = '<i class="fas fa-spinner fa-spin" style="opacity: 0.5;"></i>';
    });
}

/**
 * Close the stock detail modal
 */
function closeStockModal() {
    const modal = document.getElementById('stockDetailModal');
    if (modal) {
        modal.style.display = 'none';
    }

    // Destroy chart instances
    if (stockChartInstance) {
        stockChartInstance.destroy();
        stockChartInstance = null;
    }
    if (rsiChartInstance) {
        rsiChartInstance.destroy();
        rsiChartInstance = null;
    }
    if (macdChartInstance) {
        macdChartInstance.destroy();
        macdChartInstance = null;
    }

    // Reset indicator states
    activeIndicators = { bollinger: false, rsi: false, macd: false };
    currentChartData = null;
    currentChartType = 'line';
    currentModalTicker = null;

    // Hide indicator panels
    const rsiPanel = document.getElementById('rsiPanel');
    if (rsiPanel) rsiPanel.style.display = 'none';
    const macdPanel = document.getElementById('macdPanel');
    if (macdPanel) macdPanel.style.display = 'none';

    // Reset indicator buttons
    document.querySelectorAll('.indicator-toggle-btn').forEach(btn => {
        btn.classList.remove('active');
    });
    document.querySelectorAll('.chart-type-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.type === 'line');
    });
}

/**
 * Show loading state in modal
 */
function showModalLoading() {
    const content = document.querySelector('.stock-modal-content');
    if (content) {
        content.innerHTML = `
            <div class="stock-modal-loading">
                <i class="fas fa-spinner fa-spin"></i>
                <span>Loading stock data...</span>
            </div>
        `;
    }
}

/**
 * Show error state in modal
 */
function showModalError(message) {
    const content = document.querySelector('.stock-modal-content');
    if (content) {
        content.innerHTML = `
            <div class="stock-modal-error">
                <i class="fas fa-exclamation-circle"></i>
                <span>${message}</span>
                <button class="btn" onclick="closeStockModal()" style="margin-top: 16px;">
                    <i class="fas fa-times"></i> Close
                </button>
            </div>
        `;
    }
}

/**
 * Populate modal with stock data
 */
function populateModalData(data) {
    const modal = document.getElementById('stockDetailModal');
    if (!modal) return;

    // Restore full modal structure if it was replaced by loading state
    const content = modal.querySelector('.stock-modal-content');
    if (content && originalModalContent) {
        content.innerHTML = originalModalContent;
    }

    // Helper to safely set element text
    const setText = (id, value, prefix = '', suffix = '') => {
        const el = document.getElementById(id);
        if (el) {
            el.textContent = (value && value !== 'N/A') ? `${prefix}${value}${suffix}` : 'N/A';
        }
    };

    // Helper to format large numbers
    const formatLargeNum = (num) => {
        if (!num || num === 'N/A') return 'N/A';
        if (typeof num === 'string') return num;
        if (num >= 1e12) return `${(num / 1e12).toFixed(2)}T`;
        if (num >= 1e9) return `${(num / 1e9).toFixed(2)}B`;
        if (num >= 1e6) return `${(num / 1e6).toFixed(2)}M`;
        return num.toLocaleString();
    };

    // Reset to first tab
    switchStockTab('overview');

    // Update header info
    document.getElementById('modalSymbol').textContent = data.ticker;
    document.getElementById('modalName').textContent = data.name || data.ticker;

    // Update price
    const priceEl = document.getElementById('modalPrice');
    priceEl.textContent = `$${data.price}`;

    // Update change
    const changeEl = document.getElementById('modalChange');
    const isUp = data.change >= 0;
    const arrow = isUp ? '▲' : '▼';
    changeEl.textContent = `${arrow} ${data.change >= 0 ? '+' : ''}${data.change} (${data.change_percent >= 0 ? '+' : ''}${data.change_percent}%)`;
    changeEl.className = `stock-modal-change ${isUp ? 'up' : 'down'}`;

    // ========== OVERVIEW TAB ==========
    setText('modalMarketCap', data.market_cap);
    setText('modalVolume', formatVolume(data.volume));
    setText('modalAvgVol', formatVolume(data.avg_volume));
    setText('modalBeta', data.beta);
    setText('modal52High', data['52_week_high'], '$');
    setText('modal52Low', data['52_week_low'], '$');
    setText('modalOpen', data.open, '$');
    setText('modalPrevClose', data.previous_close, '$');

    setText('modal50DayAvg', data.fifty_day_avg, '$');
    setText('modal200DayAvg', data.two_hundred_day_avg, '$');

    // 52 week change with color
    const change52El = document.getElementById('modal52WChange');
    if (change52El && data.fifty_two_week_change && data.fifty_two_week_change !== 'N/A') {
        change52El.textContent = `${data.fifty_two_week_change >= 0 ? '+' : ''}${data.fifty_two_week_change}%`;
        change52El.style.color = data.fifty_two_week_change >= 0 ? '#00c853' : '#ff5252';
    } else if (change52El) {
        change52El.textContent = 'N/A';
        change52El.style.color = '';
    }

    // Bid/Ask
    const bidAskEl = document.getElementById('modalBidAsk');
    if (bidAskEl) {
        if (data.bid && data.ask && data.bid !== 'N/A' && data.ask !== 'N/A') {
            bidAskEl.textContent = `${data.bid} / ${data.ask}`;
        } else {
            bidAskEl.textContent = 'N/A';
        }
    }

    // Day Range
    const dayRangeEl = document.getElementById('modalDayRange');
    if (dayRangeEl) {
        if (data.day_low && data.day_high && data.day_low !== 'N/A' && data.day_high !== 'N/A') {
            dayRangeEl.textContent = `${data.day_low} - ${data.day_high}`;
        } else {
            dayRangeEl.textContent = 'N/A';
        }
    }

    setText('modalEPS', data.eps, '$');
    setText('modalDivYield', data.dividend_yield, '', '%');
    setText('modalEarningsDate', data.earnings_date);

    // ========== VALUATION TAB ==========
    setText('modalPE', data.pe_ratio);
    setText('modalForwardPE', data.forward_pe);
    setText('modalPEG', data.peg_ratio);
    setText('modalPB', data.price_to_book);
    setText('modalPS', data.price_to_sales);
    setText('modalEVRevenue', data.ev_to_revenue);
    setText('modalEVEBITDA', data.ev_to_ebitda);
    setText('modalEV', data.enterprise_value);

    // Growth with colors
    const setGrowth = (id, value) => {
        const el = document.getElementById(id);
        if (el && value && value !== 'N/A') {
            el.textContent = `${value >= 0 ? '+' : ''}${value}%`;
            el.style.color = value >= 0 ? '#00c853' : '#ff5252';
        } else if (el) {
            el.textContent = 'N/A';
            el.style.color = '';
        }
    };
    setGrowth('modalRevGrowth', data.revenue_growth);
    setGrowth('modalEarningsGrowth', data.earnings_growth);
    setGrowth('modalQtrGrowth', data.earnings_quarterly_growth);
    setText('modalForwardEPS', data.forward_eps, '$');

    // ========== FINANCIALS TAB ==========
    setText('modalProfitMargin', data.profit_margin, '', '%');
    setText('modalOpMargin', data.operating_margin, '', '%');
    setText('modalGrossMargin', data.gross_margin, '', '%');
    setText('modalEBITDAMargin', data.ebitda_margin, '', '%');
    setText('modalROE', data.roe, '', '%');
    setText('modalROA', data.roa, '', '%');
    setText('modalRevenue', data.revenue);
    setText('modalNetIncome', data.net_income);

    setText('modalTotalCash', data.total_cash);
    setText('modalTotalDebt', data.total_debt);
    setText('modalDebtEquity', data.debt_to_equity, '', '%');
    setText('modalCurrentRatio', data.current_ratio);
    setText('modalQuickRatio', data.quick_ratio);
    setText('modalBookValue', data.book_value, '$');
    setText('modalFCF', data.free_cash_flow);
    setText('modalOpCashFlow', data.operating_cash_flow);

    setText('modalDivRate', data.dividend_rate, '$');
    setText('modalDivYieldPct', data.dividend_yield, '', '%');
    setText('modalPayoutRatio', data.payout_ratio, '', '%');
    setText('modal5YAvgYield', data.five_year_avg_dividend_yield, '', '%');

    // ========== OWNERSHIP TAB ==========
    setText('modalInsiderOwn', data.insider_ownership, '', '%');
    setText('modalInstOwn', data.institutional_ownership, '', '%');
    setText('modalSharesOut', formatLargeNum(data.shares_outstanding));
    setText('modalFloat', formatLargeNum(data.float_shares));

    setText('modalShortPct', data.short_percent_of_float, '', '%');
    setText('modalShortRatio', data.short_ratio);
    setText('modalSharesShort', formatLargeNum(data.shares_short));
    setText('modalSharesShortPrior', formatLargeNum(data.shares_short_prior));

    // Top Holders
    const holdersEl = document.getElementById('modalTopHolders');
    if (holdersEl) {
        if (data.top_holders && data.top_holders.length > 0) {
            holdersEl.innerHTML = data.top_holders.map(h => `
                <div class="holder-item">
                    <span class="holder-name">${h.name}</span>
                    <div class="holder-stats">
                        <div class="holder-stat">
                            <span class="holder-stat-label">Shares</span>
                            <span class="holder-stat-value">${formatLargeNum(h.shares)}</span>
                        </div>
                        <div class="holder-stat">
                            <span class="holder-stat-label">% Out</span>
                            <span class="holder-stat-value">${h.pct_out}%</span>
                        </div>
                    </div>
                </div>
            `).join('');
        } else {
            holdersEl.innerHTML = '<div class="holder-placeholder">No institutional holder data available</div>';
        }
    }

    // ========== ANALYSTS TAB ==========
    // Recommendation with color
    const recEl = document.getElementById('modalRecommendation');
    if (recEl) {
        if (data.recommendation && data.recommendation !== 'N/A') {
            const recMap = {
                'strong_buy': { text: 'Strong Buy', color: '#00c853' },
                'buy': { text: 'Buy', color: '#4caf50' },
                'hold': { text: 'Hold', color: '#ffc107' },
                'sell': { text: 'Sell', color: '#ff5722' },
                'strong_sell': { text: 'Strong Sell', color: '#f44336' }
            };
            const rec = recMap[data.recommendation] || { text: data.recommendation, color: '#888' };
            recEl.textContent = rec.text;
            recEl.style.color = rec.color;
        } else {
            recEl.textContent = 'N/A';
            recEl.style.color = '';
        }
    }

    setText('modalNumAnalysts', data.num_analysts);
    setText('modalTargetPrice', data.target_price, '$');
    setText('modalTargetHigh', data.target_high, '$');
    setText('modalTargetLow', data.target_low, '$');

    // Calculate upside
    const upsideEl = document.getElementById('modalUpside');
    if (upsideEl && data.target_price && data.target_price !== 'N/A' && data.price) {
        const upside = ((data.target_price - data.price) / data.price * 100).toFixed(1);
        upsideEl.textContent = `${upside >= 0 ? '+' : ''}${upside}%`;
        upsideEl.style.color = upside >= 0 ? '#00c853' : '#ff5252';
    } else if (upsideEl) {
        upsideEl.textContent = 'N/A';
        upsideEl.style.color = '';
    }

    // ========== COMPANY INFO ==========
    setText('modalExchange', data.exchange);
    setText('modalSector', data.sector);
    setText('modalIndustry', data.industry);
    setText('modalHQ', data.headquarters);
    setText('modalEmployees', data.employees ? formatNumber(data.employees) : 'N/A');

    // Update description
    document.getElementById('modalDescription').textContent = data.description || 'No description available.';

    // Update external links
    const websiteLink = document.getElementById('modalWebsite');
    if (websiteLink) {
        if (data.website) {
            websiteLink.href = data.website;
            websiteLink.style.display = 'flex';
        } else {
            websiteLink.style.display = 'none';
        }
    }

    const yahooLink = document.getElementById('modalYahooLink');
    if (yahooLink) {
        yahooLink.href = `https://finance.yahoo.com/quote/${data.ticker}`;
    }

    const secLink = document.getElementById('modalSECLink');
    if (secLink) {
        secLink.href = `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${data.ticker}&type=&dateb=&owner=include&count=40`;
    }
}

/**
 * Switch between stock modal tabs with lazy loading
 * Only loads tab data when the tab is clicked for the first time
 */
function switchStockTab(tabName) {
    // Update tab buttons
    document.querySelectorAll('.stock-tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.tab === tabName);
    });

    // Update tab content
    document.querySelectorAll('.stock-tab-content').forEach(content => {
        content.classList.toggle('active', content.id === `tab-${tabName}`);
    });

    // Lazy load tab data if not already loaded
    if (!tabLoadState[tabName] && currentStockData) {
        lazyLoadTabData(tabName, currentStockData);
    }
}

/**
 * Lazy load data for a specific tab
 */
async function lazyLoadTabData(tabName, data) {
    console.log(`[LazyLoad] Loading data for tab: ${tabName}`);

    const tabContent = document.getElementById(`tab-${tabName}`);
    if (!tabContent) return;

    // Show loading spinner in the tab
    const existingSpinner = tabContent.querySelector('.tab-loading-spinner');
    if (!existingSpinner && !tabLoadState[tabName]) {
        const spinner = document.createElement('div');
        spinner.className = 'tab-loading-spinner';
        spinner.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Loading...';
        spinner.style.cssText = 'text-align: center; padding: 20px; color: #888;';
        tabContent.insertBefore(spinner, tabContent.firstChild);
    }

    try {
        switch (tabName) {
            case 'news':
                // Load news lazily
                if (!tabLoadState.news && currentModalTicker) {
                    await loadRelatedNews(currentModalTicker);
                }
                break;

            case 'insiders':
                // Load insider transactions lazily
                if (!tabLoadState.insiders && currentModalTicker) {
                    await loadInsiderTransactions(currentModalTicker);
                }
                break;

            case 'valuation':
            case 'financials':
            case 'ownership':
            case 'analysts':
                // These tabs use data already fetched, just mark as loaded
                // The populateModalData already fills these
                break;
        }

        tabLoadState[tabName] = true;
    } catch (error) {
        console.error(`[LazyLoad] Error loading ${tabName} tab:`, error);
    } finally {
        // Remove spinner
        const spinner = tabContent.querySelector('.tab-loading-spinner');
        if (spinner) spinner.remove();
    }
}

// Make switchStockTab globally available
window.switchStockTab = switchStockTab;

/**
 * Load and render stock chart with technical indicators
 */
async function loadAndRenderChart(ticker, period) {
    try {
        // Map period to appropriate interval
        const intervalMap = {
            '1d': '5m',
            '5d': '30m',
            '1mo': '1d',
            '3mo': '1d',
            '1y': '1wk',
            '2y': '1mo',
            '5y': '1mo',
            'max': '3mo'
        };

        const interval = intervalMap[period] || '1d';

        const response = await fetchWithTimeout(`/api/stock/${ticker}/chart?period=${period}&interval=${interval}`);
        const data = await response.json();

        if (data.error) {
            console.error('[StockModal] Chart error:', data.error);
            return;
        }

        // Store chart data for re-rendering
        currentChartData = data;

        // Render main chart
        renderStockChart(data, period, currentChartType);

        // Render indicator charts if active
        if (activeIndicators.rsi) {
            renderRSIChart(data, period);
        }
        if (activeIndicators.macd) {
            renderMACDChart(data, period);
        }

    } catch (error) {
        console.error('[StockModal] Error loading chart:', error);
    }
}

/**
 * Render the stock chart using Chart.js
 * Supports line and candlestick chart types with Bollinger Bands overlay
 */
function renderStockChart(data, period, chartType = 'line') {
    const ctx = document.getElementById('stockChart');
    if (!ctx) return;

    // Destroy existing chart
    if (stockChartInstance) {
        stockChartInstance.destroy();
    }

    const ohlcData = data.data || data;
    if (!ohlcData || ohlcData.length === 0) {
        return;
    }

    // Format dates based on period
    const labels = ohlcData.map(d => {
        const date = new Date(d.date);
        if (period === '1d') {
            return date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
        } else if (period === '5d' || period === '1mo') {
            return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
        } else {
            return date.toLocaleDateString('en-US', { month: 'short', year: '2-digit' });
        }
    });

    const prices = ohlcData.map(d => d.close);

    // Determine color based on price trend
    const startPrice = prices[0];
    const endPrice = prices[prices.length - 1];
    const isUp = endPrice >= startPrice;
    const color = isUp ? '#00c851' : '#ff4444';

    // Build datasets
    const datasets = [];

    if (chartType === 'candlestick') {
        // Create candlestick-style visualization using bars
        const candleColors = ohlcData.map(d => d.close >= d.open ? '#00c851' : '#ff4444');
        const candleBorders = ohlcData.map(d => d.close >= d.open ? '#00a040' : '#cc3333');

        datasets.push({
            label: 'Price',
            data: prices,
            backgroundColor: candleColors,
            borderColor: candleBorders,
            borderWidth: 1,
            type: 'bar',
            barPercentage: 0.6,
            categoryPercentage: 0.8
        });
    } else {
        // Line chart
        datasets.push({
            label: 'Price',
            data: prices,
            borderColor: color,
            backgroundColor: isUp ? 'rgba(0, 200, 81, 0.1)' : 'rgba(255, 68, 68, 0.1)',
            borderWidth: 2,
            fill: true,
            tension: 0.4,
            pointRadius: 0,
            pointHoverRadius: 4,
            pointHoverBackgroundColor: color,
            pointHoverBorderColor: '#fff',
            pointHoverBorderWidth: 2
        });
    }

    // Add Bollinger Bands if active
    if (activeIndicators.bollinger && data.bollinger) {
        const bb = data.bollinger;

        datasets.push({
            label: 'BB Upper',
            data: bb.upper,
            borderColor: 'rgba(59, 130, 246, 0.6)',
            borderWidth: 1,
            borderDash: [5, 5],
            fill: false,
            pointRadius: 0,
            tension: 0.4,
            type: 'line'
        });

        datasets.push({
            label: 'BB Middle',
            data: bb.middle,
            borderColor: 'rgba(59, 130, 246, 0.4)',
            borderWidth: 1,
            fill: false,
            pointRadius: 0,
            tension: 0.4,
            type: 'line'
        });

        datasets.push({
            label: 'BB Lower',
            data: bb.lower,
            borderColor: 'rgba(59, 130, 246, 0.6)',
            borderWidth: 1,
            borderDash: [5, 5],
            fill: '-1',
            backgroundColor: 'rgba(59, 130, 246, 0.05)',
            pointRadius: 0,
            tension: 0.4,
            type: 'line'
        });
    }

    stockChartInstance = new Chart(ctx, {
        type: chartType === 'candlestick' ? 'bar' : 'line',
        data: {
            labels: labels,
            datasets: datasets
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                intersect: false,
                mode: 'index'
            },
            plugins: {
                legend: {
                    display: false
                },
                tooltip: {
                    backgroundColor: 'rgba(0, 0, 0, 0.9)',
                    titleColor: '#ff6600',
                    bodyColor: '#e0e0e0',
                    borderColor: '#333',
                    borderWidth: 1,
                    displayColors: false,
                    callbacks: {
                        label: function(context) {
                            const idx = context.dataIndex;
                            const d = ohlcData[idx];
                            if (chartType === 'candlestick' && context.dataset.label === 'Price') {
                                return [
                                    `O: $${d.open.toFixed(2)}`,
                                    `H: $${d.high.toFixed(2)}`,
                                    `L: $${d.low.toFixed(2)}`,
                                    `C: $${d.close.toFixed(2)}`
                                ];
                            }
                            if (context.dataset.label.startsWith('BB')) {
                                return `${context.dataset.label}: $${context.parsed.y?.toFixed(2) || '--'}`;
                            }
                            return `$${context.parsed.y?.toFixed(2) || '--'}`;
                        }
                    }
                }
            },
            scales: {
                x: {
                    display: true,
                    grid: {
                        color: 'rgba(255, 255, 255, 0.05)',
                        drawBorder: false
                    },
                    ticks: {
                        color: '#888',
                        font: {
                            size: 10,
                            family: "'Courier New', monospace"
                        },
                        maxTicksLimit: 6
                    }
                },
                y: {
                    display: true,
                    position: 'right',
                    grid: {
                        color: 'rgba(255, 255, 255, 0.05)',
                        drawBorder: false
                    },
                    ticks: {
                        color: '#888',
                        font: {
                            size: 10,
                            family: "'Courier New', monospace"
                        },
                        callback: function(value) {
                            return '$' + value.toFixed(2);
                        }
                    }
                }
            }
        }
    });
}

/**
 * Render RSI indicator chart
 */
function renderRSIChart(data, period) {
    const ctx = document.getElementById('rsiChart');
    if (!ctx) return;

    if (rsiChartInstance) {
        rsiChartInstance.destroy();
    }

    const ohlcData = data.data;
    const rsiData = data.rsi;

    if (!rsiData || rsiData.length === 0) return;

    const labels = ohlcData.map(d => {
        const date = new Date(d.date);
        if (period === '1d') {
            return date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
        } else if (period === '5d' || period === '1mo') {
            return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
        } else {
            return date.toLocaleDateString('en-US', { month: 'short', year: '2-digit' });
        }
    });

    const lastRSI = rsiData.filter(v => v !== null).pop();
    const rsiValueEl = document.getElementById('rsiValue');
    if (rsiValueEl && lastRSI !== undefined) {
        rsiValueEl.textContent = lastRSI.toFixed(1);
        rsiValueEl.className = 'indicator-value';
        if (lastRSI >= 70) rsiValueEl.classList.add('overbought');
        else if (lastRSI <= 30) rsiValueEl.classList.add('oversold');
        else rsiValueEl.classList.add('neutral');
    }

    rsiChartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: 'RSI',
                data: rsiData,
                borderColor: '#8b5cf6',
                backgroundColor: 'rgba(139, 92, 246, 0.1)',
                borderWidth: 1.5,
                fill: true,
                tension: 0.4,
                pointRadius: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { intersect: false, mode: 'index' },
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: 'rgba(0, 0, 0, 0.9)',
                    titleColor: '#8b5cf6',
                    bodyColor: '#e0e0e0',
                    displayColors: false,
                    callbacks: { label: (ctx) => `RSI: ${ctx.parsed.y?.toFixed(1) || '--'}` }
                }
            },
            scales: {
                x: { display: false },
                y: {
                    display: true,
                    position: 'right',
                    min: 0,
                    max: 100,
                    grid: { color: 'rgba(255, 255, 255, 0.05)', drawBorder: false },
                    ticks: { color: '#888', font: { size: 9, family: "'Courier New', monospace" }, stepSize: 30 }
                }
            }
        }
    });
}

/**
 * Render MACD indicator chart
 */
function renderMACDChart(data, period) {
    const ctx = document.getElementById('macdChart');
    if (!ctx) return;

    if (macdChartInstance) {
        macdChartInstance.destroy();
    }

    const ohlcData = data.data;
    const macdData = data.macd;

    if (!macdData || !macdData.macd) return;

    const labels = ohlcData.map(d => {
        const date = new Date(d.date);
        if (period === '1d') {
            return date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
        } else if (period === '5d' || period === '1mo') {
            return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
        } else {
            return date.toLocaleDateString('en-US', { month: 'short', year: '2-digit' });
        }
    });

    const lastMACD = macdData.macd.filter(v => v !== null).pop();
    const macdValueEl = document.getElementById('macdValue');
    if (macdValueEl && lastMACD !== undefined) {
        macdValueEl.textContent = lastMACD.toFixed(2);
    }

    const histogramColors = macdData.histogram.map(v => {
        if (v === null) return 'transparent';
        return v >= 0 ? 'rgba(16, 185, 129, 0.7)' : 'rgba(239, 68, 68, 0.7)';
    });

    macdChartInstance = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Histogram',
                    data: macdData.histogram,
                    backgroundColor: histogramColors,
                    borderWidth: 0,
                    barPercentage: 0.8,
                    categoryPercentage: 1.0,
                    order: 2
                },
                {
                    label: 'MACD',
                    data: macdData.macd,
                    type: 'line',
                    borderColor: '#10b981',
                    borderWidth: 1.5,
                    fill: false,
                    tension: 0.4,
                    pointRadius: 0,
                    order: 1
                },
                {
                    label: 'Signal',
                    data: macdData.signal,
                    type: 'line',
                    borderColor: '#f59e0b',
                    borderWidth: 1.5,
                    fill: false,
                    tension: 0.4,
                    pointRadius: 0,
                    order: 1
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { intersect: false, mode: 'index' },
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: 'rgba(0, 0, 0, 0.9)',
                    titleColor: '#10b981',
                    bodyColor: '#e0e0e0',
                    displayColors: true,
                    callbacks: { label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y?.toFixed(4) || '--'}` }
                }
            },
            scales: {
                x: { display: false },
                y: {
                    display: true,
                    position: 'right',
                    grid: { color: 'rgba(255, 255, 255, 0.05)', drawBorder: false },
                    ticks: { color: '#888', font: { size: 9, family: "'Courier New', monospace" }, callback: (v) => v.toFixed(2) }
                }
            }
        }
    });
}

/**
 * Change chart type (line or candlestick)
 */
function changeChartType(type) {
    currentChartType = type;
    document.querySelectorAll('.chart-type-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.type === type);
    });
    if (currentChartData) {
        renderStockChart(currentChartData, currentChartPeriod, type);
    }
}

/**
 * Toggle technical indicator
 */
function toggleIndicator(indicator) {
    activeIndicators[indicator] = !activeIndicators[indicator];

    const btn = document.querySelector(`.indicator-toggle-btn[data-indicator="${indicator}"]`);
    if (btn) {
        btn.classList.toggle('active', activeIndicators[indicator]);
    }

    if (indicator === 'rsi') {
        const panel = document.getElementById('rsiPanel');
        if (panel) {
            panel.style.display = activeIndicators.rsi ? 'block' : 'none';
            if (activeIndicators.rsi && currentChartData) {
                renderRSIChart(currentChartData, currentChartPeriod);
            } else if (rsiChartInstance) {
                rsiChartInstance.destroy();
                rsiChartInstance = null;
            }
        }
    } else if (indicator === 'macd') {
        const panel = document.getElementById('macdPanel');
        if (panel) {
            panel.style.display = activeIndicators.macd ? 'block' : 'none';
            if (activeIndicators.macd && currentChartData) {
                renderMACDChart(currentChartData, currentChartPeriod);
            } else if (macdChartInstance) {
                macdChartInstance.destroy();
                macdChartInstance = null;
            }
        }
    } else if (indicator === 'bollinger') {
        if (currentChartData) {
            renderStockChart(currentChartData, currentChartPeriod, currentChartType);
        }
    }
}

/**
 * Change chart period
 */
async function changeChartPeriod(period) {
    if (!currentModalTicker) return;
    
    currentChartPeriod = period;
    
    // Update button states
    document.querySelectorAll('.chart-period-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.period === period);
    });
    
    // Load new chart data
    await loadAndRenderChart(currentModalTicker, period);
}

/**
 * Load related news for the stock
 */
async function loadRelatedNews(ticker) {
    const newsList = document.getElementById('modalNewsList');
    if (!newsList) return;
    
    newsList.innerHTML = '<div class="loading"><i class="fas fa-spinner fa-spin"></i> Loading news...</div>';
    
    try {
        const response = await fetchWithTimeout(`/api/stock/${ticker}/news`);
        const data = await response.json();
        
        if (!data.articles || data.articles.length === 0) {
            newsList.innerHTML = '<div class="empty-state">No recent news found</div>';
            return;
        }
        
        newsList.innerHTML = data.articles.map(article => {
            const sentimentClass = article.sentiment_score > 0.2 ? 'positive' : 
                                  article.sentiment_score < -0.2 ? 'negative' : 'neutral';
            const sentimentLabel = sentimentClass.toUpperCase();
            
            return `
                <div class="news-item">
                    <a href="${article.url}" target="_blank" rel="noopener">${escapeHtml(article.title)}</a>
                    <div class="news-meta">
                        <span class="news-source">${article.source}</span>
                        <span>${timeAgo(article.published_at)}</span>
                        <span class="news-sentiment ${sentimentClass}">${sentimentLabel}</span>
                    </div>
                </div>
            `;
        }).join('');
        
    } catch (error) {
        console.error('[StockModal] Error loading news:', error);
        newsList.innerHTML = '<div class="empty-state">Error loading news</div>';
    }
}

/**
 * Make ticker symbols clickable throughout the dashboard
 * Call this function after rendering content with potential tickers
 */
function makeTickersClickable(container) {
    if (!container) {
        container = document.body;
    }
    
    // Find text nodes that contain ticker patterns
    const walker = document.createTreeWalker(
        container,
        NodeFilter.SHOW_TEXT,
        null,
        false
    );
    
    const textNodes = [];
    let node;
    while (node = walker.nextNode()) {
        // Skip if parent is already a link, script, style, or has clickable class
        const parent = node.parentElement;
        if (parent && (
            parent.tagName === 'A' ||
            parent.tagName === 'SCRIPT' ||
            parent.tagName === 'STYLE' ||
            parent.classList.contains('ticker-clickable') ||
            parent.closest('.stock-modal-content')
        )) {
            continue;
        }
        
        if (/\$[A-Z]{1,5}\b/.test(node.textContent)) {
            textNodes.push(node);
        }
    }
    
    // Replace ticker patterns with clickable spans
    textNodes.forEach(node => {
        const text = node.textContent;
        const parts = text.split(/(\$[A-Z]{1,5}\b)/g);
        
        if (parts.length > 1) {
            const fragment = document.createDocumentFragment();
            parts.forEach(part => {
                if (/^\$[A-Z]{1,5}$/.test(part)) {
                    const ticker = part.substring(1);
                    const span = document.createElement('span');
                    span.className = 'ticker-clickable';
                    span.textContent = part;
                    span.title = `Click to view ${ticker} details`;
                    span.onclick = (e) => {
                        e.stopPropagation();
                        openStockModal(ticker);
                    };
                    fragment.appendChild(span);
                } else {
                    fragment.appendChild(document.createTextNode(part));
                }
            });
            node.parentNode.replaceChild(fragment, node);
        }
    });
}

/**
 * Enhanced highlightTickers function that makes tickers clickable
 */
function highlightTickersClickable(text) {
    if (!text) return '';
    // Match $TICKER patterns and wrap in clickable spans
    return text.replace(/\$([A-Za-z]{1,5})/g, '<span class="ticker-clickable" onclick="event.stopPropagation(); openStockModal(\'$1\')" title="Click to view $1">$$$1</span>');
}

// Override the existing highlightTickers function
const originalHighlightTickers = highlightTickers;
highlightTickers = highlightTickersClickable;

// ============================================================================
// Global exports for modal functions
// ============================================================================

window.openStockModal = openStockModal;
window.closeStockModal = closeStockModal;
window.changeChartPeriod = changeChartPeriod;
window.changeChartType = changeChartType;
window.toggleIndicator = toggleIndicator;

// ============================================================================
// Keyboard shortcuts for modal
// ============================================================================

document.addEventListener('keydown', (e) => {
    // Close modal on Escape
    if (e.key === 'Escape') {
        const modal = document.getElementById('stockDetailModal');
        if (modal && modal.style.display !== 'none') {
            closeStockModal();
        }
    }
});

// ============================================================================
// Initialize clickable tickers on page load
// ============================================================================

document.addEventListener('DOMContentLoaded', () => {
    // Make tickers clickable in existing content after a short delay
    setTimeout(() => {
        makeTickersClickable();
    }, 1000);
});


/* ============================================================================
   ADVANCED SEARCH - BLOOMBERG TERMINAL STYLE
   ============================================================================ */

// Search State
let searchState = {
    query: '',
    type: 'all',
    filters: {
        dateRange: '',
        dateFrom: '',
        dateTo: '',
        sources: [],
        sentiment: '',
        tickers: [],
        minMentions: 1
    },
    results: {
        articles: [],
        companies: [],
        alerts: []
    },
    pagination: {
        offset: 0,
        limit: 20,
        total: 0
    },
    sort: 'relevance',
    isLoading: false,
    selectedIndex: -1
};

// Recent searches (stored in localStorage)
let recentSearches = [];
const MAX_RECENT_SEARCHES = 10;

// Initialize search
function initSearch() {
    loadRecentSearches();
    setupSearchEventListeners();
    loadSourcesForFilter();
}

// Load recent searches from localStorage
function loadRecentSearches() {
    try {
        const stored = localStorage.getItem('nickberg_recent_searches');
        if (stored) {
            recentSearches = JSON.parse(stored);
            renderRecentSearches();
        }
    } catch (e) {
        console.error('Error loading recent searches:', e);
    }
}

// Save recent searches to localStorage
function saveRecentSearches() {
    try {
        localStorage.setItem('nickberg_recent_searches', JSON.stringify(recentSearches));
    } catch (e) {
        console.error('Error saving recent searches:', e);
    }
}

// Add to recent searches
function addToRecentSearches(query, filters) {
    if (!query && filters.tickers.length === 0) return;
    
    const entry = {
        query: query,
        type: searchState.type,
        filters: { ...filters },
        timestamp: Date.now()
    };
    
    // Remove duplicates
    recentSearches = recentSearches.filter(s => 
        !(s.query === query && JSON.stringify(s.filters) === JSON.stringify(filters))
    );
    
    // Add to front
    recentSearches.unshift(entry);
    
    // Limit size
    if (recentSearches.length > MAX_RECENT_SEARCHES) {
        recentSearches = recentSearches.slice(0, MAX_RECENT_SEARCHES);
    }
    
    saveRecentSearches();
    renderRecentSearches();
}

// Render recent searches
function renderRecentSearches() {
    const container = document.getElementById('recentSearches');
    if (!container) return;
    
    if (recentSearches.length === 0) {
        container.innerHTML = '<span class="empty-text">No recent searches</span>';
        return;
    }
    
    container.innerHTML = recentSearches.map((search, index) => `
        <div class="recent-search-item" data-index="${index}">
            <span class="search-text">${search.query || '[Filters Only]'}</span>
            <button class="delete-recent" data-index="${index}" title="Remove">
                <i class="fas fa-times"></i>
            </button>
        </div>
    `).join('');
    
    // Add click handlers
    container.querySelectorAll('.recent-search-item').forEach(item => {
        item.addEventListener('click', (e) => {
            if (e.target.closest('.delete-recent')) return;
            const index = parseInt(item.dataset.index);
            loadRecentSearch(index);
        });
    });
    
    container.querySelectorAll('.delete-recent').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const index = parseInt(btn.dataset.index);
            removeRecentSearch(index);
        });
    });
}

// Load a recent search
function loadRecentSearch(index) {
    const search = recentSearches[index];
    if (!search) return;
    
    searchState.query = search.query;
    searchState.type = search.type;
    searchState.filters = { ...search.filters };
    
    // Update UI
    document.getElementById('searchInput').value = search.query;
    document.getElementById('searchDateRange').value = search.filters.dateRange || '';
    document.getElementById('dateFrom').value = search.filters.dateFrom || '';
    document.getElementById('dateTo').value = search.filters.dateTo || '';
    document.getElementById('searchSentiment').value = search.filters.sentiment || '';
    document.getElementById('searchMinMentions').value = search.filters.minMentions || 1;
    
    // Update ticker tags
    renderSearchTickerTags();
    
    // Update tab
    switchSearchTab(search.type);
    
    // Perform search
    performSearch();
}

// Remove a recent search
function removeRecentSearch(index) {
    recentSearches.splice(index, 1);
    saveRecentSearches();
    renderRecentSearches();
}

// Setup event listeners
function setupSearchEventListeners() {
    // Search button click
    const searchBtn = document.getElementById('searchBtn');
    if (searchBtn) {
        searchBtn.addEventListener('click', openSearchOverlay);
    }
    
    // Close button
    const closeBtn = document.getElementById('searchCloseBtn');
    if (closeBtn) {
        closeBtn.addEventListener('click', closeSearchOverlay);
    }
    
    // Clear button
    const clearBtn = document.getElementById('searchClearBtn');
    if (clearBtn) {
        clearBtn.addEventListener('click', () => {
            document.getElementById('searchInput').value = '';
            document.getElementById('searchInput').focus();
        });
    }
    
    // Search input
    const searchInput = document.getElementById('searchInput');
    if (searchInput) {
        searchInput.addEventListener('input', debounce((e) => {
            searchState.query = e.target.value;
            if (e.target.value.length >= 2) {
                fetchSuggestions(e.target.value);
            } else {
                hideSuggestions();
            }
        }, 150));
        
        searchInput.addEventListener('keydown', handleSearchInputKeydown);
    }
    
    // Tab switching
    document.querySelectorAll('.search-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            switchSearchTab(tab.dataset.tab);
        });
    });
    
    // Date range
    const dateRange = document.getElementById('searchDateRange');
    if (dateRange) {
        dateRange.addEventListener('change', (e) => {
            searchState.filters.dateRange = e.target.value;
            const customRange = document.getElementById('customDateRange');
            if (e.target.value === 'custom') {
                customRange.style.display = 'flex';
            } else {
                customRange.style.display = 'none';
                updateDateRangeFromPreset(e.target.value);
            }
            performSearch();
        });
    }
    
    // Custom date inputs
    const dateFrom = document.getElementById('dateFrom');
    const dateTo = document.getElementById('dateTo');
    if (dateFrom && dateTo) {
        dateFrom.addEventListener('change', (e) => {
            searchState.filters.dateFrom = e.target.value;
            performSearch();
        });
        dateTo.addEventListener('change', (e) => {
            searchState.filters.dateTo = e.target.value;
            performSearch();
        });
    }
    
    // Sentiment filter
    const sentiment = document.getElementById('searchSentiment');
    if (sentiment) {
        sentiment.addEventListener('change', (e) => {
            searchState.filters.sentiment = e.target.value;
            performSearch();
        });
    }
    
    // Ticker input
    const tickerInput = document.getElementById('searchTickerInput');
    const addTickerBtn = document.getElementById('addSearchTickerBtn');
    if (tickerInput && addTickerBtn) {
        tickerInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                addSearchTicker(tickerInput.value);
                tickerInput.value = '';
            }
        });
        addTickerBtn.addEventListener('click', () => {
            addSearchTicker(tickerInput.value);
            tickerInput.value = '';
        });
    }
    
    // Min mentions
    const minMentions = document.getElementById('searchMinMentions');
    if (minMentions) {
        minMentions.addEventListener('change', (e) => {
            searchState.filters.minMentions = parseInt(e.target.value) || 1;
            performSearch();
        });
    }
    
    // Sort
    const sortSelect = document.getElementById('searchSort');
    if (sortSelect) {
        sortSelect.addEventListener('change', (e) => {
            searchState.sort = e.target.value;
            performSearch();
        });
    }
    
    // Clear filters
    const clearFiltersBtn = document.getElementById('clearFiltersBtn');
    if (clearFiltersBtn) {
        clearFiltersBtn.addEventListener('click', clearAllFilters);
    }
    
    // Pagination
    const prevBtn = document.getElementById('prevPageBtn');
    const nextBtn = document.getElementById('nextPageBtn');
    if (prevBtn && nextBtn) {
        prevBtn.addEventListener('click', () => changePage(-1));
        nextBtn.addEventListener('click', () => changePage(1));
    }
    
    // Keyboard shortcuts
    document.addEventListener('keydown', (e) => {
        // Ctrl+K or Cmd+K to open search
        if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
            e.preventDefault();
            openSearchOverlay();
        }
        
        // Escape to close search
        if (e.key === 'Escape') {
            const overlay = document.getElementById('searchOverlay');
            if (overlay && overlay.classList.contains('active')) {
                closeSearchOverlay();
            }
        }
    });
}

// Update date range from preset
function updateDateRangeFromPreset(preset) {
    const now = new Date();
    const dateTo = now.toISOString().split('T')[0];
    let dateFrom = '';
    
    switch (preset) {
        case 'today':
            dateFrom = dateTo;
            break;
        case 'yesterday':
            const yesterday = new Date(now);
            yesterday.setDate(yesterday.getDate() - 1);
            dateFrom = yesterday.toISOString().split('T')[0];
            break;
        case '7d':
            const weekAgo = new Date(now);
            weekAgo.setDate(weekAgo.getDate() - 7);
            dateFrom = weekAgo.toISOString().split('T')[0];
            break;
        case '30d':
            const monthAgo = new Date(now);
            monthAgo.setDate(monthAgo.getDate() - 30);
            dateFrom = monthAgo.toISOString().split('T')[0];
            break;
    }
    
    searchState.filters.dateFrom = dateFrom;
    searchState.filters.dateTo = dateTo;
    
    document.getElementById('dateFrom').value = dateFrom;
    document.getElementById('dateTo').value = dateTo;
}

// Add search ticker
function addSearchTicker(ticker) {
    ticker = ticker.toUpperCase().trim();
    if (!ticker) return;
    if (searchState.filters.tickers.includes(ticker)) return;
    
    searchState.filters.tickers.push(ticker);
    renderSearchTickerTags();
    performSearch();
}

// Remove search ticker
function removeSearchTicker(ticker) {
    searchState.filters.tickers = searchState.filters.tickers.filter(t => t !== ticker);
    renderSearchTickerTags();
    performSearch();
}

// Render ticker tags
function renderSearchTickerTags() {
    const container = document.getElementById('searchTickerTags');
    if (!container) return;
    
    container.innerHTML = searchState.filters.tickers.map(ticker => `
        <span class="ticker-tag">
            ${ticker}
            <button onclick="removeSearchTicker('${ticker}')" title="Remove">
                <i class="fas fa-times"></i>
            </button>
        </span>
    `).join('');
}

// Make function globally accessible
window.removeSearchTicker = removeSearchTicker;

// Load sources for filter
async function loadSourcesForFilter() {
    try {
        const response = await fetchWithTimeout('/api/sources/all');
        const sources = await response.json();
        
        const container = document.getElementById('searchSources');
        if (!container) return;
        
        if (sources.length === 0) {
            container.innerHTML = '<span class="empty-text">No sources</span>';
            return;
        }
        
        container.innerHTML = sources.map(source => `
            <label class="filter-checkbox-item">
                <input type="checkbox" value="${source.source}" onchange="toggleSourceFilter('${source.source}', this.checked)">
                <span>${source.source} (${source.count})</span>
            </label>
        `).join('');
    } catch (error) {
        console.error('Error loading sources:', error);
    }
}

// Toggle source filter
function toggleSourceFilter(source, checked) {
    if (checked) {
        if (!searchState.filters.sources.includes(source)) {
            searchState.filters.sources.push(source);
        }
    } else {
        searchState.filters.sources = searchState.filters.sources.filter(s => s !== source);
    }
    performSearch();
}

// Make function globally accessible
window.toggleSourceFilter = toggleSourceFilter;

// Clear all filters
function clearAllFilters() {
    searchState.filters = {
        dateRange: '',
        dateFrom: '',
        dateTo: '',
        sources: [],
        sentiment: '',
        tickers: [],
        minMentions: 1
    };
    
    // Reset UI
    document.getElementById('searchDateRange').value = '';
    document.getElementById('customDateRange').style.display = 'none';
    document.getElementById('dateFrom').value = '';
    document.getElementById('dateTo').value = '';
    document.getElementById('searchSentiment').value = '';
    document.getElementById('searchMinMentions').value = '1';
    
    // Uncheck all sources
    document.querySelectorAll('#searchSources input[type="checkbox"]').forEach(cb => {
        cb.checked = false;
    });
    
    renderSearchTickerTags();
    performSearch();
}

// Open search overlay
function openSearchOverlay() {
    const overlay = document.getElementById('searchOverlay');
    if (overlay) {
        overlay.classList.add('active');
        document.body.style.overflow = 'hidden';
        
        // Focus input
        setTimeout(() => {
            const input = document.getElementById('searchInput');
            if (input) input.focus();
        }, 100);
        
        // Load sources if not loaded
        loadSourcesForFilter();
    }
}

// Close search overlay
function closeSearchOverlay() {
    const overlay = document.getElementById('searchOverlay');
    if (overlay) {
        overlay.classList.remove('active');
        document.body.style.overflow = '';
        hideSuggestions();
    }
}

// Switch search tab
function switchSearchTab(tab) {
    searchState.type = tab;
    
    // Update UI
    document.querySelectorAll('.search-tab').forEach(t => {
        t.classList.toggle('active', t.dataset.tab === tab);
    });
    
    performSearch();
}

// Fetch suggestions
async function fetchSuggestions(query) {
    try {
        const response = await fetchWithTimeout(`/api/search/suggestions?q=${encodeURIComponent(query)}&limit=10`);
        const data = await response.json();
        renderSuggestions(data.suggestions);
    } catch (error) {
        console.error('Error fetching suggestions:', error);
    }
}

// Render suggestions
function renderSuggestions(suggestions) {
    let container = document.querySelector('.search-suggestions');
    
    if (!suggestions || suggestions.length === 0) {
        if (container) container.remove();
        return;
    }
    
    if (!container) {
        container = document.createElement('div');
        container.className = 'search-suggestions';
        document.querySelector('.search-input-wrapper').appendChild(container);
    }
    
    container.innerHTML = suggestions.map((s, index) => `
        <div class="suggestion-item" data-index="${index}" data-value="${s.value}" data-type="${s.type}">
            <span class="suggestion-type">${s.type}</span>
            <span class="suggestion-value">${highlightMatch(s.value, searchState.query)}</span>
        </div>
    `).join('');
    
    // Add click handlers
    container.querySelectorAll('.suggestion-item').forEach(item => {
        item.addEventListener('click', () => {
            const value = item.dataset.value;
            const type = item.dataset.type;
            
            if (type === 'ticker') {
                addSearchTicker(value);
            } else {
                document.getElementById('searchInput').value = value;
                searchState.query = value;
                performSearch();
            }
            
            hideSuggestions();
        });
    });
}

// Hide suggestions
function hideSuggestions() {
    const container = document.querySelector('.search-suggestions');
    if (container) container.remove();
}

// Highlight match in suggestion
function highlightMatch(text, query) {
    if (!query) return text;
    const regex = new RegExp(`(${escapeRegex(query)})`, 'gi');
    return text.replace(regex, '<mark>$1</mark>');
}

// Escape regex special chars
function escapeRegex(string) {
    return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// Handle search input keydown
function handleSearchInputKeydown(e) {
    const suggestions = document.querySelectorAll('.suggestion-item');
    const selected = document.querySelector('.suggestion-item.selected');
    let selectedIndex = selected ? parseInt(selected.dataset.index) : -1;
    
    switch (e.key) {
        case 'ArrowDown':
            e.preventDefault();
            if (suggestions.length > 0) {
                selectedIndex = (selectedIndex + 1) % suggestions.length;
                updateSuggestionSelection(suggestions, selectedIndex);
            }
            break;
            
        case 'ArrowUp':
            e.preventDefault();
            if (suggestions.length > 0) {
                selectedIndex = selectedIndex <= 0 ? suggestions.length - 1 : selectedIndex - 1;
                updateSuggestionSelection(suggestions, selectedIndex);
            }
            break;
            
        case 'Enter':
            e.preventDefault();
            if (selected) {
                selected.click();
            } else {
                hideSuggestions();
                performSearch();
            }
            break;
            
        case 'Escape':
            hideSuggestions();
            break;
    }
}

// Update suggestion selection
function updateSuggestionSelection(suggestions, index) {
    suggestions.forEach((s, i) => {
        s.classList.toggle('selected', i === index);
    });
}

// Perform search
// Note: searchDebounceTimer already declared at top of file

function performSearch() {
    clearTimeout(searchDebounceTimer);
    searchDebounceTimer = setTimeout(() => {
        executeSearch();
    }, 300);
}

// Execute search
async function executeSearch() {
    if (searchState.isLoading) return;
    
    const query = searchState.query;
    const filters = searchState.filters;
    
    // Show loading
    searchState.isLoading = true;
    showSearchLoading();
    
    // Build URL
    const params = new URLSearchParams();
    if (query) params.append('q', query);
    params.append('type', searchState.type);
    params.append('limit', searchState.pagination.limit);
    params.append('offset', searchState.pagination.offset);
    params.append('sort', searchState.sort);
    
    if (filters.dateFrom) params.append('date_from', filters.dateFrom);
    if (filters.dateTo) params.append('date_to', filters.dateTo);
    if (filters.sentiment) params.append('sentiment', filters.sentiment);
    if (filters.minMentions > 1) params.append('min_mentions', filters.minMentions);
    
    filters.sources.forEach(s => params.append('sources', s));
    filters.tickers.forEach(t => params.append('tickers', t));
    
    try {
        const response = await fetchWithTimeout(`/api/search?${params.toString()}`);
        const data = await response.json();
        
        // Update state
        searchState.results = {
            articles: data.articles.items,
            companies: data.companies.items,
            alerts: data.alerts.items
        };
        
        searchState.pagination.total = data.total;
        
        // Update UI
        updateResultCounts(data);
        renderSearchResults();
        updateSearchStatus(`Found ${data.total} results`);
        
        // Add to recent searches
        if (query || filters.tickers.length > 0) {
            addToRecentSearches(query, filters);
        }
        
    } catch (error) {
        console.error('Search error:', error);
        updateSearchStatus('Search failed');
        showSearchError();
    } finally {
        searchState.isLoading = false;
    }
}

// Show search loading
function showSearchLoading() {
    const container = document.getElementById('searchResultsList');
    if (container) {
        container.innerHTML = `
            <div class="search-loading">
                <i class="fas fa-spinner fa-spin"></i>
                <span>Searching...</span>
            </div>
        `;
    }
    updateSearchStatus('Searching...');
}

// Update result counts
function updateResultCounts(data) {
    document.getElementById('countAll').textContent = data.total;
    document.getElementById('countArticles').textContent = data.articles.total;
    document.getElementById('countCompanies').textContent = data.companies.total;
    document.getElementById('countAlerts').textContent = data.alerts.total;
}

// Render search results
function renderSearchResults() {
    const container = document.getElementById('searchResultsList');
    if (!container) return;
    
    const type = searchState.type;
    let results = [];
    
    if (type === 'all') {
        // Combine and interleave results
        const maxItems = 5;
        results = [
            ...searchState.results.articles.slice(0, maxItems).map(r => ({ ...r, resultType: 'article' })),
            ...searchState.results.companies.slice(0, maxItems).map(r => ({ ...r, resultType: 'company' })),
            ...searchState.results.alerts.slice(0, maxItems).map(r => ({ ...r, resultType: 'alert' }))
        ];
    } else if (type === 'articles') {
        results = searchState.results.articles.map(r => ({ ...r, resultType: 'article' }));
    } else if (type === 'companies') {
        results = searchState.results.companies.map(r => ({ ...r, resultType: 'company' }));
    } else if (type === 'alerts') {
        results = searchState.results.alerts.map(r => ({ ...r, resultType: 'alert' }));
    }
    
    // Sort results
    if (searchState.sort === 'date_desc') {
        results.sort((a, b) => new Date(b.scraped_at || b.last_mentioned || b.created_at) - new Date(a.scraped_at || a.last_mentioned || a.created_at));
    } else if (searchState.sort === 'date_asc') {
        results.sort((a, b) => new Date(a.scraped_at || a.last_mentioned || a.created_at) - new Date(b.scraped_at || b.last_mentioned || b.created_at));
    } else if (searchState.sort === 'mentions') {
        results.sort((a, b) => (b.mention_count || 0) - (a.mention_count || 0));
    }
    // relevance is default - keep as returned by API
    
    if (results.length === 0) {
        container.innerHTML = `
            <div class="no-results">
                <i class="fas fa-search"></i>
                <p>No results found</p>
            </div>
        `;
        updatePagination(0, 0);
        return;
    }
    
    container.innerHTML = results.map((result, index) => {
        if (result.resultType === 'article') {
            return renderArticleResult(result, index);
        } else if (result.resultType === 'company') {
            return renderCompanyResult(result, index);
        } else if (result.resultType === 'alert') {
            return renderAlertResult(result, index);
        }
    }).join('');
    
    // Update pagination
    const total = type === 'all' ? searchState.pagination.total : 
                  type === 'articles' ? document.getElementById('countArticles').textContent :
                  type === 'companies' ? document.getElementById('countCompanies').textContent :
                  document.getElementById('countAlerts').textContent;
    updatePagination(parseInt(total), searchState.pagination.offset);
}

// Render article result
function renderArticleResult(article, index) {
    const sentiment = article.sentiment_score > 0.2 ? 'positive' : 
                      article.sentiment_score < -0.2 ? 'negative' : 'neutral';
    const sentimentLabel = sentiment.charAt(0).toUpperCase() + sentiment.slice(1);
    
    const highlight = article.highlight || {};
    const title = highlight.title || article.title;
    const snippet = highlight.snippet || (article.content ? article.content.substring(0, 200) + '...' : '');
    
    return `
        <div class="result-item article-result" data-index="${index}" data-type="article" data-id="${article.id}">
            <div class="result-header">
                <div class="result-title">
                    <a href="${article.url}" target="_blank" rel="noopener">${title}</a>
                </div>
                <span class="result-source">${article.source}</span>
            </div>
            <div class="result-snippet">${snippet}</div>
            <div class="result-meta">
                <span>${timeAgo(article.scraped_at)}</span>
                <div class="result-tags">
                    ${article.mentions.map(m => `<span class="result-tag">${m}</span>`).join('')}
                    ${article.sentiment_score !== null ? `<span class="result-tag sentiment-${sentiment}">${sentimentLabel} ${article.sentiment_score > 0 ? '+' : ''}${article.sentiment_score.toFixed(2)}</span>` : ''}
                </div>
            </div>
        </div>
    `;
}

// Render company result
function renderCompanyResult(company, index) {
    const recentArticles = company.recent_articles || [];
    
    return `
        <div class="result-item company-result" data-index="${index}" data-type="company" data-ticker="${company.ticker}">
            <div class="result-company">
                <div class="company-info">
                    <span class="company-ticker-display">${company.ticker}</span>
                    <span class="company-name-display">${company.name}</span>
                </div>
                <div class="company-stats">
                    <div class="company-mention-count">${company.mention_count}</div>
                    <div class="company-stat-label">Mentions</div>
                </div>
            </div>
            ${recentArticles.length > 0 ? `
                <div class="company-recent-articles">
                    ${recentArticles.map(a => `
                        <div class="recent-article">
                            <span class="recent-article-title" title="${a.title}">${a.title}</span>
                            <span class="recent-article-source">${a.source}</span>
                        </div>
                    `).join('')}
                </div>
            ` : ''}
        </div>
    `;
}

// Render alert result
function renderAlertResult(alert, index) {
    return `
        <div class="result-item alert-result ${alert.severity}" data-index="${index}" data-type="alert" data-id="${alert.id}">
            <div class="result-alert">
                <div class="alert-header-row">
                    <span class="alert-type-badge">${alert.type.replace(/_/g, ' ').toUpperCase()}</span>
                    <span class="alert-severity-badge ${alert.severity}">${alert.severity}</span>
                </div>
                <div class="alert-message-text">${alert.highlight ? alert.highlight.title : alert.message}</div>
                <div class="alert-details">
                    <span><strong>${alert.ticker}</strong> - ${alert.company}</span>
                    <span>${timeAgo(alert.created_at)}</span>
                </div>
            </div>
        </div>
    `;
}

// Update pagination
function updatePagination(total, offset) {
    const pagination = document.getElementById('searchPagination');
    const prevBtn = document.getElementById('prevPageBtn');
    const nextBtn = document.getElementById('nextPageBtn');
    const pageInfo = document.getElementById('pageInfo');
    
    if (total <= searchState.pagination.limit) {
        pagination.style.display = 'none';
        return;
    }
    
    pagination.style.display = 'flex';
    const currentPage = Math.floor(offset / searchState.pagination.limit) + 1;
    const totalPages = Math.ceil(total / searchState.pagination.limit);
    
    prevBtn.disabled = currentPage <= 1;
    nextBtn.disabled = currentPage >= totalPages;
    pageInfo.textContent = `Page ${currentPage} of ${totalPages}`;
}

// Change page
function changePage(direction) {
    const newOffset = searchState.pagination.offset + (direction * searchState.pagination.limit);
    if (newOffset < 0) return;
    
    searchState.pagination.offset = newOffset;
    executeSearch();
}

// Update search status
function updateSearchStatus(text) {
    const status = document.getElementById('searchStatus');
    if (status) status.textContent = text;
}

// Show search error
function showSearchError() {
    const container = document.getElementById('searchResultsList');
    if (container) {
        container.innerHTML = `
            <div class="no-results">
                <i class="fas fa-exclamation-circle"></i>
                <p>Search failed. Please try again.</p>
            </div>
        `;
    }
}

// Initialize search on DOM ready
document.addEventListener('DOMContentLoaded', initSearch);

// Make functions globally accessible
window.openSearchOverlay = openSearchOverlay;
window.closeSearchOverlay = closeSearchOverlay;

// =============================================================================
// Insider Trading Functions
// =============================================================================

/**
 * Load and display insider trading data for a stock
 */
async function loadInsiderTransactions(ticker) {
    const container = document.getElementById('modalInsiderTransactions');
    if (!container) return;

    container.innerHTML = '<div class="insider-placeholder"><i class="fas fa-spinner fa-spin"></i> Loading insider transactions...</div>';

    try {
        const response = await fetchWithTimeout(`/api/stock/${ticker}/insiders`);
        const data = await response.json();

        if (data.error) {
            container.innerHTML = `<div class="insider-placeholder">${data.error}</div>`;
            return;
        }

        if (!data.transactions || data.transactions.length === 0) {
            container.innerHTML = '<div class="insider-placeholder">No recent insider transactions found</div>';
            return;
        }

        // Build table header
        let html = `
            <div class="insider-header">
                <span>Date</span>
                <span>Insider</span>
                <span>Type</span>
                <span>Shares</span>
                <span>Value</span>
            </div>
        `;

        // Build transaction rows
        html += data.transactions.map(trans => {
            const typeClass = trans.transaction_type.toLowerCase().includes('buy') ? 'buy' :
                              trans.transaction_type.toLowerCase().includes('sell') ? 'sell' :
                              trans.transaction_type.toLowerCase().includes('exercise') ? 'exercise' : 'gift';

            const formattedValue = trans.value ? `$${formatLargeNumber(trans.value)}` : 'N/A';
            const formattedShares = trans.shares ? formatLargeNumber(trans.shares) : 'N/A';

            return `
                <div class="insider-transaction">
                    <span class="insider-date">${trans.date}</span>
                    <div class="insider-info">
                        <span class="insider-name">${trans.insider}</span>
                        <span class="insider-title">${trans.title}</span>
                    </div>
                    <span class="insider-type ${typeClass}">${trans.transaction_type}</span>
                    <span class="insider-shares">${formattedShares}</span>
                    <span class="insider-value">${formattedValue}</span>
                </div>
            `;
        }).join('');

        container.innerHTML = html;

    } catch (error) {
        console.error('Error loading insider transactions:', error);
        container.innerHTML = '<div class="insider-placeholder">Failed to load insider transactions</div>';
    }
}

/**
 * Format large numbers with K, M, B suffixes
 */
function formatLargeNumber(num) {
    if (!num || isNaN(num)) return '0';
    const absNum = Math.abs(num);
    if (absNum >= 1e9) return (num / 1e9).toFixed(2) + 'B';
    if (absNum >= 1e6) return (num / 1e6).toFixed(2) + 'M';
    if (absNum >= 1e3) return (num / 1e3).toFixed(1) + 'K';
    return num.toLocaleString();
}

// =============================================================================
// Trending Tickers Functions
// =============================================================================

/**
 * Load and display trending tickers
 */
async function loadTrendingTickers() {
    const container = document.getElementById('trendingTickersList');
    if (!container) return;

    try {
        const response = await fetchWithTimeout('/api/trending-tickers?hours=24&limit=8&min_mentions=1');
        const data = await response.json();

        if (!data.tickers || data.tickers.length === 0) {
            container.innerHTML = '<div class="empty-state">No trending tickers</div>';
            return;
        }

        container.innerHTML = data.tickers.map((ticker, idx) => `
            <div class="trending-ticker-item" onclick="openStockModal('${ticker.ticker}')">
                <div class="trending-ticker-info">
                    <span class="trending-ticker-rank ${idx < 3 ? 'top' : ''}">${idx + 1}</span>
                    <span class="trending-ticker-symbol">${ticker.ticker}</span>
                </div>
                <div class="trending-ticker-stats">
                    <span class="trending-ticker-mentions">${ticker.mentions} mentions</span>
                    <span class="trending-ticker-sentiment ${ticker.sentiment_label}">${ticker.sentiment_label.toUpperCase()}</span>
                </div>
            </div>
        `).join('');

    } catch (error) {
        console.error('Error loading trending tickers:', error);
        container.innerHTML = '<div class="empty-state">Error loading data</div>';
    }
}

// =============================================================================
// Sentiment Trend Functions
// =============================================================================

/**
 * Load and display sentiment trend mini-chart
 */
async function loadSentimentTrend() {
    const barsContainer = document.getElementById('sentimentBars');
    const summaryContainer = document.getElementById('sentimentSummary');
    if (!barsContainer) return;

    try {
        const response = await fetchWithTimeout('/api/sentiment/trends?hours=48&interval=6h');
        const data = await response.json();

        if (!data.trends || data.trends.length === 0) {
            barsContainer.innerHTML = '<div class="empty-state">No data</div>';
            return;
        }

        // Find max total for scaling
        const maxTotal = Math.max(...data.trends.map(t => t.total), 1);

        // Build bar chart
        const barsHtml = data.trends.slice(-12).map(t => {
            const total = t.total || 1;
            const posHeight = (t.positive / total) * 100;
            const neutralHeight = (t.neutral / total) * 100;
            const negHeight = (t.negative / total) * 100;
            const barHeight = (total / maxTotal) * 40;

            return `
                <div class="sentiment-bar" title="${t.time}">
                    <div class="sentiment-bar-segment positive" style="height: ${posHeight * barHeight / 100}px"></div>
                    <div class="sentiment-bar-segment neutral" style="height: ${neutralHeight * barHeight / 100}px"></div>
                    <div class="sentiment-bar-segment negative" style="height: ${negHeight * barHeight / 100}px"></div>
                </div>
            `;
        }).join('');

        barsContainer.innerHTML = barsHtml;

        // Update summary
        if (summaryContainer && data.summary) {
            const mood = data.summary.overall_sentiment || 'neutral';
            const moodLabel = mood === 'bullish' ? 'BULLISH' : mood === 'bearish' ? 'BEARISH' : 'NEUTRAL';

            summaryContainer.innerHTML = `
                <span class="sentiment-label ${mood}">${moodLabel}</span>
                <div class="sentiment-counts">
                    <span class="sentiment-count"><span class="dot positive"></span>${data.summary.positive}</span>
                    <span class="sentiment-count"><span class="dot neutral"></span>${data.summary.neutral}</span>
                    <span class="sentiment-count"><span class="dot negative"></span>${data.summary.negative}</span>
                </div>
            `;
        }

    } catch (error) {
        console.error('Error loading sentiment trend:', error);
        barsContainer.innerHTML = '<div class="empty-state">Error</div>';
    }
}

// =============================================================================
// Sentiment Filter Functions
// =============================================================================

/**
 * Setup sentiment filter buttons for articles list
 */
function setupSentimentFilters() {
    const filterBtns = document.querySelectorAll('.sentiment-filter-btn');
    if (!filterBtns.length) return;

    filterBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            // Update active state
            filterBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');

            // Apply filter
            const sentiment = btn.dataset.sentiment;
            if (sentiment === 'all') {
                articlesState.filters.sentiment = null;
            } else {
                articlesState.filters.sentiment = sentiment;
            }

            // Reload articles with filter
            articlesState.offset = 0;
            articlesState.articles = [];
            articlesState.hasMore = true;
            document.getElementById('articlesList').innerHTML = '';
            loadMoreArticles();
        });
    });
}

// Initialize new features on DOM ready
document.addEventListener('DOMContentLoaded', () => {
    // Load trending tickers and sentiment trend on startup
    setTimeout(() => {
        loadTrendingTickers();
        loadSentimentTrend();
        setupSentimentFilters();
    }, 500);

    // Refresh periodically
    setInterval(() => {
        loadTrendingTickers();
        loadSentimentTrend();
    }, 120000); // Every 2 minutes
});

// Make functions globally accessible
window.loadInsiderTransactions = loadInsiderTransactions;
window.loadTrendingTickers = loadTrendingTickers;
window.loadSentimentTrend = loadSentimentTrend;

// =============================================================================
// Stock Comparison Feature
// =============================================================================

let comparisonTickers = [];
let compareChartInstance = null;

function openCompareModal() {
    const modal = document.getElementById('compareModal');
    if (modal) {
        modal.style.display = 'flex';
        document.getElementById('compareTickerInput').focus();
    }
}

function closeCompareModal() {
    const modal = document.getElementById('compareModal');
    if (modal) {
        modal.style.display = 'none';
    }
    comparisonTickers = [];
    updateCompareTickerList();
    document.getElementById('compareResults').style.display = 'none';
    document.getElementById('compareLoading').style.display = 'none';
    if (compareChartInstance) {
        compareChartInstance.destroy();
        compareChartInstance = null;
    }
}

function handleCompareTickerKeypress(event) {
    if (event.key === 'Enter') {
        addComparisonTicker();
    }
}

function addComparisonTicker() {
    const input = document.getElementById('compareTickerInput');
    const ticker = input.value.trim().toUpperCase();
    if (!ticker) return;
    if (!/^[A-Z]{1,5}$/.test(ticker)) {
        showToast('Invalid ticker format', 'error');
        return;
    }
    if (comparisonTickers.includes(ticker)) {
        showToast(`${ticker} already added`, 'warning');
        input.value = '';
        return;
    }
    if (comparisonTickers.length >= 4) {
        showToast('Maximum 4 tickers allowed', 'warning');
        return;
    }
    comparisonTickers.push(ticker);
    updateCompareTickerList();
    input.value = '';
    input.focus();
}

function removeComparisonTicker(ticker) {
    comparisonTickers = comparisonTickers.filter(t => t !== ticker);
    updateCompareTickerList();
}

function updateCompareTickerList() {
    const container = document.getElementById('compareTickerList');
    if (!container) return;
    container.innerHTML = comparisonTickers.map(ticker => `
        <span class="compare-ticker-tag">
            ${ticker}
            <span class="remove" onclick="removeComparisonTicker('${ticker}')">&times;</span>
        </span>
    `).join('');
}

function refreshComparison() {
    if (comparisonTickers.length >= 2) {
        runComparison();
    }
}

async function runComparison() {
    if (comparisonTickers.length < 2) {
        showToast('Add at least 2 tickers to compare', 'warning');
        return;
    }
    const period = document.getElementById('comparePeriod').value;
    const loading = document.getElementById('compareLoading');
    const results = document.getElementById('compareResults');
    loading.style.display = 'block';
    results.style.display = 'none';
    try {
        const response = await fetchWithTimeout(`/api/compare?tickers=${comparisonTickers.join(',')}&period=${period}`);
        const data = await response.json();
        if (data.error) {
            showToast(data.error, 'error');
            loading.style.display = 'none';
            return;
        }
        renderComparisonChart(data);
        renderComparisonMetrics(data);
        loading.style.display = 'none';
        results.style.display = 'flex';
    } catch (error) {
        console.error('Error running comparison:', error);
        showToast('Failed to load comparison data', 'error');
        loading.style.display = 'none';
    }
}

function renderComparisonChart(data) {
    const ctx = document.getElementById('compareChart');
    if (!ctx) return;
    if (compareChartInstance) {
        compareChartInstance.destroy();
    }
    const colors = ['#ff9f1c', '#3b82f6', '#10b981', '#8b5cf6'];
    const datasets = [];
    Object.entries(data.chart_data).forEach(([ticker, chartData], index) => {
        if (chartData && chartData.length > 0) {
            datasets.push({
                label: ticker,
                data: chartData.map(d => ({ x: d.date, y: d.percent_change })),
                borderColor: colors[index % colors.length],
                backgroundColor: colors[index % colors.length] + '20',
                borderWidth: 2,
                fill: false,
                tension: 0.3,
                pointRadius: 0,
                pointHoverRadius: 4
            });
        }
    });
    const allDates = [...new Set(Object.values(data.chart_data).flat().map(d => d.date))].sort();
    compareChartInstance = new Chart(ctx, {
        type: 'line',
        data: { labels: allDates, datasets: datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { intersect: false, mode: 'index' },
            plugins: {
                legend: { position: 'top', labels: { color: '#94a3b8', usePointStyle: true, padding: 20 } },
                tooltip: {
                    backgroundColor: '#1e293b',
                    titleColor: '#f8fafc',
                    bodyColor: '#94a3b8',
                    borderColor: '#334155',
                    borderWidth: 1,
                    callbacks: { label: ctx => `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(2)}%` }
                }
            },
            scales: {
                x: { grid: { color: '#334155' }, ticks: { color: '#94a3b8', maxRotation: 0, maxTicksLimit: 10 } },
                y: { grid: { color: '#334155' }, ticks: { color: '#94a3b8', callback: v => v.toFixed(1) + '%' } }
            }
        }
    });
}

function renderComparisonMetrics(data) {
    const headerRow = document.getElementById('compareMetricsHeader');
    const tbody = document.getElementById('compareMetricsBody');
    if (!headerRow || !tbody) return;
    const tickers = Object.keys(data.stocks);
    headerRow.innerHTML = '<th>Metric</th>' + tickers.map(t => `<th>${t}</th>`).join('');
    const metrics = [
        { key: 'name', label: 'Company' },
        { key: 'price', label: 'Price', format: v => `$${v}` },
        { key: 'change_percent', label: 'Change %', format: v => `${v >= 0 ? '+' : ''}${v}%`, colorize: true },
        { key: 'market_cap', label: 'Market Cap' },
        { key: 'pe_ratio', label: 'P/E Ratio' },
        { key: 'forward_pe', label: 'Forward P/E' },
        { key: 'peg_ratio', label: 'PEG Ratio' },
        { key: 'eps', label: 'EPS' },
        { key: 'dividend_yield', label: 'Div Yield', format: v => v ? `${v}%` : 'N/A' },
        { key: 'beta', label: 'Beta' },
        { key: '52_week_high', label: '52W High', format: v => typeof v === 'number' ? `$${v}` : v },
        { key: '52_week_low', label: '52W Low', format: v => typeof v === 'number' ? `$${v}` : v },
        { key: 'profit_margin', label: 'Profit Margin', format: v => v !== 'N/A' ? `${v}%` : v },
        { key: 'roe', label: 'ROE', format: v => v !== 'N/A' ? `${v}%` : v },
        { key: 'debt_to_equity', label: 'Debt/Equity' },
        { key: 'sector', label: 'Sector' },
        { key: 'industry', label: 'Industry' }
    ];
    tbody.innerHTML = metrics.map(metric => {
        const cells = tickers.map(ticker => {
            const stock = data.stocks[ticker];
            if (!stock || stock.error) return '<td>--</td>';
            let value = stock[metric.key];
            if (value === undefined || value === null) value = 'N/A';
            if (metric.format && value !== 'N/A') value = metric.format(value);
            let className = '';
            if (metric.colorize && typeof stock[metric.key] === 'number') {
                className = stock[metric.key] >= 0 ? 'positive' : 'negative';
            }
            return `<td class="${className}">${value}</td>`;
        }).join('');
        return `<tr><td>${metric.label}</td>${cells}</tr>`;
    }).join('');
}

window.openCompareModal = openCompareModal;
window.closeCompareModal = closeCompareModal;
window.handleCompareTickerKeypress = handleCompareTickerKeypress;
window.addComparisonTicker = addComparisonTicker;
window.removeComparisonTicker = removeComparisonTicker;
window.refreshComparison = refreshComparison;
window.runComparison = runComparison;

// =============================================================================
// Options Chain Feature
// =============================================================================

let currentOptionsData = null;

async function loadOptionsExpirations(ticker) {
    const select = document.getElementById('optionsExpiration');
    if (!select) return;
    select.innerHTML = '<option value="">Loading...</option>';
    try {
        const response = await fetchWithTimeout(`/api/stock/${ticker}/options`);
        const data = await response.json();
        if (data.error && !data.expirations) {
            select.innerHTML = '<option value="">No options available</option>';
            return;
        }
        const expirations = data.expirations || [];
        select.innerHTML = '<option value="">Select expiration...</option>' +
            expirations.map(exp => `<option value="${exp}">${exp}</option>`).join('');
    } catch (error) {
        console.error('Error loading options expirations:', error);
        select.innerHTML = '<option value="">Error loading options</option>';
    }
}

async function loadOptionsForExpiration() {
    const select = document.getElementById('optionsExpiration');
    const expiration = select.value;
    if (!expiration || !currentModalTicker) return;
    const loading = document.getElementById('optionsLoading');
    const content = document.getElementById('optionsContent');
    const dataContainer = document.getElementById('optionsData');
    loading.style.display = 'block';
    content.style.display = 'none';
    dataContainer.style.display = 'none';
    try {
        const response = await fetchWithTimeout(`/api/stock/${currentModalTicker}/options?expiration=${expiration}`);
        const data = await response.json();
        if (data.error) {
            content.innerHTML = `<div class="options-empty"><i class="fas fa-exclamation-circle"></i><p>${data.error}</p></div>`;
            loading.style.display = 'none';
            content.style.display = 'block';
            return;
        }
        currentOptionsData = data;
        renderOptionsChain(data);
        loading.style.display = 'none';
        dataContainer.style.display = 'block';
    } catch (error) {
        console.error('Error loading options chain:', error);
        content.innerHTML = '<div class="options-empty"><i class="fas fa-exclamation-circle"></i><p>Failed to load options data</p></div>';
        loading.style.display = 'none';
        content.style.display = 'block';
    }
}

function renderOptionsChain(data) {
    const priceEl = document.getElementById('optionsCurrentPrice');
    if (priceEl) priceEl.innerHTML = `Current Price: <span>$${data.currentPrice}</span>`;
    const callsBody = document.getElementById('optionsCallsBody');
    if (callsBody && data.calls) {
        callsBody.innerHTML = data.calls.map(opt => {
            const itmClass = opt.inTheMoney ? 'itm' : '';
            const ivClass = getIVClass(opt.impliedVolatility);
            return `<tr class="${itmClass}"><td>$${opt.strike}</td><td>$${opt.lastPrice}</td><td>$${opt.bid}</td><td>$${opt.ask}</td><td>${formatCompactNum(opt.volume)}</td><td>${formatCompactNum(opt.openInterest)}</td><td class="${ivClass}">${opt.impliedVolatility}%</td></tr>`;
        }).join('');
    }
    const putsBody = document.getElementById('optionsPutsBody');
    if (putsBody && data.puts) {
        putsBody.innerHTML = data.puts.map(opt => {
            const itmClass = opt.inTheMoney ? 'itm' : '';
            const ivClass = getIVClass(opt.impliedVolatility);
            return `<tr class="${itmClass}"><td>$${opt.strike}</td><td>$${opt.lastPrice}</td><td>$${opt.bid}</td><td>$${opt.ask}</td><td>${formatCompactNum(opt.volume)}</td><td>${formatCompactNum(opt.openInterest)}</td><td class="${ivClass}">${opt.impliedVolatility}%</td></tr>`;
        }).join('');
    }
}

function getIVClass(iv) {
    if (iv > 80) return 'iv-high';
    if (iv > 40) return 'iv-medium';
    return 'iv-low';
}

function formatCompactNum(num) {
    if (!num) return '0';
    if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
    if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
    return num.toString();
}

// Hook into stock modal tab switching for options loading
const originalSwitchStockTabFn = window.switchStockTab;
window.switchStockTab = function(tabName) {
    if (typeof originalSwitchStockTabFn === 'function') {
        originalSwitchStockTabFn(tabName);
    } else {
        document.querySelectorAll('.stock-tab').forEach(tab => {
            tab.classList.toggle('active', tab.dataset.tab === tabName);
        });
        document.querySelectorAll('.stock-tab-content').forEach(content => {
            content.classList.toggle('active', content.id === `tab-${tabName}`);
        });
    }
    if (tabName === 'options' && currentModalTicker) {
        loadOptionsExpirations(currentModalTicker);
    }
};

window.loadOptionsExpirations = loadOptionsExpirations;
window.loadOptionsForExpiration = loadOptionsForExpiration;
