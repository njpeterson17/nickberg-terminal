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

// Initialize dashboard
document.addEventListener('DOMContentLoaded', () => {
    // Hide any chart elements (replaced with ticker)
    hideChartElements();
    
    initDashboard();
    setupEventListeners();
    setupSettingsEventListeners();
    initTicker();
    initMobileFeatures();

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
}

// Bluesky Financial Accounts to follow
const BLUESKY_FINANCIAL_ACCOUNTS = [
    // Options & Flow
    { handle: 'unusualwhales.bsky.social', name: 'Unusual Whales', display: 'Unusual Whales' },
    { handle: 'spotgamma.bsky.social', name: 'SpotGamma', display: 'SpotGamma' },
    { handle: 'tradytorp.bsky.social', name: 'Trady Torp', display: 'Trady' },
    { handle: 'carterbraxton.bsky.social', name: 'Carter Braxton', display: 'Carter Braxton' },
    
    // Macro & Markets
    { handle: 'strazza.bsky.social', name: 'Strazza', display: 'Strazza' },
    { handle: 'carnage4life.bsky.social', name: 'Carnage4Life', display: 'Carnage4Life' },
    { handle: 'truflation.bsky.social', name: 'Truflation', display: 'Truflation' },
    { handle: 'kobeissiletter.bsky.social', name: 'Kobeissi Letter', display: 'Kobeissi Letter' },
    { handle: 'dailyreckoning.bsky.social', name: 'Daily Reckoning', display: 'Daily Reckoning' },
    
    // News & Media
    { handle: 'benzinga.bsky.social', name: 'Benzinga', display: 'Benzinga' },
    { handle: 'marketwatch.bsky.social', name: 'MarketWatch', display: 'MarketWatch' },
    { handle: 'morningbrew.bsky.social', name: 'Morning Brew', display: 'Morning Brew' },
    { handle: 'theblock.bsky.social', name: 'The Block', display: 'The Block' },
    { handle: 'coindesk.bsky.social', name: 'CoinDesk', display: 'CoinDesk' },
    
    // Trading & Technical Analysis
    { handle: 'stocktwits.bsky.social', name: 'StockTwits', display: 'StockTwits' },
    { handle: 'markminervini.bsky.social', name: 'Mark Minervini', display: 'Mark Minervini' },
    { handle: 'ivanhoff.bsky.social', name: 'Ivanhoff', display: 'Ivanhoff' },
    { handle: 'brianferoldi.bsky.social', name: 'Brian Feroldi', display: 'Brian Feroldi' },
    { handle: 'mrblondtrading.bsky.social', name: 'MrBlond', display: 'MrBlond' },
    
    // Sentiment & Data
    { handle: 'sentiment.bsky.social', name: 'Sentiment', display: 'Sentiment' },
    { handle: 'fintwit.bsky.social', name: 'FinTwit', display: 'FinTwit' },
    { handle: 'retailmind.bsky.social', name: 'Retail Mind', display: 'Retail Mind' },
    { handle: 'finchat.bsky.social', name: 'FinChat', display: 'FinChat' },
    
    // Crypto & Web3
    { handle: 'sassal0x.bsky.social', name: 'sassal.eth', display: 'sassal' },
    { handle: 'dcinvestor.bsky.social', name: 'DCinvestor', display: 'DCinvestor' },
    { handle: 'degentrading.bsky.social', name: 'Degen Trading', display: 'Degen' },
    { handle: 'cryptocobain.bsky.social', name: 'Crypto Cobain', display: 'Cobain' }
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
        
        // Take top 30 posts
        const topPosts = allPosts.slice(0, 30);
        
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

// Render Bluesky posts to the container
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
    
    const html = posts.map(post => {
        const timeAgo = formatTimeAgo(new Date(post.indexedAt));
        const avatarUrl = post.author.avatar || '';
        const initial = (post.author.displayName || post.author.handle).charAt(0).toUpperCase();
        
        // Highlight stock tickers
        const highlightedText = highlightTickers(escapeHtml(post.text));
        
        // Build repost indicator
        let repostHtml = '';
        if (post.isRepost && post.repostedBy) {
            repostHtml = `
                <div class="bluesky-repost-indicator">
                    <i class="fas fa-retweet"></i>
                    <span>Reposted by ${escapeHtml(post.repostedBy.displayName || post.repostedBy.handle)}</span>
                </div>
            `;
        }
        
        // Build embed (image)
        let embedHtml = '';
        if (post.embed?.$type === 'app.bsky.embed.images#view' && post.embed.images?.length > 0) {
            const image = post.embed.images[0];
            embedHtml = `
                <div class="bluesky-embed-image">
                    <img src="${image.thumb}" alt="${escapeHtml(image.alt || '')}" loading="lazy">
                </div>
            `;
        } else if (post.embed?.$type === 'app.bsky.embed.external#view') {
            const external = post.embed.external;
            embedHtml = `
                <div class="bluesky-quote-post">
                    <div style="font-size: 11px; color: var(--text-secondary); margin-bottom: 4px;">
                        <i class="fas fa-link"></i> ${escapeHtml(external.title || 'Link')}
                    </div>
                    <div style="font-size: 11px; color: var(--text-dim);">${escapeHtml(external.description || '').substring(0, 100)}...</div>
                </div>
            `;
        }
        
        return `
            <div class="bluesky-post" data-uri="${post.uri}">
                ${repostHtml}
                <div class="bluesky-post-header">
                    <div class="bluesky-avatar">
                        ${avatarUrl ? `<img src="${avatarUrl}" alt="" loading="lazy">` : initial}
                    </div>
                    <div class="bluesky-user-info">
                        <div class="bluesky-display-name">${escapeHtml(post.author.displayName || post.author.handle)}</div>
                        <div class="bluesky-handle">@${post.author.handle}</div>
                    </div>
                    <div class="bluesky-timestamp">${timeAgo}</div>
                </div>
                <div class="bluesky-post-content">${highlightedText}</div>
                ${embedHtml}
                <div class="bluesky-post-actions">
                    <div class="bluesky-action" title="Reply">
                        <i class="far fa-comment"></i>
                        <span>${formatCount(post.replyCount)}</span>
                    </div>
                    <div class="bluesky-action" title="Repost">
                        <i class="fas fa-retweet"></i>
                        <span>${formatCount(post.repostCount)}</span>
                    </div>
                    <div class="bluesky-action" title="Like">
                        <i class="far fa-heart"></i>
                        <span>${formatCount(post.likeCount)}</span>
                    </div>
                </div>
            </div>
        `;
    }).join('');
    
    container.innerHTML = html;
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
        console.log(`[Stock Lookup] Fetching details for ${symbol}`);
        showToast(`Looking up ${symbol}...`, 'info');
        
        // Fetch detailed stock info
        const response = await fetchWithTimeout(`/api/stock/${symbol}/details`);
        const data = await response.json();
        
        console.log('[Stock Lookup] Received data:', data);
        
        // Display in stock details panel
        displayStockDetails(data);
        
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
            loadTopCompanies(),
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
                    <div class="market-item" title="${info.label}">
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
                        <span class="mover-symbol">${stock.symbol}</span>
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
        <div class="company-item" data-ticker="${company.company_ticker}">
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

// Load articles
async function loadArticles() {
    const response = await fetchWithTimeout('/api/articles?limit=20');
    const articles = await response.json();
    
    const container = document.getElementById('articlesList');
    
    if (articles.length === 0) {
        container.innerHTML = '<div class="empty-state">No articles yet</div>';
        return;
    }
    
    // Update filter options
    const sources = [...new Set(articles.map(a => a.source))];
    const filterSelect = document.getElementById('articleFilter');
    filterSelect.innerHTML = '<option value="all">All Sources</option>' + 
        sources.map(s => `<option value="${s}">${s}</option>`).join('');
    
    filterSelect.addEventListener('change', (e) => {
        const filtered = e.target.value === 'all' 
            ? articles 
            : articles.filter(a => a.source === e.target.value);
        renderArticles(filtered);
    });
    
    renderArticles(articles);
}

function renderArticles(articles) {
    const container = document.getElementById('articlesList');
    
    container.innerHTML = articles.map(article => `
        <div class="article-item">
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
            <span class="stock-ticker-item">
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
    'r': { action: 'refresh', description: 'Refresh data' },
    'a': { action: 'alerts', description: 'Jump to Alerts' },
    'm': { action: 'mentions', description: 'Jump to Top Mentions' },
    'n': { action: 'news', description: 'Jump to News feed' },
    't': { action: 'ticker', description: 'Jump to Ticker' },
    's': { action: 'settings', description: 'Open Settings' },
    '/': { action: 'search', description: 'Search' },
    '?': { action: 'help', description: 'Show help' },
    'h': { action: 'help', description: 'Show help' },
};

let searchMode = false;
let helpVisible = false;

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
    
    // Handle Escape key to close overlays
    if (e.key === 'Escape') {
        if (helpVisible) {
            hideHelpOverlay();
        }
        return;
    }
    
    const key = e.key.toLowerCase();
    const shortcut = KEYBOARD_SHORTCUTS[key];
    
    if (!shortcut) return;
    
    // Handle help first
    if (shortcut.action === 'help') {
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
            e.preventDefault();
            refreshData();
            showToast('Refreshing...', 'info');
            break;
            
        case 'alerts':
            e.preventDefault();
            focusPanel('alertsList');
            highlightPanel('.alerts-panel, .panel:has(#alertsList)');
            break;
            
        case 'mentions':
            e.preventDefault();
            focusPanel('topCompanies');
            highlightPanel('#topCompanies').closest('.panel');
            break;
            
        case 'news':
            e.preventDefault();
            focusPanel('articlesList');
            highlightPanel('.articles-panel');
            break;
            
        case 'ticker':
            e.preventDefault();
            focusPanel('newsTicker');
            highlightPanel('.news-ticker-container');
            break;
            
        case 'settings':
            e.preventDefault();
            switchTab('settings');
            showToast('Settings opened', 'info');
            break;
            
        case 'search':
            e.preventDefault();
            openSearch();
            break;
            
        case 'timeframe':
            e.preventDefault();
            setChartTimeframe(shortcut.value);
            break;
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
    // Focus article filter as search
    const filter = document.getElementById('articleFilter');
    if (filter) {
        searchMode = true;
        filter.focus();
        showToast('Type to filter articles', 'info');
    }
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
    
    const shortcuts = Object.entries(KEYBOARD_SHORTCUTS)
        .filter(([key, val]) => !['1', '2', '3', '4'].includes(key)) // Exclude number keys from main list
        .map(([key, val]) => `
            <div class="help-row">
                <span class="help-key">${key.toUpperCase()}</span>
                <span class="help-desc">${val.description}</span>
            </div>
        `).join('');
    
    overlay.innerHTML = `
        <div class="help-content">
            <div class="help-header">
                <h2>⌨️  KEYBOARD SHORTCUTS</h2>
                <button class="help-close" onclick="hideHelpOverlay()">✕</button>
            </div>
            <div class="help-section">
                <h3>NAVIGATION</h3>
                ${shortcuts}
            </div>
            <div class="help-section">
                <h3>CHART TIMEFRAMES</h3>
                <div class="help-row"><span class="help-key">1</span><span class="help-desc">1 Hour</span></div>
                <div class="help-row"><span class="help-key">2</span><span class="help-desc">6 Hours</span></div>
                <div class="help-row"><span class="help-key">3</span><span class="help-desc">24 Hours</span></div>
                <div class="help-row"><span class="help-key">4</span><span class="help-desc">7 Days</span></div>
            </div>
            <div class="help-footer">
                Press ? or H to toggle this help • ESC to close
            </div>
        </div>
    `;
    
    // Close on escape
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

// Add ESC key to close stock details
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        closeStockDetails();
    }
});
