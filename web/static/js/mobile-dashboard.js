/**
 * Nickberg Terminal - Mobile Dashboard JavaScript
 * PWA-enabled mobile dashboard with offline support
 * @version 1.0.0
 */

// ============================================
// SERVICE WORKER REGISTRATION
// ============================================

/**
 * Register the Service Worker for PWA functionality
 * This enables offline support, background sync, and push notifications
 */
async function registerServiceWorker() {
    if (!('serviceWorker' in navigator)) {
        console.log('[Mobile] Service Workers not supported');
        return;
    }
    
    try {
        const registration = await navigator.serviceWorker.register('/static/js/service-worker.js', {
            scope: '/'
        });
        
        console.log('[Mobile] Service Worker registered:', registration.scope);
        
        // Handle service worker updates
        registration.addEventListener('updatefound', () => {
            const newWorker = registration.installing;
            
            newWorker.addEventListener('statechange', () => {
                if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
                    // New version available
                    showUpdateNotification(newWorker);
                }
            });
        });
        
        // Listen for messages from service worker
        navigator.serviceWorker.addEventListener('message', handleSWMessage);
        
        return registration;
    } catch (error) {
        console.error('[Mobile] Service Worker registration failed:', error);
    }
}

/**
 * Handle messages from the Service Worker
 */
function handleSWMessage(event) {
    const { type, message, payload } = event.data;
    
    switch (type) {
        case 'SYNC_COMPLETE':
            showToast('Sync complete', 'success');
            refreshAllData();
            break;
            
        case 'UPDATE_AVAILABLE':
            showToast('Update available - refresh to get latest', 'info');
            break;
            
        case 'BACKGROUND_UPDATE':
            showToast('Data updated in background', 'info');
            break;
            
        case 'CACHE_INFO':
            console.log('[Mobile] Cache info:', payload);
            break;
            
        default:
            console.log('[Mobile] SW Message:', event.data);
    }
}

/**
 * Show update notification when new service worker is available
 */
function showUpdateNotification(worker) {
    showToast('New version available! Tap to update', 'info', () => {
        worker.postMessage({ type: 'SKIP_WAITING' });
        window.location.reload();
    });
}

// ============================================
// PWA INSTALL PROMPT
// ============================================

let deferredInstallPrompt = null;

/**
 * Initialize PWA install prompt handling
 */
function initInstallPrompt() {
    // Listen for beforeinstallprompt event
    window.addEventListener('beforeinstallprompt', (e) => {
        // Prevent the default mini-infobar from appearing
        e.preventDefault();
        // Store the event for later use
        deferredInstallPrompt = e;
        // Show custom install prompt after a delay
        setTimeout(() => {
            showInstallPrompt();
        }, 3000);
    });
    
    // Handle installed event
    window.addEventListener('appinstalled', () => {
        console.log('[Mobile] PWA was installed');
        deferredInstallPrompt = null;
        hideInstallPrompt();
        showToast('Nickberg installed successfully!', 'success');
    });
}

/**
 * Show the custom install prompt
 */
function showInstallPrompt() {
    const prompt = document.getElementById('installPrompt');
    if (prompt && deferredInstallPrompt) {
        prompt.style.display = 'flex';
    }
}

/**
 * Hide the install prompt
 */
function hideInstallPrompt() {
    const prompt = document.getElementById('installPrompt');
    if (prompt) {
        prompt.style.display = 'none';
    }
}

/**
 * Trigger the PWA installation
 */
async function installPWA() {
    if (!deferredInstallPrompt) {
        return;
    }
    
    // Show the install prompt
    deferredInstallPrompt.prompt();
    
    // Wait for user choice
    const { outcome } = await deferredInstallPrompt.userChoice;
    
    if (outcome === 'accepted') {
        console.log('[Mobile] User accepted install');
    } else {
        console.log('[Mobile] User dismissed install');
    }
    
    // Clear the deferred prompt
    deferredInstallPrompt = null;
    hideInstallPrompt();
}

// ============================================
// NETWORK STATUS
// ============================================

