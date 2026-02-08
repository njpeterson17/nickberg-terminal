/**
 * Optimized Bluesky Feed Loader
 * 
 * Performance improvements:
 * - Lazy loading: Only loads feeds when scrolled into view
 * - Batched requests: Loads accounts in small batches (5 at a time)
 * - Caching: Caches feed data for 5 minutes
 * - Rate limiting: Delays between batches to prevent API overload
 * - Error handling: Gracefully handles failures
 */

class OptimizedBlueskyLoader {
      constructor(options = {}) {
                this.batchSize = options.batchSize || 5;
                this.batchDelay = options.batchDelay || 1000; // 1 second between batches
          this.cacheTimeout = options.cacheTimeout || 300000; // 5 minutes
          this.maxRetries = options.maxRetries || 2;

          this.cache = new Map();
                this.loadingQueue = [];
                this.isLoading = false;
                this.observer = null;

          // Accounts to load (same as original)
          this.accounts = [
                        'unusualwhales.bsky.social',
                        'spotgamma.bsky.social',
                        'ladebackk.bsky.social',
                        'jarvisflow.bsky.social',
                        'darkpoolmarkets.bsky.social',
                        'chriswhittall.bsky.social',
                        'strazza.bsky.social',
                        'carnage4life.bsky.social',
                        'ecb.bsky.social',
                        'quiverquant.bsky.social',
                        'federalreserve.gov',
                        'elerianm.bsky.social',
                        'claudia-sahm.bsky.social',
                        'josephpolitano.bsky.social',
                        'darioperkins.bsky.social',
                        'benzinga.bsky.social',
                        'marketwatch.bsky.social',
                        'morningbrew.bsky.social',
                        'theblock.bsky.social',
                        'coindesk.bsky.social',
                        'bloomberg.com',
                        'reuters.com',
                        'financialtimes.com',
                        'cnbc.com',
                        'wsj.com',
                        'stocktwits.bsky.social',
                        'markminervini.bsky.social',
                        'tradingview.bsky.social',
                        '0dte.bsky.social',
                        'cboe.bsky.social',
                        'brianferoldi.bsky.social',
                        'mindmathmoney.com',
                        'dkellercmt.bsky.social',
                        'martialchartsfx.bsky.social',
                        'intradaytrader.bsky.social',
                        'jamtrades.bsky.social',
                        'sentiment.bsky.social',
                        'fintwit.bsky.social',
                        'finchat.bsky.social',
                        'sentimentrader.bsky.social',
                        'topdowncharts.bsky.social',
                        'marketsentiment.bsky.social',
                        'hmeisler.bsky.social',
                        'sassal0x.bsky.social',
                        'dcinvestor.bsky.social',
                        'cryptocobain.bsky.social',
                        'calle.bsky.social',
                        'phenotype.dev',
                        'apoorv.xyz'
                    ];
      }

    /**
       * Initialize lazy loading with Intersection Observer
       */
    initLazyLoading(containerSelector) {
              const container = document.querySelector(containerSelector);
              if (!container) {
                            console.warn('Bluesky container not found:', containerSelector);
                            return;
              }

          // Only load when container is visible
          this.observer = new IntersectionObserver((entries) => {
                        entries.forEach(entry => {
                                          if (entry.isIntersecting && !this.isLoading) {
                                                                console.log('Bluesky section visible, starting load...');
                                                                this.loadAllFeeds();
                                                                this.observer.unobserve(entry.target); // Load only once
                                          }
                        });
          }, {
                        rootMargin: '200px' // Start loading 200px before visible
          });

          this.observer.observe(container);
              console.log('Lazy loading initialized for Bluesky feeds');
    }

    /**
       * Check if cached data is still valid
       */
    isCacheValid(handle) {
              const cached = this.cache.get(handle);
              if (!cached) return false;
              return (Date.now() - cached.timestamp) < this.cacheTimeout;
    }

    /**
       * Get cached feed or return null
       */
    getCachedFeed(handle) {
              if (this.isCacheValid(handle)) {
                            console.log(`Using cached data for ${handle}`);
                            return this.cache.get(handle).data;
              }
              return null;
    }

    /**
       * Cache feed data
       */
    cacheFeed(handle, data) {
              this.cache.set(handle, {
                            data: data,
                            timestamp: Date.now()
              });
    }

