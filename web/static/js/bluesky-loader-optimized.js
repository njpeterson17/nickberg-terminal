/**
 * BlueSky Loader Optimized
 * High-performance loader for BlueSky financial feeds
 * 
 * Features:
 * - Parallel fetching with concurrency control
 * - Intersection Observer lazy loading
 * - IndexedDB persistent caching
 * - Request deduplication
 * - Virtual scrolling support
 * - Exponential backoff retry
 */

(function() {
    'use strict';

    // ============================================
    // CONFIGURATION
    // ============================================
    const CONFIG = {
        CONCURRENCY: 5,              // Max parallel API calls
        BATCH_SIZE: 3,               // Accounts per batch
        POSTS_PER_ACCOUNT: 5,        // Posts to fetch per account
        CACHE_TTL: 2 * 60 * 1000,    // 2 minutes in-memory
        DB_CACHE_TTL: 10 * 60 * 1000, // 10 minutes IndexedDB
        RETRY_ATTEMPTS: 3,
        RETRY_DELAY: 1000,           // Initial retry delay (ms)
        VIRTUAL_SCROLL: true,        // Enable virtual scrolling
        LAZY_LOAD: true,             // Load only when visible
        PRIORITY_ACCOUNTS: [         // Load these first
            'unusualwhales.bsky.social',
            'spotgamma.bsky.social',
            'strazza.bsky.social'
        ]
    };

    // ============================================
    // STATE
    // ============================================
    const state = {
        posts: [],
        dids: new Map(),
        loading: false,
        abortControllers: new Map(),
        retryQueue: [],
        visiblePostIds: new Set(),
        intersectionObserver: null,
        db: null
    };

    // ============================================
    // INDEXEDDB CACHE
    // ============================================
    const DB_NAME = 'bluesky-cache-v1';
    const DB_STORE = 'posts';
    const DB_DID_STORE = 'dids';

    async function initDB() {
        if (state.db) return state.db;
        
        return new Promise((resolve, reject) => {
            const request = indexedDB.open(DB_NAME, 1);
            
            request.onerror = () => reject(request.error);
            request.onsuccess = () => {
                state.db = request.result;
                resolve(state.db);
            };
            
            request.onupgradeneeded = (event) => {
                const db = event.target.result;
                if (!db.objectStoreNames.contains(DB_STORE)) {
                    const postsStore = db.createObjectStore(DB_STORE, { keyPath: 'id' });
                    postsStore.createIndex('timestamp', 'timestamp', { unique: false });
                    postsStore.createIndex('handle', 'handle', { unique: false });
                }
                if (!db.objectStoreNames.contains(DB_DID_STORE)) {
                    db.createObjectStore(DB_DID_STORE, { keyPath: 'handle' });
                }
            };
        });
    }

    async function getCachedPosts(handle) {
        try {
            const db = await initDB();
            const tx = db.transaction(DB_STORE, 'readonly');
            const store = tx.objectStore(DB_STORE);
            const index = store.index('handle');
            
            const posts = await new Promise((resolve, reject) => {
                const request = index.getAll(handle);
                request.onsuccess = () => resolve(request.result || []);
                request.onerror = () => reject(request.error);
            });
            
            // Filter expired
            const now = Date.now();
            const valid = posts.filter(p => now - p.timestamp < CONFIG.DB_CACHE_TTL);
            
            // Clean up expired if we found any
            if (valid.length < posts.length) {
                cleanupExpiredPosts(handle);
            }
            
            return valid.map(p => p.data);
        } catch (e) {
            console.warn('Cache read failed:', e);
            return [];
        }
    }

    async function cachePosts(handle, posts) {
        try {
            const db = await initDB();
            const tx = db.transaction(DB_STORE, 'readwrite');
            const store = tx.objectStore(DB_STORE);
            
            const timestamp = Date.now();
            posts.forEach((post, index) => {
                store.put({
                    id: `${handle}_${post.cid || index}`,
                    handle: handle,
                    data: post,
                    timestamp: timestamp
                });
            });
            
            await new Promise((resolve, reject) => {
                tx.oncomplete = resolve;
                tx.onerror = () => reject(tx.error);
            });
        } catch (e) {
            console.warn('Cache write failed:', e);
        }
    }

    async function getCachedDID(handle) {
        try {
            const db = await initDB();
            const tx = db.transaction(DB_DID_STORE, 'readonly');
            const store = tx.objectStore(DB_DID_STORE);
            
            const result = await new Promise((resolve, reject) => {
                const request = store.get(handle);
                request.onsuccess = () => resolve(request.result);
                request.onerror = () => reject(request.error);
            });
            
            if (result && Date.now() - result.timestamp < CONFIG.DB_CACHE_TTL) {
                return result.did;
            }
            return null;
        } catch (e) {
            return null;
        }
    }

    async function cacheDID(handle, did) {
        try {
            const db = await initDB();
            const tx = db.transaction(DB_DID_STORE, 'readwrite');
            const store = tx.objectStore(DB_DID_STORE);
            
            await new Promise((resolve, reject) => {
                const request = store.put({
                    handle: handle,
                    did: did,
                    timestamp: Date.now()
                });
                request.onsuccess = resolve;
                request.onerror = () => reject(request.error);
            });
        } catch (e) {
            console.warn('DID cache failed:', e);
        }
    }

    async function cleanupExpiredPosts(handle) {
        // Cleanup in background
        setTimeout(async () => {
            try {
                const db = await initDB();
                const tx = db.transaction(DB_STORE, 'readwrite');
                const store = tx.objectStore(DB_STORE);
                const index = store.index('handle');
                
                const request = index.openCursor(IDBKeyRange.only(handle));
                const now = Date.now();
                
                request.onsuccess = (event) => {
                    const cursor = event.target.result;
                    if (cursor) {
                        if (now - cursor.value.timestamp > CONFIG.DB_CACHE_TTL) {
                            cursor.delete();
                        }
                        cursor.continue();
                    }
                };
            } catch (e) {
                // Silent fail
            }
        }, 100);
    }

    // ============================================
    // API FUNCTIONS
    // ============================================
    
    async function resolveHandle(handle, signal) {
        // Check memory cache
        if (state.dids.has(handle)) {
            return state.dids.get(handle);
        }
        
        // Check IndexedDB cache
        const cached = await getCachedDID(handle);
        if (cached) {
            state.dids.set(handle, cached);
            return cached;
        }
        
        // Fetch from API
        const response = await fetch(
            `https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle?handle=${encodeURIComponent(handle)}`,
            { signal }
        );
        
        if (!response.ok) throw new Error(`Failed to resolve handle: ${handle}`);
        
        const data = await response.json();
        state.dids.set(handle, data.did);
        cacheDID(handle, data.did); // Background cache
        
        return data.did;
    }

    async function fetchAuthorFeed(did, limit, signal) {
        const response = await fetch(
            `https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?actor=${encodeURIComponent(did)}&limit=${limit}`,
            { signal }
        );
        
        if (!response.ok) throw new Error(`Failed to fetch feed for: ${did}`);
        
        return response.json();
    }

    async function fetchAccountPosts(account, signal) {
        const { handle, name, display } = account;
        
        // Check cache first
        const cached = await getCachedPosts(handle);
        if (cached.length >= CONFIG.POSTS_PER_ACCOUNT) {
            return cached.slice(0, CONFIG.POSTS_PER_ACCOUNT);
        }
        
        // Fetch fresh data
        const did = await resolveHandle(handle, signal);
        const feedData = await fetchAuthorFeed(did, CONFIG.POSTS_PER_ACCOUNT, signal);
        
        const posts = feedData.feed.map(item => processPost(item, { handle, name, display }));
        
        // Cache the results
        cachePosts(handle, posts);
        
        return posts;
    }

    function processPost(item, account) {
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
    }

    // ============================================
    // BATCH PROCESSING WITH CONCURRENCY
    // ============================================
    
    async function fetchWithRetry(account, attempt = 1) {
        const controller = new AbortController();
        state.abortControllers.set(account.handle, controller);
        
        try {
            const posts = await fetchAccountPosts(account, controller.signal);
            state.abortControllers.delete(account.handle);
            return { success: true, account, posts };
        } catch (error) {
            state.abortControllers.delete(account.handle);
            
            // Check if aborted
            if (error.name === 'AbortError') {
                return { success: false, account, error, aborted: true };
            }
            
            // Retry logic
            if (attempt < CONFIG.RETRY_ATTEMPTS) {
                const delay = CONFIG.RETRY_DELAY * Math.pow(2, attempt - 1);
                await sleep(delay);
                return fetchWithRetry(account, attempt + 1);
            }
            
            return { success: false, account, error };
        }
    }

    function sleep(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    async function processBatch(accounts, onProgress) {
        const results = await Promise.allSettled(
            accounts.map(account => fetchWithRetry(account))
        );
        
        const posts = [];
        const errors = [];
        
        results.forEach((result, index) => {
            if (result.status === 'fulfilled') {
                const data = result.value;
                if (data.success) {
                    posts.push(...data.posts);
                } else if (!data.aborted) {
                    errors.push({ account: data.account, error: data.error });
                }
            } else {
                errors.push({ account: accounts[index], error: result.reason });
            }
        });
        
        if (onProgress) {
            onProgress(posts, errors, accounts.length);
        }
        
        return { posts, errors };
    }

    async function* fetchBatches(accounts, onProgress) {
        // Split into priority and regular accounts
        const priority = accounts.filter(a => CONFIG.PRIORITY_ACCOUNTS.includes(a.handle));
        const regular = accounts.filter(a => !CONFIG.PRIORITY_ACCOUNTS.includes(a.handle));
        
        // Process priority accounts first
        if (priority.length > 0) {
            console.log(`[BlueSky] Loading ${priority.length} priority accounts...`);
            for (let i = 0; i < priority.length; i += CONFIG.CONCURRENCY) {
                const batch = priority.slice(i, i + CONFIG.CONCURRENCY);
                yield await processBatch(batch, onProgress);
            }
        }
        
        // Process regular accounts in batches
        console.log(`[BlueSky] Loading ${regular.length} regular accounts...`);
        for (let i = 0; i < regular.length; i += CONFIG.CONCURRENCY) {
            const batch = regular.slice(i, i + CONFIG.CONCURRENCY);
            yield await processBatch(batch, onProgress);
        }
    }

    // ============================================
    // VIRTUAL SCROLLING & LAZY LOADING
    // ============================================
    
    function createIntersectionObserver(container, onVisible) {
        if (!CONFIG.LAZY_LOAD) return null;
        
        const options = {
            root: container,
            rootMargin: '100px', // Load 100px before visible
            threshold: 0.1
        };
        
        return new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                const postId = entry.target.dataset.postId;
                if (entry.isIntersecting) {
                    state.visiblePostIds.add(postId);
                    onVisible(entry.target, true);
                } else {
                    state.visiblePostIds.delete(postId);
                    onVisible(entry.target, false);
                }
            });
        }, options);
    }

    // ============================================
    // RENDER FUNCTIONS
    // ============================================
    
    function createPostElement(post, index) {
        const div = document.createElement('div');
        div.className = 'bluesky-post-item';
        div.dataset.postId = post.cid || index;
        div.dataset.index = index;
        
        // Use template for performance
        div.innerHTML = `
            <div class="bluesky-post-content">
                <div class="bluesky-post-header">
                    <img class="bluesky-avatar lazy" data-src="${post.author.avatar || ''}" alt="">
                    <div class="bluesky-author-info">
                        <span class="bluesky-display-name">${escapeHtml(post.author.displayName)}</span>
                        <span class="bluesky-handle">@${escapeHtml(post.author.handle)}</span>
                    </div>
                    <span class="bluesky-time">${formatTime(post.indexedAt)}</span>
                </div>
                <div class="bluesky-post-text">${linkifyText(escapeHtml(post.text))}</div>
                ${renderEmbed(post.embed)}
                <div class="bluesky-post-stats">
                    <span><i class="fas fa-comment"></i> ${post.replyCount}</span>
                    <span><i class="fas fa-retweet"></i> ${post.repostCount}</span>
                    <span><i class="fas fa-heart"></i> ${post.likeCount}</span>
                </div>
            </div>
        `;
        
        // Add click handler
        div.addEventListener('click', () => {
            window.open(getBlueskyPostUrl(post.uri, post.author.handle), '_blank');
        });
        
        return div;
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    function linkifyText(text) {
        // URLs
        text = text.replace(/https?:\/\/[^\s]+/g, '<a href="$&" target="_blank" rel="noopener">$&</a>');
        // Mentions
        text = text.replace(/@([a-zA-Z0-9_.-]+)/g, '<a href="https://bsky.app/profile/$1" target="_blank">@$1</a>');
        // Hashtags
        text = text.replace(/#([a-zA-Z0-9_]+)/g, '<a href="https://bsky.app/search?q=%23$1" target="_blank">#$1</a>');
        return text;
    }

    function formatTime(timestamp) {
        const date = new Date(timestamp);
        const now = new Date();
        const diff = Math.floor((now - date) / 1000);
        
        if (diff < 60) return 'now';
        if (diff < 3600) return `${Math.floor(diff / 60)}m`;
        if (diff < 86400) return `${Math.floor(diff / 3600)}h`;
        return `${Math.floor(diff / 86400)}d`;
    }

    function renderEmbed(embed) {
        if (!embed) return '';
        
        // Images
        if (embed.$type === 'app.bsky.embed.images#view' && embed.images) {
            const images = embed.images.slice(0, 4).map(img => 
                `<img class="bluesky-embed-image lazy" data-src="${img.thumb}" alt="${escapeHtml(img.alt || '')}">`
            ).join('');
            return `<div class="bluesky-embed-images">${images}</div>`;
        }
        
        // External link card
        if (embed.$type === 'app.bsky.embed.external#view' && embed.external) {
            const ext = embed.external;
            return `
                <div class="bluesky-embed-external">
                    ${ext.thumb ? `<img class="lazy" data-src="${ext.thumb}" alt="">` : ''}
                    <div class="external-content">
                        <div class="external-title">${escapeHtml(ext.title)}</div>
                        <div class="external-desc">${escapeHtml(ext.description)}</div>
                    </div>
                </div>
            `;
        }
        
        return '';
    }

    function getBlueskyPostUrl(uri, handle) {
        const match = uri.match(/at:\/\/did:plc:([^/]+)\/app\.bsky\.feed\.post\/(.+)/);
        if (match) {
            return `https://bsky.app/profile/${handle}/post/${match[2]}`;
        }
        return `https://bsky.app/profile/${handle}`;
    }

    // ============================================
    // LAZY IMAGE LOADING
    // ============================================
    
    function initLazyImages(container) {
        const images = container.querySelectorAll('img.lazy[data-src]');
        
        const imgObserver = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    const img = entry.target;
                    img.src = img.dataset.src;
                    img.classList.remove('lazy');
                    imgObserver.unobserve(img);
                }
            });
        }, { rootMargin: '50px' });
        
        images.forEach(img => imgObserver.observe(img));
    }

    // ============================================
    // MAIN LOADER CLASS
    // ============================================
    
    class BlueskyLoader {
        constructor(options = {}) {
            this.options = { ...CONFIG, ...options };
            this.posts = [];
            this.container = null;
            this.onProgress = null;
            this.onComplete = null;
            this.onError = null;
            this.observer = null;
        }

        async load(accounts, container, callbacks = {}) {
            const startTime = performance.now();
            
            this.container = container;
            this.onProgress = callbacks.onProgress;
            this.onComplete = callbacks.onComplete;
            this.onError = callbacks.onError;
            
            // Show loading state
            container.innerHTML = '<div class="bluesky-loading"><i class="fas fa-spinner fa-spin"></i> Loading posts...</div>';
            
            state.loading = true;
            const allPosts = [];
            const allErrors = [];
            
            try {
                // Process batches with streaming updates
                for await (const batch of fetchBatches(accounts, (posts, errors, count) => {
                    allPosts.push(...posts);
                    allErrors.push(...errors);
                    
                    // Sort by date
                    allPosts.sort((a, b) => new Date(b.indexedAt) - new Date(a.indexedAt));
                    
                    // Update UI progressively
                    if (this.onProgress) {
                        this.onProgress(allPosts, errors, count);
                    }
                    
                    // Render initial batch quickly
                    if (allPosts.length >= 20 && !this.initialRender) {
                        this.initialRender = true;
                        this.renderPosts(allPosts.slice(0, 50), container);
                    }
                })) {
                    // Batch processed
                }
                
                // Final render with all posts
                this.posts = allPosts.slice(0, 120);
                this.renderPosts(this.posts, container);
                
                const duration = performance.now() - startTime;
                console.log(`[BlueSky] Loaded ${this.posts.length} posts in ${duration.toFixed(0)}ms`);
                
                if (this.onComplete) {
                    this.onComplete(this.posts, allErrors);
                }
                
            } catch (error) {
                console.error('[BlueSky] Load failed:', error);
                if (this.onError) {
                    this.onError(error);
                }
                container.innerHTML = `
                    <div class="bluesky-error">
                        <i class="fas fa-exclamation-triangle"></i>
                        <span>Failed to load posts. <button onclick="location.reload()">Retry</button></span>
                    </div>
                `;
            } finally {
                state.loading = false;
            }
            
            return { posts: this.posts, errors: allErrors };
        }

        renderPosts(posts, container) {
            // Clear existing
            container.innerHTML = '';
            
            // Create fragment for batch DOM insert
            const fragment = document.createDocumentFragment();
            
            posts.forEach((post, index) => {
                const el = createPostElement(post, index);
                fragment.appendChild(el);
            });
            
            container.appendChild(fragment);
            
            // Initialize lazy loading
            initLazyImages(container);
            
            // Setup intersection observer for virtual scrolling
            if (CONFIG.VIRTUAL_SCROLL) {
                this.observer = createIntersectionObserver(container, (el, isVisible) => {
                    el.classList.toggle('visible', isVisible);
                });
                
                // Observe all posts
                container.querySelectorAll('.bluesky-post-item').forEach(el => {
                    this.observer.observe(el);
                });
            }
        }

        destroy() {
            // Abort all pending requests
            state.abortControllers.forEach(controller => controller.abort());
            state.abortControllers.clear();
            
            // Disconnect observer
            if (this.observer) {
                this.observer.disconnect();
            }
            
            // Clear state
            this.posts = [];
            state.loading = false;
        }

        // Force refresh
        async refresh(accounts) {
            // Clear caches
            state.dids.clear();
            state.posts = [];
            
            // Reload
            return this.load(accounts, this.container, {
                onProgress: this.onProgress,
                onComplete: this.onComplete,
                onError: this.onError
            });
        }
    }

    // ============================================
    // PUBLIC API
    // ============================================
    
    // Expose to global scope
    window.BlueskyLoader = BlueskyLoader;
    window.BlueskyLoaderConfig = CONFIG;
    
    // Auto-initialize if data is available
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            // Loader is ready but needs explicit init
            console.log('[BlueSky] Optimized loader ready');
        });
    }

})();