/**
 * Initialize network status monitoring
 */
function initNetworkStatus() {
    updateConnectionStatus();
    
    window.addEventListener('online', () => {
        updateConnectionStatus();
        showToast('Back online', 'success');
        // Refresh data when coming back online
        refreshAllData();
    });
    
    window.addEventListener('offline', () => {
        updateConnectionStatus();
        showToast('You are offline - using cached data', 'info');
    });
}

/**
 * Update the connection status indicator
 */
function updateConnectionStatus() {
    const statusEl = document.getElementById('connectionStatus');
    const textEl = document.getElementById('connectionText');
    const cacheStatusEl = document.getElementById('cacheStatus');
    const offlineItem = document.getElementById('offlineModeItem');
    
    if (navigator.onLine) {
        statusEl.classList.remove('offline');
        statusEl.classList.add('online');
        textEl.textContent = 'Online';
        cacheStatusEl.style.display = 'none';
        if (offlineItem) offlineItem.style.display = 'none';
    } else {
        statusEl.classList.remove('online');
        statusEl.classList.add('offline');
        textEl.textContent = 'Offline';
        cacheStatusEl.style.display = 'flex';
        if (offlineItem) offlineItem.style.display = 'flex';
    }
}

// ============================================
// UI COMPONENTS
// ============================================

/**
 * Show toast notification
 */
function showToast(message, type = 'info', onClick = null) {
    const container = document.getElementById('toastContainer');
    
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    
    const iconMap = {
        success: 'fa-check-circle',
        error: 'fa-exclamation-circle',
        info: 'fa-info-circle'
    };
    
    toast.innerHTML = `
        <i class="fas ${iconMap[type] || iconMap.info}" style="color: var(--accent-${type === 'success' ? 'green' : type === 'error' ? 'red' : 'blue'});"></i>
        <span class="toast-message">${message}</span>
    `;
    
    if (onClick) {
        toast.style.cursor = 'pointer';
        toast.addEventListener('click', onClick);
    }
    
    container.appendChild(toast);
    
    // Auto remove after 4 seconds
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(-20px)';
        toast.style.transition = 'all 0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

/**
 * Initialize tab navigation
 */
function initTabs() {
    const tabs = document.querySelectorAll('.nav-tab');
    const contents = document.querySelectorAll('.tab-content');
    
    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const targetTab = tab.dataset.tab;
            
            // Update active tab
            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            
            // Update active content
            contents.forEach(c => c.classList.remove('active'));
            document.getElementById(`${targetTab}Tab`).classList.add('active');
            
            // Load tab-specific data
            if (targetTab === 'alerts') {
                loadAlerts();
            } else if (targetTab === 'companies') {
                loadCompanies();
            }
        });
    });
}

/**
 * Initialize pull-to-refresh functionality
 */
function initPullToRefresh() {
    let touchStartY = 0;
    let touchEndY = 0;
    let isPulling = false;
    
    const content = document.querySelector('.tab-content.active');
    
    document.addEventListener('touchstart', (e) => {
        touchStartY = e.changedTouches[0].screenY;
        // Only enable pull-to-refresh when at top of page
        isPulling = window.scrollY === 0;
    }, { passive: true });
    
    document.addEventListener('touchend', (e) => {
        if (!isPulling) return;
        
        touchEndY = e.changedTouches[0].screenY;
        const diff = touchEndY - touchStartY;
        
        // If pulled down more than 100px, refresh
        if (diff > 100) {
            refreshAllData();
            showToast('Refreshing...', 'info');
        }
        
        isPulling = false;
    }, { passive: true });
}

// ============================================
// DATA FETCHING
// ============================================

const API_BASE = '';
let isRefreshing = false;

/**
 * Fetch with timeout and error handling
 */
async function fetchWithTimeout(url, options = {}, timeout = 10000) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeout);
    
    try {
        const response = await fetch(url, {
            ...options,
            signal: controller.signal
        });
        clearTimeout(timeoutId);
        return response;
    } catch (error) {
        clearTimeout(timeoutId);
        throw error;
    }
}

