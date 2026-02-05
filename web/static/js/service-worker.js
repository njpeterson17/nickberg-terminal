/**
 * Nickberg Terminal - Service Worker
 * Provides offline support, background sync, and push notifications
 * @version 1.0.0
 */

// ============================================
// CONFIGURATION
// ============================================

const CACHE_NAME = 'nickberg-terminal-v1';
const STATIC_CACHE_NAME = `${CACHE_NAME}-static`;
const API_CACHE_NAME = `${CACHE_NAME}-api`;
const IMAGE_CACHE_NAME = `${CACHE_NAME}-images`;

// Pre-cache essential assets for offline functionality
const CORE_ASSETS = [
  '/mobile',
  '/static/css/bloomberg-theme.css',
  '/static/js/dashboard.js',
  '/static/manifest.json'
];

// CDN resources that should be cached
const CDN_ASSETS = [
  'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css',
  'https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js'
];

// URLs to cache with specific strategies
const API_URL_PATTERN = /\/api\//;
const STATIC_URL_PATTERN = /\.(css|js|json|woff2?)$/;
const IMAGE_URL_PATTERN = /\.(png|jpg|jpeg|gif|svg|webp|ico)$/i;

// Cache expiration times (in milliseconds)
const CACHE_EXPIRATION = {
  api: 5 * 60 * 1000,      // 5 minutes for API data
  images: 30 * 24 * 60 * 60 * 1000,  // 30 days for images
  static: 7 * 24 * 60 * 60 * 1000    // 7 days for static assets
};

// ============================================
// INSTALL EVENT
// ============================================

/**
 * Install Event Handler
 * Pre-caches essential assets for offline functionality
 * This runs when the service worker is first installed
 */
self.addEventListener('install', (event) => {
  console.log('[Service Worker] Installing...');
  
  // Skip waiting to activate immediately
  self.skipWaiting();
  
  // Pre-cache core assets
  event.waitUntil(
    caches.open(STATIC_CACHE_NAME)
      .then((cache) => {
        console.log('[Service Worker] Pre-caching core assets');
        // Use addAll with individual catch to prevent one failure from failing all
        const cachePromises = [...CORE_ASSETS, ...CDN_ASSETS].map(url => {
          return cache.add(url).catch(err => {
            console.warn(`[Service Worker] Failed to cache: ${url}`, err);
          });
        });
        return Promise.all(cachePromises);
      })
      .then(() => {
        console.log('[Service Worker] Core assets cached successfully');
      })
      .catch((error) => {
        console.error('[Service Worker] Pre-caching failed:', error);
      })
  );
});

// ============================================
// ACTIVATE EVENT
// ============================================

/**
 * Activate Event Handler
 * Cleans up old caches and takes control of clients
 * This runs when the service worker becomes active
 */
self.addEventListener('activate', (event) => {
  console.log('[Service Worker] Activating...');
  
  // Clean up old caches
  event.waitUntil(
    caches.keys()
      .then((cacheNames) => {
        return Promise.all(
          cacheNames
            .filter((cacheName) => {
              // Delete caches that start with 'nickberg-terminal-' but aren't the current version
              return cacheName.startsWith('nickberg-terminal-') && 
                     cacheName !== STATIC_CACHE_NAME &&
                     cacheName !== API_CACHE_NAME &&
                     cacheName !== IMAGE_CACHE_NAME;
            })
            .map((cacheName) => {
              console.log(`[Service Worker] Deleting old cache: ${cacheName}`);
              return caches.delete(cacheName);
            })
        );
      })
      .then(() => {
        console.log('[Service Worker] Old caches cleaned up');
        // Take control of all clients immediately
        return self.clients.claim();
      })
      .catch((error) => {
        console.error('[Service Worker] Cache cleanup failed:', error);
      })
  );
});

// ============================================
// FETCH EVENT
// ============================================