    /**
       * Resolve Bluesky handle to DID
       */
    async resolveHandle(handle, retryCount = 0) {
              const cached = this.getCachedFeed(`handle_${handle}`);
              if (cached) return cached;

          try {
                        const response = await fetch(
                                          `https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle?handle=${handle}`,
                          { signal: AbortSignal.timeout(5000) } // 5 second timeout
                                      );

                  if (!response.ok) {
                                    throw new Error(`HTTP ${response.status}`);
                  }

                  const data = await response.json();
                        this.cacheFeed(`handle_${handle}`, data.did);
                        return data.did;
          } catch (error) {
                        if (retryCount < this.maxRetries) {
                                          console.log(`Retrying ${handle} (attempt ${retryCount + 1}/${this.maxRetries})`);
                                          await this.delay(1000 * (retryCount + 1)); // Exponential backoff
                            return this.resolveHandle(handle, retryCount + 1);
                        }
                        console.error(`Failed to resolve handle ${handle}:`, error.message);
                        return null;
          }
    }

    /**
       * Fetch author feed from Bluesky
       */
    async fetchAuthorFeed(did, handle, retryCount = 0) {
              const cached = this.getCachedFeed(`feed_${did}`);
              if (cached) return cached;

          try {
                        const response = await fetch(
                                          `https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?actor=${did}&limit=5`,
                          { signal: AbortSignal.timeout(5000) }
                                      );

                  if (!response.ok) {
                                    throw new Error(`HTTP ${response.status}`);
                  }

                  const data = await response.json();
                        this.cacheFeed(`feed_${did}`, data);
                        return data;
          } catch (error) {
                        if (retryCount < this.maxRetries) {
                                          console.log(`Retrying feed for ${handle} (attempt ${retryCount + 1}/${this.maxRetries})`);
                                          await this.delay(1000 * (retryCount + 1));
                                          return this.fetchAuthorFeed(did, handle, retryCount + 1);
                        }
                        console.error(`Failed to fetch feed for ${handle}:`, error.message);
                        return null;
          }
    }

    /**
       * Load a single account's feed
       */
    async loadAccount(handle) {
              try {
                            const did = await this.resolveHandle(handle);
                            if (!did) return null;

                  const feed = await this.fetchAuthorFeed(did, handle);
                            if (!feed) return null;

                  return {
                                    handle: handle,
                                    did: did,
                                    feed: feed
                  };
              } catch (error) {
                            console.error(`Error loading account ${handle}:`, error);
                            return null;
              }
    }

    /**
       * Load a batch of accounts
       */
    async loadBatch(accounts) {
              console.log(`Loading batch of ${accounts.length} accounts...`);
              const promises = accounts.map(handle => this.loadAccount(handle));
              const results = await Promise.allSettled(promises);

          const successful = results
                  .filter(r => r.status === 'fulfilled' && r.value !== null)
                  .map(r => r.value);

          console.log(`Batch complete: ${successful.length}/${accounts.length} successful`);
              return successful;
    }

    /**
       * Delay utility
       */
    delay(ms) {
              return new Promise(resolve => setTimeout(resolve, ms));
    }

    /**
       * Load all feeds in batches
       */
    async loadAllFeeds(onBatchComplete = null, onComplete = null) {
              if (this.isLoading) {
                            console.log('Already loading feeds...');
                            return;
              }

          this.isLoading = true;
              console.log(`Starting optimized load of ${this.accounts.length} Bluesky accounts...`);
              console.log(`Batch size: ${this.batchSize}, Delay: ${this.batchDelay}ms`);

          const allResults = [];
              const batches = [];

          // Split accounts into batches
          for (let i = 0; i < this.accounts.length; i += this.batchSize) {
                        batches.push(this.accounts.slice(i, i + this.batchSize));
          }

          console.log(`Total batches: ${batches.length}`);

          // Load each batch with delay
          for (let i = 0; i < batches.length; i++) {
                        console.log(`Processing batch ${i + 1}/${batches.length}...`);

                  const batchResults = await this.loadBatch(batches[i]);
                        allResults.push(...batchResults);

                  // Callback for each batch
                  if (onBatchComplete) {
                                    onBatchComplete(batchResults, i + 1, batches.length);
                  }

                  // Delay between batches (except for last batch)
                  if (i < batches.length - 1) {
                                    await this.delay(this.batchDelay);
                  }
          }

          this.isLoading = false;
              console.log(`✓ Completed loading ${allResults.length}/${this.accounts.length} accounts`);

          // Final callback
          if (onComplete) {
                        onComplete(allResults);
          }

          return allResults;
    }

    /**
       * Clear cache
       */
    clearCache() {
              this.cache.clear();
              console.log('Bluesky cache cleared');
    }

    /**
       * Disconnect observer
       */
    disconnect() {
              if (this.observer) {
                            this.observer.disconnect();
              }
    }
}

// Export for use in dashboard.js
if (typeof module !== 'undefined' && module.exports) {
      module.exports = OptimizedBlueskyLoader;
}