/**
 * Load dashboard statistics
 */
async function loadStats() {
    try {
        const response = await fetchWithTimeout(`${API_BASE}/api/stats`);
        const data = await response.json();
        
        document.getElementById('statArticles').textContent = data.total_articles?.toLocaleString() || '--';
        document.getElementById('statMentions').textContent = data.total_mentions?.toLocaleString() || '--';
        document.getElementById('statAlerts').textContent = data.total_alerts?.toLocaleString() || '--';
        document.getElementById('stat24h').textContent = data.articles_24h?.toLocaleString() || '--';
    } catch (error) {
        console.error('[Mobile] Failed to load stats:', error);
    }
}

/**
 * Load latest articles
 */
async function loadArticles() {
    const container = document.getElementById('articlesList');
    
    try {
        const response = await fetchWithTimeout(`${API_BASE}/api/articles?limit=10`);
        const articles = await response.json();
        
        if (!articles || articles.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-newspaper"></i>
                    <p>No articles found</p>
                </div>
            `;
            return;
        }
        
        container.innerHTML = articles.map(article => {
            const sentiment = article.sentiment > 0.2 ? 'positive' : article.sentiment < -0.2 ? 'negative' : 'neutral';
            const sentimentText = sentiment === 'positive' ? 'Bullish' : sentiment === 'negative' ? 'Bearish' : 'Neutral';
            const timeAgo = formatTimeAgo(article.published_at);
            
            return `
                <div class="article-item" onclick="window.open('${article.url}', '_blank')">
                    <div class="article-source">
                        <i class="fas fa-rss"></i>
                        ${article.source}
                    </div>
                    <div class="article-title">${escapeHtml(article.title)}</div>
                    <div class="article-meta">
                        <span class="article-time">${timeAgo}</span>
                        <span class="article-sentiment ${sentiment}">${sentimentText}</span>
                    </div>
                </div>
            `;
        }).join('');
    } catch (error) {
        console.error('[Mobile] Failed to load articles:', error);
        container.innerHTML = `
            <div class="empty-state">
                <i class="fas fa-wifi-slash"></i>
                <p>Unable to load articles</p>
            </div>
        `;
    }
}

/**
 * Load alerts
 */
async function loadAlerts() {
    const container = document.getElementById('alertsList');
    
    try {
        const response = await fetchWithTimeout(`${API_BASE}/api/alerts?limit=10`);
        const alerts = await response.json();
        
        if (!alerts || alerts.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-bell-slash"></i>
                    <p>No active alerts</p>
                </div>
            `;
            return;
        }
        
        container.innerHTML = alerts.map(alert => {
            const timeAgo = formatTimeAgo(alert.created_at);
            
            return `
                <div class="alert-item ${alert.severity}">
                    <div class="alert-header">
                        <span class="alert-type">${alert.type}</span>
                        <span class="alert-severity ${alert.severity}">${alert.severity}</span>
                    </div>
                    <div class="alert-message">${escapeHtml(alert.message)}</div>
                    <div class="alert-meta">
                        <span>${alert.ticker || alert.company || 'System'}</span>
                        <span>${timeAgo}</span>
                    </div>
                </div>
            `;
        }).join('');
    } catch (error) {
        console.error('[Mobile] Failed to load alerts:', error);
        container.innerHTML = `
            <div class="empty-state">
                <i class="fas fa-wifi-slash"></i>
                <p>Unable to load alerts</p>
            </div>
        `;
    }
}

/**
 * Load top mentioned companies
 */
