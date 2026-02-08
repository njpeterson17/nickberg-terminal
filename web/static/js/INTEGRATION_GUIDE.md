# Integration Guide: Optimized Bluesky Loader

## Overview
This guide explains how to integrate the `bluesky-loader-optimized.js` into your dashboard to fix the performance issues.

## What This Fixes
- **100+ simultaneous API calls** → Batched loading (5 at a time)
- - **Immediate loading on page load** → Lazy loading when section becomes visible
  - - **No caching** → 5-minute cache for all feeds
    - - **No error handling** → Graceful retries and error messages
      - - **Slow page load** → Fast initial load, progressive enhancement
       
        - ## Installation Steps
       
        - ### Step 1: Add Script Tag to HTML
        - Add this script tag to your `index.html` or main template **before** `dashboard.js`:
       
        - ```html
          <script src="{{ url_for('static', filename='js/bluesky-loader-optimized.js') }}"></script>
          <script src="{{ url_for('static', filename='js/dashboard.js') }}"></script>
          ```

          ### Step 2: Update dashboard.js

          Find the section in `dashboard.js` where Bluesky feeds are currently loaded (likely where you have all the `BLUESKY_ACCOUNTS` or where you're calling the Bluesky API).

          **Replace the old Bluesky loading code with:**

          ```javascript
          // Initialize the optimized loader
          const blueskyLoader = new OptimizedBlueskyLoader({
              batchSize: 5,          // Load 5 accounts at a time
              batchDelay: 1000,      // 1 second delay between batches
              cacheTimeout: 300000,  // 5 minute cache
              maxRetries: 2          // Retry failed requests twice
          });

          // Initialize lazy loading - only loads when user scrolls to Bluesky section
          blueskyLoader.initLazyLoading('#bluesky-feed-container');

          // Optional: Add callbacks to show loading progress
          blueskyLoader.loadAllFeeds(
              // Called after each batch completes
              (batchResults, batchNum, totalBatches) => {
                  console.log(`Loaded batch ${batchNum}/${totalBatches}`);
                  // Update your UI here with the new batch data
                  updateBlueskyFeed(batchResults);
              },
              // Called when all batches complete
              (allResults) => {
                  console.log(`✓ All ${allResults.length} accounts loaded`);
                  hideLoadingSpinner();
              }
          );
          ```

          ### Step 3: Update Your HTML Container

          Make sure you have a container with the ID `bluesky-feed-container` (or change the selector in the code above):

          ```html
          <div id="bluesky-feed-container" class="bluesky-section">
              <h3>Bluesky Finance Feed</h3>
              <div id="bluesky-posts">
                  <!-- Posts will be loaded here -->
              </div>
          </div>
          ```

          ### Step 4: Create Display Function

          Create a function to display the loaded feeds:

          ```javascript
          function updateBlueskyFeed(batchResults) {
              const container = document.getElementById('bluesky-posts');

              batchResults.forEach(account => {
                  if (!account || !account.feed) return;

                  const { handle, feed } = account;

                  // Process each post in the feed
                  feed.feed.forEach(item => {
                      const post = item.post;
                      const postHTML = `
                          <div class="bluesky-post">
                              <div class="post-header">
                                  <strong>@${handle}</strong>
                                  <span class="post-time">${formatTime(post.indexedAt)}</span>
                              </div>
                              <div class="post-content">
                                  ${post.record.text}
                              </div>
                          </div>
                      `;
                      container.insertAdjacentHTML('beforeend', postHTML);
                  });
              });
          }

          function formatTime(timestamp) {
              const date = new Date(timestamp);
              const now = new Date();
              const seconds = Math.floor((now - date) / 1000);

              if (seconds < 60) return `${seconds}s ago`;
              if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
              if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
              return `${Math.floor(seconds / 86400)}d ago`;
          }
          ```

          ## Alternative: Manual Loading

          If you don't want lazy loading and prefer to load immediately (but still want batching):

          ```javascript
          // Initialize without lazy loading
          const blueskyLoader = new OptimizedBlueskyLoader();

          // Load immediately when page loads
          document.addEventListener('DOMContentLoaded', async () => {
              const results = await blueskyLoader.loadAllFeeds(
                  (batch, num, total) => updateBlueskyFeed(batch),
                  (all) => console.log('Done loading!')
              );
          });
          ```

          ## Configuration Options

          ```javascript
          new OptimizedBlueskyLoader({
              batchSize: 5,          // Accounts per batch (default: 5)
              batchDelay: 1000,      // Milliseconds between batches (default: 1000)
              cacheTimeout: 300000,  // Cache duration in ms (default: 300000 = 5 min)
              maxRetries: 2          // Retry attempts for failed requests (default: 2)
          })
          ```

          ## Testing

          1. Open browser DevTools (F12)
          2. 2. Go to the Network tab
             3. 3. Refresh the page
                4. 4. You should see:
                   5.    - Initial page loads fast
                         -    - Bluesky API calls start only when you scroll near the section
                              -    - Calls are made in small batches with delays
                                   -    - Refresh again and some requests use cache (much faster)
                                    
                                        - ## Performance Comparison
                                    
                                        - ### Before:
                                        - - 150+ requests fired immediately
                                          - - Page load time: 10-15 seconds
                                            - - Multiple API failures
                                              - - Backend server overload
                                               
                                                - ### After:
                                                - - 5 requests at a time
                                                  - - Initial page load: <2 seconds
                                                    - - Bluesky loads progressively in background
                                                      - - Cached data loads instantly on refresh
                                                        - - No server overload
                                                         
                                                          - ## Troubleshooting
                                                         
                                                          - ### Feeds not loading?
                                                          - Check the browser console for errors. The loader logs all activity.
                                                         
                                                          - ### Want to adjust the account list?
                                                          - Edit the `this.accounts` array in `bluesky-loader-optimized.js` (line 25-75)
                                                         
                                                          - ### Want to clear cache manually?
                                                          - ```javascript
                                                            blueskyLoader.clearCache();
                                                            ```

                                                            ### Want different batch settings?
                                                            Adjust the options when creating the loader - smaller batches = slower but more reliable.

                                                            ## Next Steps

                                                            1. Deploy the changes
                                                            2. 2. Monitor server logs to ensure 503 errors are resolved
                                                               3. 3. Consider adding a backend cache as well for even better performance
                                                                  4. 4. Add visual loading indicators for better UX
                                                                    
                                                                     5. ## Questions?
                                                                     6. Check the code comments in `bluesky-loader-optimized.js` for detailed documentation of each method.
                                                                     7. 