/**
 * Fetch Event Handler
 * Intercepts all network requests and applies appropriate caching strategies
 * Strategies:
 * - API calls: Network first, cache fallback (stale-while-revalidate)
 * - Static assets: Cache first, network fallback
 * - Images: Cache first with expiration (30 days)
 */
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);
  
  // Skip non-GET requests and browser extensions
  if (request.method !== 'GET' || url.protocol === 'chrome-extension:') {
    return;
  }
  
  // Handle API requests (Network First strategy)
  if (API_URL_PATTERN.test(url.pathname)) {
    event.respondWith(handleApiRequest(request));
    return;
  }
  
  // Handle image requests (Cache First with expiration)
  if (IMAGE_URL_PATTERN.test(url.pathname) || request.destination === 'image') {
    event.respondWith(handleImageRequest(request));
    return;
  }
  
  // Handle static assets (Cache First strategy)
  if (STATIC_URL_PATTERN.test(url.pathname) || 
      request.destination === 'script' || 
      request.destination === 'style' ||
      request.destination === 'font') {
    event.respondWith(handleStaticRequest(request));
    return;
  }
  
  // Handle navigation requests (HTML pages)
  if (request.mode === 'navigate') {
    event.respondWith(handleNavigationRequest(request));
    return;
  }
  
  // Default: Network first, cache fallback
  event.respondWith(handleDefaultRequest(request));
});

/**
 * API Request Handler - Network First Strategy
 * Tries network first, falls back to cache if offline
 * Updates cache with fresh data when network succeeds
 */
async function handleApiRequest(request) {
  const cache = await caches.open(API_CACHE_NAME);
  
  try {
    // Try network first
    const networkResponse = await fetch(request);
    
    if (networkResponse.ok) {
      // Clone and cache the response
      const responseToCache = networkResponse.clone();
      
      // Add timestamp for expiration tracking
      const headers = new Headers(responseToCache.headers);
      headers.append('x-cached-at', Date.now().toString());
      
      const modifiedResponse = new Response(responseToCache.body, {
        status: responseToCache.status,
        statusText: responseToCache.statusText,
        headers: headers
      });
      
      cache.put(request, modifiedResponse);
    }
    
    return networkResponse;
  } catch (error) {
    console.log('[Service Worker] API network failed, trying cache:', error);
    
    // Fallback to cache
    const cachedResponse = await cache.match(request);
    
    if (cachedResponse) {
      // Check if cache is expired
      const cachedAt = cachedResponse.headers.get('x-cached-at');
      if (cachedAt && (Date.now() - parseInt(cachedAt)) > CACHE_EXPIRATION.api) {
        console.log('[Service Worker] API cache expired, returning stale data');
        // Return stale data but trigger background refresh
        refreshCacheInBackground(request, cache);
      }
      return cachedResponse;
    }
    
    // No cache available - return offline error
    return new Response(
      JSON.stringify({ error: 'Offline', message: 'No cached data available' }),
      {
        status: 503,
        headers: { 'Content-Type': 'application/json' }
      }
    );
  }
}

/**
 * Static Asset Handler - Cache First Strategy
 * Serves from cache immediately, updates cache in background
 */
async function handleStaticRequest(request) {
  const cache = await caches.open(STATIC_CACHE_NAME);
  const cachedResponse = await cache.match(request);
  
  if (cachedResponse) {
    // Return cached version immediately
    // Update cache in background for next time
    refreshCacheInBackground(request, cache);
    return cachedResponse;
  }
  
  // Not in cache - fetch from network
  try {
    const networkResponse = await fetch(request);
    
    if (networkResponse.ok) {
      cache.put(request, networkResponse.clone());
    }
    
    return networkResponse;
  } catch (error) {
    console.error('[Service Worker] Failed to fetch static asset:', error);
    throw error;
  }
}

/**
 * Image Handler - Cache First with Expiration
 * Caches images for 30 days
 */
async function handleImageRequest(request) {
  const cache = await caches.open(IMAGE_CACHE_NAME);
  const cachedResponse = await cache.match(request);
  
  if (cachedResponse) {
    // Check expiration
    const cachedAt = cachedResponse.headers.get('x-cached-at');
    const isExpired = cachedAt && (Date.now() - parseInt(cachedAt)) > CACHE_EXPIRATION.images;
    
    if (!isExpired) {
      return cachedResponse;
    }
    
    // Expired - fetch fresh but return cached as fallback
    refreshCacheInBackground(request, cache);
    return cachedResponse;
  }
  
  // Not in cache - fetch and store
  try {
    const networkResponse = await fetch(request);
    
    if (networkResponse.ok) {
      // Add timestamp header
      const headers = new Headers(networkResponse.headers);
      headers.append('x-cached-at', Date.now().toString());
      
      const modifiedResponse = new Response(networkResponse.body, {
        status: networkResponse.status,
        statusText: networkResponse.statusText,
        headers: headers
      });
      
      cache.put(request, modifiedResponse);
      return networkResponse;
    }
    
    return networkResponse;
  } catch (error) {
    console.error('[Service Worker] Failed to fetch image:', error);
    // Return a placeholder or error response
    return new Response('Image unavailable offline', { status: 503 });
  }
}