async function loadCompanies() {
    const container = document.getElementById('companiesList');
    
    try {
        const response = await fetchWithTimeout(`${API_BASE}/api/companies/top?limit=10`);
        const companies = await response.json();
        
        if (!companies || companies.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-building"></i>
                    <p>No companies found</p>
                </div>
            `;
            return;
        }
        
        container.innerHTML = companies.map(company => `
            <div class="company-item">
                <div class="company-info">
                    <span class="company-ticker">${company.ticker}</span>
                    <span class="company-name">${escapeHtml(company.name)}</span>
                </div>
                <div class="company-stats">
                    <div class="company-mentions" style="color: var(--accent-orange);">${company.mention_count}</div>
                    <div class="company-label">mentions</div>
                </div>
            </div>
        `).join('');
    } catch (error) {
        console.error('[Mobile] Failed to load companies:', error);
        container.innerHTML = `
            <div class="empty-state">
                <i class="fas fa-wifi-slash"></i>
                <p>Unable to load companies</p>
            </div>
        `;
    }
}

/**
 * Load market ticker data
 */
async function loadMarketTicker() {
    const symbols = ['SPY', 'QQQ', 'DIA', 'IWM', 'VIX'];
    
    try {
        const response = await fetchWithTimeout(`${API_BASE}/api/prices?tickers=${symbols.join(',')}`);
        const prices = await response.json();
        
        const tickerContent = document.getElementById('marketTicker');
        
        tickerContent.innerHTML = symbols.map(symbol => {
            const data = prices[symbol];
            if (!data) return '';
            
            const changeClass = data.change_pct > 0 ? 'positive' : data.change_pct < 0 ? 'negative' : 'neutral';
            const changePrefix = data.change_pct > 0 ? '+' : '';
            
            return `
                <div class="ticker-item">
                    <span class="ticker-symbol">${symbol}</span>
                    <span class="ticker-price">$${data.price?.toFixed(2) || '--'}</span>
                    <span class="ticker-change ${changeClass}">${changePrefix}${data.change_pct?.toFixed(2) || '--'}%</span>
                </div>
            `;
        }).join('');
    } catch (error) {
        console.error('[Mobile] Failed to load market ticker:', error);
    }
}

/**
 * Refresh all data
 */
async function refreshAllData() {
    if (isRefreshing) return;
    isRefreshing = true;
    
    const refreshBtn = document.getElementById('refreshBtn');
    if (refreshBtn) {
        refreshBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
    }
    
    await Promise.all([
        loadStats(),
        loadArticles(),
        loadAlerts(),
        loadCompanies(),
        loadMarketTicker()
    ]);
    
    if (refreshBtn) {
        refreshBtn.innerHTML = '<i class="fas fa-sync-alt"></i>';
    }
    
    isRefreshing = false;
}

// ============================================
// UTILITY FUNCTIONS
// ============================================

/**
 * Format time ago
 */
function formatTimeAgo(dateString) {
    if (!dateString) return 'Unknown';
    
    const date = new Date(dateString);
    const now = new Date();
    const seconds = Math.floor((now - date) / 1000);
    
    if (seconds < 60) return 'Just now';
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`;
    
    return date.toLocaleDateString();
}

/**
 * Escape HTML to prevent XSS
 */
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ============================================
// PUSH NOTIFICATIONS
// ============================================

/**
 * Initialize push notifications
 */
async function initPushNotifications() {
    const toggle = document.getElementById('pushToggle');
    if (!toggle) return;
    
    // Check if notifications are supported
    if (!('Notification' in window) || !('PushManager' in window)) {
        toggle.disabled = true;
        return;
    }
    
    // Check current permission status
    if (Notification.permission === 'granted') {
        toggle.checked = true;
    }
    
    toggle.addEventListener('change', async () => {
        if (toggle.checked) {
            const permission = await Notification.requestPermission();
            
            if (permission === 'granted') {
                showToast('Push notifications enabled', 'success');
                subscribeToPushNotifications();
            } else {
                toggle.checked = false;
                showToast('Permission denied for notifications', 'error');
            }
        } else {
            unsubscribeFromPushNotifications();
        }
    });
}

/**
 * Subscribe to push notifications
 */
async function subscribeToPushNotifications() {
    try {
        const registration = await navigator.serviceWorker.ready;
        
        // Check for existing subscription
        let subscription = await registration.pushManager.getSubscription();
        
        if (!subscription) {
            // Create new subscription
            // In production, you would get this from your server
            const vapidPublicKey = 'YOUR_VAPID_PUBLIC_KEY';
            
            subscription = await registration.pushManager.subscribe({
                userVisibleOnly: true,
                applicationServerKey: urlBase64ToUint8Array(vapidPublicKey)
            });
            
            // Send subscription to server
            await sendSubscriptionToServer(subscription);
        }
        
        console.log('[Mobile] Push subscription:', subscription);
    } catch (error) {
        console.error('[Mobile] Push subscription failed:', error);
    }
}

/**
 * Unsubscribe from push notifications
 */
async function unsubscribeFromPushNotifications() {
    try {
        const registration = await navigator.serviceWorker.ready;
        const subscription = await registration.pushManager.getSubscription();
        
        if (subscription) {
            await subscription.unsubscribe();
            await removeSubscriptionFromServer(subscription);
            showToast('Push notifications disabled', 'info');
        }
    } catch (error) {
        console.error('[Mobile] Unsubscribe failed:', error);
    }
}

/**
 * Convert URL-safe base64 to Uint8Array
 */
function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding)
        .replace(/\-/g, '+')
        .replace(/_/g, '/');
    
    const rawData = window.atob(base64);
    const outputArray = new Uint8Array(rawData.length);
    
    for (let i = 0; i < rawData.length; ++i) {
        outputArray[i] = rawData.charCodeAt(i);
    }
    
    return outputArray;
}

