# BlueSky Loader Integration Guide

This guide walks you through integrating the optimized BlueSky loader into your dashboard.

## Overview

The optimized loader provides:
- **5x faster loading** through parallel fetching (5 concurrent requests)
- **Progressive rendering** - shows content in ~500ms instead of waiting for all 45 accounts
- **Persistent caching** via IndexedDB (10 minute TTL)
- **Lazy image loading** - images load only when scrolled into view
- **Automatic retry** with exponential backoff
- **Request deduplication** - prevents duplicate API calls

## Step 1: Include the Script

Add the optimized loader script to your HTML, replacing the old inline code:

```html
<!-- In your HTML head or before closing body -->
<script src="/static/js/bluesky-loader-optimized.js"></script>
```

**Remove these old script references if they exist:**
- Any inline `initBlueskyFeed()` function
- Old `fetchBlueskyPosts()` implementations

## Step 2: Update dashboard.js

### 2.1 Replace the init function

Find your existing `initBlueskyFeed()` function and replace it with:

```javascript
// ============================================
// BLUESKY FEED - Optimized Loader Integration
// ============================================

let blueskyLoader = null;

async function initBlueskyFeed() {
    const feedContainer = document.getElementById('blueskyFeedFull');
    if (!feedContainer) return;
    
    // Cleanup existing loader if any
    if (blueskyLoader) {
        blueskyLoader.destroy();
    }
    
    // Create new loader instance
    blueskyLoader = new BlueskyLoader({
        CONCURRENCY: 5,              // 5 parallel API calls
        POSTS_PER_ACCOUNT: 5,        // Posts per account
        CACHE_TTL: 2 * 60 * 1000,    // 2 minutes
        PRIORITY_ACCOUNTS: [         // Load these first for quick display
            'unusualwhales.bsky.social',
            'spotgamma.bsky.social', 
            'strazza.bsky.social'
        ]
    });
    
    // Load the feed with progress callbacks
    await blueskyLoader.load(BLUESKY_FINANCIAL_ACCOUNTS, feedContainer, {
        onProgress: (posts, errors, batchSize) => {
            // Optional: Show loading progress
            console.log(`Loaded ${posts.length} posts...`);
        },
        onComplete: (posts, errors) => {
            console.log(`BlueSky feed ready: ${posts.length} posts`);
            if (errors.length > 0) {
                console.warn('Some accounts failed to load:', errors);
            }
        },
        onError: (error) => {
            console.error('BlueSky feed error:', error);
            showBlueskyError(feedContainer, 'Failed to load feed. Will retry shortly.');
        }
    });
    
    // Setup auto-refresh every 2 minutes
    setupBlueskyRefresh();
}

function setupBlueskyRefresh() {
    // Clear any existing interval
    if (window.blueskyRefreshInterval) {
        clearInterval(window.blueskyRefreshInterval);
    }
    
    window.blueskyRefreshInterval = setInterval(() => {
        if (blueskyLoader && !blueskyLoader.loading) {
            console.log('[BlueSky] Auto-refreshing feed...');
            blueskyLoader.refresh(BLUESKY_FINANCIAL_ACCOUNTS);
        }
    }, 120000); // 2 minutes
}

// Helper: Show error message
function showBlueskyError(container, message) {
    container.innerHTML = `
        <div class="bluesky-error">
            <i class="fas fa-exclamation-triangle"></i>
            <span>${message}</span>
            <button onclick="initBlueskyFeed()" class="retry-btn">Retry</button>
        </div>
    `;
}
```

### 2.2 Keep your existing constants

Keep your existing `BLUESKY_FINANCIAL_ACCOUNTS` array - the loader uses the same format:

```javascript
const BLUESKY_FINANCIAL_ACCOUNTS = [
    { handle: 'unusualwhales.bsky.social', name: 'Unusual Whales', display: 'Unusual Whales' },
    { handle: 'spotgamma.bsky.social', name: 'SpotGamma', display: 'SpotGamma' },
    // ... rest of your accounts
];
```

### 2.3 Cleanup on page unload

Add cleanup to prevent memory leaks:

```javascript
// Cleanup when leaving page
window.addEventListener('beforeunload', () => {
    if (blueskyLoader) {
        blueskyLoader.destroy();
    }
    if (window.blueskyRefreshInterval) {
        clearInterval(window.blueskyRefreshInterval);
    }
});
```

## Step 3: Add CSS Styles

Add these styles to your CSS file for the optimized loader:

```css
/* ============================================
   BLUESKY FEED - Optimized Styles
   ============================================ */

.bluesky-loading {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
    padding: 2rem;
    color: var(--text-secondary);
}

.bluesky-loading i {
    font-size: 1.5rem;
}

.bluesky-error {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 1rem;
    padding: 2rem;
    color: var(--danger);
    text-align: center;
}

.bluesky-error i {
    font-size: 2rem;
}

.bluesky-error .retry-btn {
    padding: 0.5rem 1.5rem;
    background: var(--accent);
    color: white;
    border: none;
    border-radius: 6px;
    cursor: pointer;
    font-weight: 500;
}

.bluesky-post-item {
    padding: 1rem;
    border-bottom: 1px solid var(--border);
    cursor: pointer;
    transition: background 0.2s ease;
}

.bluesky-post-item:hover {
    background: var(--bg-secondary);
}

.bluesky-post-item.visible {
    opacity: 1;
}

.bluesky-post-header {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin-bottom: 0.5rem;
}

.bluesky-avatar {
    width: 40px;
    height: 40px;
    border-radius: 50%;
    object-fit: cover;
    background: var(--bg-tertiary);
}

.bluesky-avatar.lazy {
    opacity: 0;
    transition: opacity 0.3s ease;
}

.bluesky-avatar:not(.lazy) {
    opacity: 1;
}

.bluesky-author-info {
    flex: 1;
    display: flex;
    flex-direction: column;
}

.bluesky-display-name {
    font-weight: 600;
    color: var(--text-primary);
}

.bluesky-handle {
    font-size: 0.875rem;
    color: var(--text-secondary);
}

.bluesky-time {
    font-size: 0.75rem;
    color: var(--text-tertiary);
}

.bluesky-post-text {
    margin-bottom: 0.75rem;
    line-height: 1.5;
    word-break: break-word;
}

.bluesky-post-text a {
    color: var(--accent);
    text-decoration: none;
}

.bluesky-post-text a:hover {
    text-decoration: underline;
}

.bluesky-post-stats {
    display: flex;
    gap: 1rem;
    font-size: 0.875rem;
    color: var(--text-secondary);
}

.bluesky-post-stats span {
    display: flex;
    align-items: center;
    gap: 0.25rem;
}

/* Embedded images */
.bluesky-embed-images {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 0.5rem;
    margin: 0.75rem 0;
}

.bluesky-embed-images img {
    width: 100%;
    height: 150px;
    object-fit: cover;
    border-radius: 8px;
}

.bluesky-embed-images img.lazy {
    opacity: 0;
    transition: opacity 0.3s ease;
}

.bluesky-embed-images img:not(.lazy) {
    opacity: 1;
}

/* External link cards */
.bluesky-embed-external {
    display: flex;
    gap: 1rem;
    padding: 0.75rem;
    background: var(--bg-secondary);
    border-radius: 8px;
    margin: 0.75rem 0;
}

.bluesky-embed-external img {
    width: 80px;
    height: 80px;
    object-fit: cover;
    border-radius: 6px;
}

.bluesky-embed-external .external-content {
    flex: 1;
}

.bluesky-embed-external .external-title {
    font-weight: 600;
    margin-bottom: 0.25rem;
}

.bluesky-embed-external .external-desc {
    font-size: 0.875rem;
    color: var(--text-secondary);
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
}
```

## Step 4: Test Locally

1. **Start your local server:**
   ```bash
   # In your project directory
   python -m http.server 8080
   # or
   npm run dev
   ```

2. **Open browser dev tools (F12) and check:**
   - Network tab: Should see ~5 parallel requests to `public.api.bsky.app`
   - Console: Should see "[BlueSky] Loaded X posts in Yms"
   - First posts should appear in ~500ms (vs 5-10 seconds before)

3. **Verify caching:**
   - Refresh the page
   - Should load instantly from IndexedDB cache
   - Check Application > IndexedDB > bluesky-cache-v1

## Step 5: Performance Monitoring

Add this to track real-world performance:

```javascript
// Add to initBlueskyFeed() onComplete callback
onComplete: (posts, errors) => {
    // Report metrics
    if (window.performance && window.performance.mark) {
        performance.mark('bluesky-loaded');
    }
    
    console.log('[BlueSky] Metrics:', {
        totalPosts: posts.length,
        failedAccounts: errors.length,
        loadTime: performance.now() - loadStartTime
    });
}
```

## Step 6: Deploy to Production

1. **Commit the new files:**
   ```bash
   git add web/static/js/bluesky-loader-optimized.js
   git add web/static/js/INTEGRATION_GUIDE.md
   git commit -m "Add optimized BlueSky loader with parallel fetching"
   ```

2. **Deploy and monitor:**
   - Check browser console for any errors
   - Monitor average load times
   - Verify all accounts load successfully

## Configuration Options

Customize behavior by passing options to `BlueskyLoader`:

| Option | Default | Description |
|--------|---------|-------------|
| `CONCURRENCY` | 5 | Parallel API requests |
| `BATCH_SIZE` | 3 | Accounts per batch |
| `POSTS_PER_ACCOUNT` | 5 | Posts fetched per account |
| `CACHE_TTL` | 120000 | In-memory cache (ms) |
| `DB_CACHE_TTL` | 600000 | IndexedDB cache (ms) |
| `RETRY_ATTEMPTS` | 3 | Retry on failure |
| `LAZY_LOAD` | true | Load only when visible |
| `VIRTUAL_SCROLL` | true | Enable virtual scrolling |

## Troubleshooting

### Posts not loading
- Check browser console for CORS errors
- Verify `BLUESKY_FINANCIAL_ACCOUNTS` is defined before calling loader
- Ensure container element exists (`#blueskyFeedFull`)

### Slow loading still
- Reduce `CONCURRENCY` if hitting rate limits
- Check if IndexedDB is blocked by privacy settings
- Verify network tab shows parallel requests

### Images not loading
- Check if `lazy` class CSS is applied
- Verify IntersectionObserver is supported (modern browsers)
- Check image URLs in console

## Rollback Plan

If issues arise, quickly revert to old implementation:

```javascript
// Comment out new loader
// await blueskyLoader.load(...)

// Uncomment old implementation
// await loadBlueskyFeed(feedContainer);
```

---

**Need help?** Check the browser console for detailed logs prefixed with `[BlueSky]`.