/**
 * Navigation Handler - Network First with Offline Fallback
 * For HTML page navigation requests
 */
async function handleNavigationRequest(request) {
  const cache = await caches.open(STATIC_CACHE_NAME);
  
  try {
    const networkResponse = await fetch(request);
    
    if (networkResponse.ok) {
      cache.put(request, networkResponse.clone());
    }
    
    return networkResponse;
  } catch (error) {
    console.log('[Service Worker] Navigation network failed, using cache');
    
    const cachedResponse = await cache.match(request);
    
    if (cachedResponse) {
      return cachedResponse;
    }
    
    // Try to return the cached mobile page as fallback
    const mobileFallback = await cache.match('/mobile');
    if (mobileFallback) {
      return mobileFallback;
    }
    
    // Ultimate fallback - offline page
    return new Response(
      `
      <!DOCTYPE html>
      <html>
      <head>
        <title>Offline - Nickberg Terminal</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
          body { 
            font-family: system-ui, sans-serif; 
            background: #1a1d21; 
            color: #fff; 
            display: flex; 
            align-items: center; 
            justify-content: center; 
            height: 100vh; 
            margin: 0; 
            text-align: center;
          }
          .offline-container { padding: 20px; }
          h1 { font-size: 48px; margin-bottom: 16px; }
          p { color: #888; }
          button {
            background: #ff6b35;
            color: white;
            border: none;
            padding: 12px 24px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 16px;
            margin-top: 20px;
          }
        </style>
      </head>
      <body>
        <div class="offline-container">
          <h1>🔮</h1>
          <h2>You're Offline</h2>
          <p>Nickberg Terminal requires an internet connection.<br>Please check your connection and try again.</p>
          <button onclick="location.reload()">Retry</button>
        </div>
      </body>
      </html>
      `,
      {
        headers: { 'Content-Type': 'text/html' }
      }
    );
  }
}

/**
 * Default Request Handler
 * Network first with cache fallback
 */
async function handleDefaultRequest(request) {
  const cache = await caches.open(STATIC_CACHE_NAME);
  
  try {
    const networkResponse = await fetch(request);
    
    if (networkResponse.ok) {
      cache.put(request, networkResponse.clone());
    }
    
    return networkResponse;
  } catch (error) {
    const cachedResponse = await cache.match(request);
    
    if (cachedResponse) {
      return cachedResponse;
    }
    
    throw error;
  }
}

/**
 * Refresh cache in background without blocking response
 */
async function refreshCacheInBackground(request, cache) {
  try {
    const networkResponse = await fetch(request);
    
    if (networkResponse.ok) {
      // Add timestamp for expiration tracking
      const headers = new Headers(networkResponse.headers);
      headers.append('x-cached-at', Date.now().toString());
      
      const modifiedResponse = new Response(networkResponse.body, {
        status: networkResponse.status,
        statusText: networkResponse.statusText,
        headers: headers
      });
      
      cache.put(request, modifiedResponse);
      console.log('[Service Worker] Background cache refresh completed');
    }
  } catch (error) {
    console.log('[Service Worker] Background cache refresh failed:', error);
  }
}

// ============================================
// BACKGROUND SYNC
// ============================================

/**
 * Background Sync Event Handler
 * Queues API calls made while offline and retries when connection returns
 */
self.addEventListener('sync', (event) => {
  console.log('[Service Worker] Background sync triggered:', event.tag);
  
  if (event.tag === 'sync-api-calls') {
    event.waitUntil(syncApiCalls());
  }
});

/**
 * Sync queued API calls
 * Processes any pending requests stored in IndexedDB
 */