/**
 * Send subscription to server
 */
async function sendSubscriptionToServer(subscription) {
    // In production, send this to your backend
    console.log('[Mobile] Send subscription to server:', subscription.toJSON());
}

/**
 * Remove subscription from server
 */
async function removeSubscriptionFromServer(subscription) {
    // In production, remove this from your backend
    console.log('[Mobile] Remove subscription from server:', subscription.toJSON());
}

// ============================================
// CACHE MANAGEMENT
// ============================================

/**
 * Clear all caches
 */
async function clearCaches() {
    if ('serviceWorker' in navigator && navigator.serviceWorker.controller) {
        navigator.serviceWorker.controller.postMessage({ type: 'CLEAR_CACHE' });
        showToast('Cache cleared', 'success');
    }
}

/**
 * Get cache info
 */
async function getCacheInfo() {
    if ('serviceWorker' in navigator && navigator.serviceWorker.controller) {
        navigator.serviceWorker.controller.postMessage({ type: 'GET_CACHE_INFO' });
    }
}

// ============================================
// INITIALIZATION
// ============================================

/**
 * Initialize the mobile dashboard
 */
function initMobileDashboard() {
    console.log('[Mobile] Initializing dashboard...');
    
    // Register service worker
    registerServiceWorker();
    
    // Initialize PWA install prompt
    initInstallPrompt();
    
    // Initialize network status monitoring
    initNetworkStatus();
    
    // Initialize UI components
    initTabs();
    initPullToRefresh();
    initPushNotifications();
    
    // Event listeners
    document.getElementById('refreshBtn')?.addEventListener('click', () => {
        refreshAllData();
        showToast('Refreshing...', 'info');
    });
    
    document.getElementById('installBtn')?.addEventListener('click', installPWA);
    document.getElementById('closeInstallBtn')?.addEventListener('click', hideInstallPrompt);
    document.getElementById('clearCacheBtn')?.addEventListener('click', clearCaches);
    document.getElementById('refreshDataBtn')?.addEventListener('click', () => {
        refreshAllData();
        showToast('Refreshing...', 'info');
    });
    
    // Search functionality
    document.getElementById('searchInput')?.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            const query = e.target.value.trim();
            if (query) {
                // Navigate to search results or execute command
                showToast(`Searching for "${query}"...`, 'info');
            }
        }
    });
    
    // Initial data load
    refreshAllData();
    
    // Auto-refresh every 60 seconds
    setInterval(() => {
        if (navigator.onLine) {
            refreshAllData();
        }
    }, 60000);
    
    console.log('[Mobile] Dashboard initialized');
}

// Start when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initMobileDashboard);
} else {
    initMobileDashboard();
}
