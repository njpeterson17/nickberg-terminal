/**
 * News Sentinel Bot - Dashboard JavaScript
 */

// Global chart instance
let mainChart = null;
let sentimentChart = null;
let currentChartType = 'mentions';

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
    initDashboard();
    setupEventListeners();
    setupSettingsEventListeners();

    // Auto-refresh every 60 seconds
    setInterval(refreshData, 60000);
});

async function initDashboard() {
    await loadAllData();
}

function setupEventListeners() {
    // Refresh button
    document.getElementById('refreshBtn').addEventListener('click', () => {
        refreshData();
    });
    
    // Run bot button
    document.getElementById('runBotBtn').addEventListener('click', runBot);
    
    // Chart tabs
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            e.target.classList.add('active');
            currentChartType = e.target.dataset.chart;
            updateMainChart();
        });
    });
}

async function loadAllData() {
    try {
        showStatus('loading');
        
        await Promise.all([
            loadStats(),
            loadAlerts(),
            loadTopCompanies(),
            loadArticles(),
            loadSentiment(),
            updateMainChart()
        ]);
        
        updateLastUpdated();
        showStatus('ready');
    } catch (error) {
        console.error('Error loading data:', error);
        showToast('Error loading data', 'error');
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
    const response = await fetch('/api/stats');
    const stats = await response.json();
    
    document.getElementById('totalArticles').textContent = formatNumber(stats.total_articles);
    document.getElementById('totalMentions').textContent = formatNumber(stats.total_mentions);
    document.getElementById('totalAlerts').textContent = formatNumber(stats.total_alerts);
    document.getElementById('articles24h').textContent = formatNumber(stats.articles_24h);
}

// Load alerts
async function loadAlerts() {
    const response = await fetch('/api/alerts');
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
        await fetch(`/api/alerts/${id}/ack`, { method: 'POST' });
        loadAlerts();
        showToast('Alert acknowledged', 'success');
    } catch (error) {
        showToast('Error acknowledging alert', 'error');
    }
}

// Load top companies
async function loadTopCompanies() {
    const response = await fetch('/api/companies/top?limit=10');
    const companies = await response.json();
    
    const container = document.getElementById('topCompanies');
    
    if (companies.length === 0) {
        container.innerHTML = '<div class="empty-state">No data yet</div>';
        return;
    }
    
    container.innerHTML = companies.map((company, index) => `
        <div class="company-item">
            <div class="company-info">
                <span class="company-rank ${index < 3 ? 'top' : ''}">${index + 1}</span>
                <div>
                    <span class="company-name">${company.company_name}</span>
                    <span class="company-ticker">${company.company_ticker}</span>
                </div>
            </div>
            <span class="company-count">${company.count}</span>
        </div>
    `).join('');
}

// Load articles
async function loadArticles() {
    const response = await fetch('/api/articles?limit=20');
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
    const response = await fetch('/api/sentiment');
    const data = await response.json();
    
    // Update stats
    const statsContainer = document.getElementById('sentimentStats');
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
    
    // Update chart
    const ctx = document.getElementById('sentimentChart').getContext('2d');
    
    if (sentimentChart) {
        sentimentChart.destroy();
    }
    
    sentimentChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: ['Positive', 'Neutral', 'Negative'],
            datasets: [{
                data: [data.positive, data.neutral, data.negative],
                backgroundColor: ['#10b981', '#64748b', '#ef4444'],
                borderWidth: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: {
                        color: '#94a3b8',
                        padding: 20,
                        usePointStyle: true
                    }
                }
            }
        }
    });
}

// Update main chart
async function updateMainChart() {
    const ctx = document.getElementById('mainChart').getContext('2d');
    
    if (mainChart) {
        mainChart.destroy();
    }
    
    if (currentChartType === 'mentions') {
        const response = await fetch('/api/timeline?hours=24');
        const data = await response.json();
        renderMentionsChart(ctx, data);
    } else if (currentChartType === 'sentiment') {
        renderSentimentTrendChart(ctx);
    } else if (currentChartType === 'sources') {
        const response = await fetch('/api/sources');
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
        const response = await fetch('/api/run', { method: 'POST' });
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
            fetch('/api/preferences'),
            fetch('/api/watchlist'),
            fetch('/api/alert-rules')
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
        const response = await fetch('/api/watchlist', {
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
        const response = await fetch('/api/watchlist', {
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
        const prefsResponse = await fetch('/api/preferences', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                thresholds: thresholds
            })
        });

        // Save alert rules
        const rulesResponse = await fetch('/api/alert-rules', {
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