async function syncApiCalls() {
  // This would typically use IndexedDB to queue requests
  // For now, we'll trigger a client refresh to load fresh data
  console.log('[Service Worker] Processing queued API calls...');
  
  const clients = await self.clients.matchAll({ type: 'window' });
  clients.forEach(client => {
    client.postMessage({
      type: 'SYNC_COMPLETE',
      message: 'Background sync completed - fresh data available'
    });
  });
}

/**
 * Queue a request for background sync
 * Called when a fetch fails due to network issues
 */
async function queueForSync(request) {
  // Register for background sync if supported
  if ('sync' in self.registration) {
    try {
      await self.registration.sync.register('sync-api-calls');
      console.log('[Service Worker] Registered for background sync');
    } catch (error) {
      console.error('[Service Worker] Background sync registration failed:', error);
    }
  }
}

// ============================================
// PUSH NOTIFICATIONS
// ============================================

/**
 * Push Event Handler
 * Receives and displays push notifications from the server
 */
self.addEventListener('push', (event) => {
  console.log('[Service Worker] Push notification received:', event);
  
  let notificationData = {
    title: 'Nickberg Terminal',
    body: 'New financial alert received',
    icon: 'data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 192 192\'%3E%3Crect width=\'192\' height=\'192\' fill=\'%231a1d21\' rx=\'32\'/%3E%3Ctext x=\'96\' y=\'138\' font-size=\'130\' text-anchor=\'middle\'%3E%F0%9F%94%AE%3C/text%3E%3C/svg%3E',
    badge: 'data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 72 72\'%3E%3Crect width=\'72\' height=\'72\' fill=\'%23ff6b35\' rx=\'12\'/%3E%3Ctext x=\'36\' y=\'52\' font-size=\'48\' text-anchor=\'middle\'%3E%F0%9F%94%AE%3C/text%3E%3C/svg%3E',
    tag: 'nickberg-alert',
    requireInteraction: true,
    data: {
      url: '/mobile',
      type: 'alert'
    },
    actions: [
      {
        action: 'view',
        title: 'View',
        icon: 'data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 24 24\' fill=\'white\'%3E%3Cpath d=\'M12 4.5C7 4.5 2.73 7.61 1 12c1.73 4.39 6 7.5 11 7.5s9.27-3.11 11-7.5c-1.73-4.39-6-7.5-11-7.5zM12 17c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5zm0-8c-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3-1.34-3-3-3z\'/%3E%3C/svg%3E'
      },
      {
        action: 'dismiss',
        title: 'Dismiss',
        icon: 'data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 24 24\' fill=\'white\'%3E%3Cpath d=\'M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z\'/%3E%3C/svg%3E'
      }
    ]
  };
  
  // Parse push data if available
  if (event.data) {
    try {
      const data = event.data.json();
      notificationData = { ...notificationData, ...data };
    } catch (e) {
      notificationData.body = event.data.text();
    }
  }
  
  event.waitUntil(
    self.registration.showNotification(notificationData.title, {
      body: notificationData.body,
      icon: notificationData.icon,
      badge: notificationData.badge,
      tag: notificationData.tag,
      requireInteraction: notificationData.requireInteraction,
      data: notificationData.data,
      actions: notificationData.actions,
      vibrate: [200, 100, 200]
    })
  );
});

/**
 * Notification Click Handler
 * Handles user interactions with push notifications
 */
self.addEventListener('notificationclick', (event) => {
  console.log('[Service Worker] Notification clicked:', event);
  
  const notification = event.notification;
  const action = event.action;
  
  notification.close();
  
  if (action === 'dismiss') {
    // Just close the notification
    return;
  }
  
  // Default action or 'view' - open the app
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true })
      .then((clientList) => {
        const url = notification.data?.url || '/mobile';
        
        // Check if there's already a window open
        for (const client of clientList) {
          if (client.url.includes(self.registration.scope) && 'focus' in client) {
            return client.focus().then(() => {
              // Navigate to the specific URL
              if ('navigate' in client) {
                client.navigate(url);
              }
            });
          }
        }
        
        // No existing window - open a new one
        if (self.clients.openWindow) {
          return self.clients.openWindow(url);
        }
      })
  );
});

/**
 * Notification Close Handler
 * Tracks when notifications are dismissed without interaction
 */
self.addEventListener('notificationclose', (event) => {
  console.log('[Service Worker] Notification closed:', event);
});

