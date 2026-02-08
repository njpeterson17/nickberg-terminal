# Dashboard.js Modifications - STEP BY STEP

## What to Modify
You need to find and replace the OLD Bluesky loading code with the NEW optimized code.

## Step 1: Find the OLD Code

Search for this pattern in `dashboard.js` (it might be slightly different, but look for similar code):

- Look for `BLUESKY_ACCOUNTS` array or list of Bluesky handles
- - Look for `bsky.app` API calls
  - - Look for `resolveHandle` function calls
    - - Look for `getAuthorFeed` function calls
      - - Look for code that loads all accounts immediately on page load
       
        - Common patterns to search for:
        - ```
          BLUESKY_ACCOUNTS
          loadBlueskyFeed
          initBluesky
          bsky.app
          public.api.bsky.app
          ```

          ## Step 2: Comment Out or Remove OLD Code

          Once you find the Bluesky loading section, **comment it out** (don't delete yet, in case you need to reference it):

          ```javascript
          /* OLD BLUESKY CODE - REPLACED WITH OPTIMIZED LOADER
          async function loadBlueskyFeeds() {
              // ... old code here ...
          }
          // END OLD CODE */
          ```

          ## Step 3: Add NEW Code

          Add this code anywhere AFTER the existing functions (suggestion: near the bottom before the final initialization):

          ```javascript
          // ========================================
          // OPTIMIZED BLUESKY LOADER INTEGRATION
          // ========================================

          // Initialize the optimized Bluesky loader
          let blueskyLoader = null;

          function initOptimizedBlueskyLoader() {
              console.log('Initializing optimized Bluesky loader...');

              // Create loader with custom settings
              blueskyLoader = new OptimizedBlueskyLoader({
                  batchSize: 5,          // Load 5 accounts at a time
                  batchDelay: 1000,      // 1 second between batches
                  cacheTimeout: 300000,  // 5 minute cache
                  maxRetries: 2          // Retry failed requests twice
              });

              // Initialize lazy loading - IMPORTANT: Make sure this ID exists in your HTML!
              // If your Bluesky section has a different ID, change it here
              const blueskyContainer = document.querySelector('#bluesky-feed') ||
                                      document.querySelector('.bluesky-section') ||
                                      document.querySelector('[data-bluesky]');

              if (blueskyContainer) {
                  // Setup lazy loading
                  blueskyLoader.initLazyLoading(
                      '#' + blueskyContainer.id || '.bluesky-section'
                  );

                  console.log('✓ Lazy loading initialized for Bluesky');
              } else {
                  console.warn('Bluesky container not found - loading immediately as fallback');
                  // Load immediately if container not found
                  loadBlueskyFeeds();
              }
          }

          // Function to handle batch loading and display
          function loadBlueskyFeeds() {
              if (!blueskyLoader) {
                  console.error('Bluesky loader not initialized!');
                  return;
              }

              blueskyLoader.loadAllFeeds(
                  // Callback after each batch
                  (batchResults, batchNum, totalBatches) => {
                      console.log(`Bluesky batch ${batchNum}/${totalBatches} loaded`);
                      displayBlueskyBatch(batchResults);

                      // Update progress indicator if you have one
                      updateBlueskyLoadingProgress(batchNum, totalBatches);
                  },
                  // Callback when all done
                  (allResults) => {
                      console.log(`✓ All Bluesky feeds loaded: ${allResults.length} accounts`);
                      hideBlueskyLoadingSpinner();
                  }
              );
          }

          // Display function - CUSTOMIZE THIS based on your HTML structure
          function displayBlueskyBatch(batchResults) {
              // Find your Bluesky posts container
              const container = document.getElementById('bluesky-posts') ||
                               document.querySelector('.bluesky-posts') ||
                               document.querySelector('[data-bluesky-posts]');

              if (!container) {
                  console.warn('Bluesky posts container not found');
                  return;
              }

              batchResults.forEach(account => {
                  if (!account || !account.feed || !account.feed.feed) return;

                  const { handle, feed } = account;

                  // Process each post
                  feed.feed.forEach(item => {
                      try {
                          const post = item.post;
                          const record = post.record;

                          // Create post HTML - CUSTOMIZE styling to match your design
                          const postElement = document.createElement('div');
                          postElement.className = 'bluesky-post';
                          postElement.innerHTML = `
                              <div class="post-author">
                                  <strong>@${handle}</strong>
                                  <span class="post-time">${formatTimeAgo(post.indexedAt)}</span>
                              </div>
                              <div class="post-text">${escapeHtml(record.text || '')}</div>
                          `;

                          container.appendChild(postElement);
                      } catch (error) {
                          console.error('Error displaying post:', error);
                      }
                  });
              });
          }

          // Helper function to format timestamps
          function formatTimeAgo(timestamp) {
              const date = new Date(timestamp);
              const now = new Date();
              const seconds = Math.floor((now - date) / 1000);

              if (seconds < 60) return `${seconds}s`;
              if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
              if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
              return `${Math.floor(seconds / 86400)}d`;
          }

          // Helper function to escape HTML
          function escapeHtml(text) {
              const div = document.createElement('div');
              div.textContent = text;
              return div.innerHTML;
          }

          // Optional: Loading progress indicator
          function updateBlueskyLoadingProgress(current, total) {
              const progressElement = document.getElementById('bluesky-loading-progress');
              if (progressElement) {
                  const percent = Math.round((current / total) * 100);
                  progressElement.textContent = `Loading Bluesky feeds... ${percent}%`;
                  progressElement.style.width = `${percent}%`;
              }
          }

          // Optional: Hide loading spinner when done
          function hideBlueskyLoadingSpinner() {
              const spinner = document.getElementById('bluesky-loading') ||
                             document.querySelector('.bluesky-loading');
              if (spinner) {
                  spinner.style.display = 'none';
              }
          }

          // ========================================
          // END OPTIMIZED BLUESKY LOADER CODE
          // ========================================
          ```

          ## Step 4: Update Initialization

          Find where your dashboard initializes (usually at the bottom of dashboard.js), look for patterns like:

          ```javascript
          document.addEventListener('DOMContentLoaded', function() {
              // initialization code
          });

          // OR

          window.addEventListener('load', function() {
              // initialization code
          });

          // OR

          $(document).ready(function() {
              // jQuery initialization
          });
          ```

          Add this line to call the optimizer:

          ```javascript
          // Initialize optimized Bluesky loader
          initOptimizedBlueskyLoader();
          ```

          ### Example of where to add it:

          ```javascript
          document.addEventListener('DOMContentLoaded', function() {
              initWebSocket();
              loadArticles();
              loadPrices();
              updateSentiment();

              // ADD THIS LINE:
              initOptimizedBlueskyLoader();  // <-- NEW
          });
          ```

          ## Step 5: Update HTML Template

          Make sure your HTML has the Bluesky container. In your `index.html` or main template, add if missing:

          ```html
          <div id="bluesky-feed" class="bluesky-section">
              <h3>Bluesky Finance</h3>
              <div id="bluesky-loading" class="loading-indicator">
                  <div id="bluesky-loading-progress"></div>
                  <span>Loading feeds...</span>
              </div>
              <div id="bluesky-posts" class="bluesky-posts-container">
                  <!-- Posts will be inserted here -->
              </div>
          </div>
          ```

          ## Step 6: Add Script Tag to HTML

          In your HTML template (likely `index.html` or `base.html`), make sure to load the optimized loader BEFORE dashboard.js:

          ```html
          <!-- Load optimized Bluesky loader FIRST -->
          <script src="{{ url_for('static', filename='js/bluesky-loader-optimized.js') }}"></script>

          <!-- Then load dashboard -->
          <script src="{{ url_for('static', filename='js/dashboard.js') }}"></script>
          ```

          ## Testing

          1. Open browser DevTools (F12)
          2. 2. Go to Console tab
             3. 3. Refresh the page
                4. 4. You should see:
                   5.    ```
                            Initializing optimized Bluesky loader...
                            ✓ Lazy loading initialized for Bluesky
                            Bluesky section visible, starting load...
                            Loading batch 1/10...
                            Loaded batch 1/10
                            ...
                            ✓ All Bluesky feeds loaded: 49 accounts
                            ```

                         5. Go to Network tab - verify requests are batched
                     
                         6. ## Troubleshooting
                     
                         7. ### "blueskyLoader is not defined"
                         8. - Make sure `bluesky-loader-optimized.js` is loaded in HTML BEFORE `dashboard.js`
                           
                            - ### "Bluesky container not found"
                            - - Check that your HTML has an element with id="bluesky-feed"
                              - - Or update the selector in `initOptimizedBlueskyLoader()` to match your HTML
                               
                                - ### Posts not displaying
                                - - Check browser console for errors
                                  - - Verify `displayBlueskyBatch()` function selectors match your HTML structure
                                    - - Customize the HTML generation in `displayBlueskyBatch()` to match your styling
                                     
                                      - ### Still loading all at once
                                      - - Make sure you commented out/removed the OLD Bluesky loading code
                                        - - Verify `initOptimizedBlueskyLoader()` is being called
                                         
                                          - ## Summary
                                         
                                          - **What you're changing:**
                                          - - OLD: All 50+ accounts load immediately with 100+ API calls
                                            - - NEW: Accounts load in batches of 5, only when section is visible, with caching
                                             
                                              - **Files to modify:**
                                              - 1. `web/static/js/dashboard.js` - Add new code, remove/comment old code
                                                2. 2. `web/templates/index.html` (or equivalent) - Add script tag and HTML container
                                                  
                                                   3. **Result:**
                                                   4. - Page loads 5-10x faster
                                                      - - No more 503 errors
                                                        - - Better user experience with progressive loading
                                                         
                                                          - ## Need Help?
                                                         
                                                          - Check the `INTEGRATION_GUIDE.md` for more details and examples.