// ============================================
// MESSAGE HANDLING (Client <-> Service Worker)
// ============================================

/**
 * Message Event Handler
 * Receives messages from the client pages
 */
self.addEventListener('message', (event) => {
  console.log('[Service Worker] Message received:', event.data);
  
  const { type, payload } = event.data;
  
  switch (type) {
    case 'SKIP_WAITING':
      // Force the waiting service worker to become active
      self.skipWaiting();
      break;
      
    case 'CHECK_UPDATE':
      // Trigger a cache update check
      checkForUpdates();
      break;
      
    case 'CLEAR_CACHE':
      // Clear all caches
      clearAllCaches();
      break;
      
    case 'GET_CACHE_INFO':
      // Send cache info back to client
      sendCacheInfo(event.source);
      break;
      
    default:
      console.log('[Service Worker] Unknown message type:', type);
  }
});

/**
 * Check for updates by clearing old caches and re-fetching
 */
async function checkForUpdates() {
  console.log('[Service Worker] Checking for updates...');
  
  // Clear API cache to get fresh data
  const apiCache = await caches.open(API_CACHE_NAME);
  const keys = await apiCache.keys();
  await Promise.all(keys.map(key => apiCache.delete(key)));
  
  // Notify clients
  const clients = await self.clients.matchAll({ type: 'window' });
  clients.forEach(client => {
    client.postMessage({
      type: 'UPDATE_AVAILABLE',
      message: 'Cache cleared - fresh data will be loaded'
    });
  });
}

/**
 * Clear all caches
 */
async function clearAllCaches() {
  const cacheNames = [STATIC_CACHE_NAME, API_CACHE_NAME, IMAGE_CACHE_NAME];
  
  for (const name of cacheNames) {
    await caches.delete(name);
  }
  
  console.log('[Service Worker] All caches cleared');
}

/**
 * Send cache information to client
 */
async function sendCacheInfo(client) {
  const cacheInfo = {
    static: await getCacheSize(STATIC_CACHE_NAME),
    api: await getCacheSize(API_CACHE_NAME),
    images: await getCacheSize(IMAGE_CACHE_NAME)
  };
  
  client.postMessage({
    type: 'CACHE_INFO',
    payload: cacheInfo
  });
}

/**
 * Get the number of items in a cache
 */
async function getCacheSize(cacheName) {
  try {
    const cache = await caches.open(cacheName);
    const keys = await cache.keys();
    return keys.length;
  } catch (e) {
    return 0;
  }
}

// ============================================
// PERIODIC BACKGROUND SYNC (Experimental)
// ============================================

/**
 * Periodic Background Sync Event Handler
 * Allows the app to periodically update data in the background
 * Note: This requires user permission and is not supported in all browsers
 */
self.addEventListener('periodicsync', (event) => {
  if (event.tag === 'update-market-data') {
    event.waitUntil(updateMarketDataInBackground());
  }
});

/**
 * Update market data in the background
 */
async function updateMarketDataInBackground() {
  console.log('[Service Worker] Periodic background sync triggered');
  
  try {
    // Fetch fresh market data
    const response = await fetch('/api/market-data');
    
    if (response.ok) {
      const cache = await caches.open(API_CACHE_NAME);
      
      const headers = new Headers(response.headers);
      headers.append('x-cached-at', Date.now().toString());
      
      const modifiedResponse = new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers: headers
      });
      
      await cache.put('/api/market-data', modifiedResponse);
      
      // Notify clients of update
      const clients = await self.clients.matchAll({ type: 'window' });
      clients.forEach(client => {
        client.postMessage({
          type: 'BACKGROUND_UPDATE',
          message: 'Market data updated in background'
        });
      });
    }
  } catch (error) {
    console.error('[Service Worker] Background update failed:', error);
  }
}

// ============================================
// ERROR HANDLING
// ============================================

/**
 * Global error handler
 */
self.addEventListener('error', (event) => {
  console.error('[Service Worker] Error:', event.message, event.filename, event.lineno);
});

/**
 * Unhandled rejection handler
 */
self.addEventListener('unhandledrejection', (event) => {
  console.error('[Service Worker] Unhandled promise rejection:', event.reason);
});

console.log('[Service Worker] Script loaded and waiting for events');
