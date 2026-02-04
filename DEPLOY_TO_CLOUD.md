# Deploy Nickberg Terminal to the Cloud

This guide walks you through deploying the Nickberg Terminal to Render.com (free tier) so you can access it from anywhere on your phone.

## Option 1: Render.com (Recommended - Free Tier)

Render offers:
- **Free web services** (sleeps after 15 min inactivity)
- **Free background workers** (for scraper)
- **Free PostgreSQL or SQLite with persistent disk**
- **Custom domains** (optional)

### Step 1: Sign Up for Render

1. Go to https://render.com
2. Sign up with GitHub (easiest)
3. Verify your email

### Step 2: Push Code to GitHub

If your code isn't in GitHub yet:

```bash
cd /home/nick/nickberg-terminal

# Initialize git (if not already done)
git init
git add .
git commit -m "Initial commit for cloud deployment"

# Create GitHub repo and push
git remote add origin https://github.com/YOUR_USERNAME/nickberg-terminal.git
git push -u origin main
```

### Step 3: Deploy on Render

**Option A: Using render.yaml (Blueprints)**

1. In Render dashboard, click **"Blueprints"** → **"New Blueprint Instance"**
2. Connect your GitHub repo
3. Render will read the `render.yaml` file and create:
   - Web service (dashboard)
   - Background worker (scraper)
   - Persistent disk (database)
4. Click **"Apply"**

**Option B: Manual Setup**

1. Click **"New +"** → **"Web Service"**
2. Connect your GitHub repo
3. Configure:
   - **Name**: `nickberg-terminal`
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn web.app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120`
   - **Plan**: Free
4. Add **Disk** (for database persistence):
   - **Name**: `data`
   - **Mount Path**: `/opt/render/project/src/data`
   - **Size**: 1 GB
5. Add **Environment Variables**:
   - `FLASK_ENV`: `production`
   - `NICKBERG_DB_PATH`: `/opt/render/project/src/data/nickberg.db`
   - `SCRAPER_MODE`: `schedule`
6. Click **"Create Web Service"**

### Step 4: Create Background Scraper

1. Click **"New +"** → **"Background Worker"**
2. Use same GitHub repo
3. Configure:
   - **Name**: `nickberg-scraper`
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python src/main.py run`
   - **Plan**: Free
4. Add same disk:
   - **Name**: `data`
   - **Mount Path**: `/opt/render/project/src/data`
   - **Size**: 1 GB
5. Add environment variables:
   - `SCRAPER_MODE`: `continuous`
   - `SCRAPER_INTERVAL_SECONDS`: `900`
   - `NICKBERG_DB_PATH`: `/opt/render/project/src/data/nickberg.db`
6. Click **"Create Worker"**

### Step 5: Access Your Dashboard

After deployment (takes ~5 minutes), you'll get a URL like:
```
https://nickberg-terminal.onrender.com
```

**Open this on your phone!** 📱

---

## Option 2: Railway.app (Alternative)

Railway offers $5/month free credit (enough for small apps).

1. Sign up at https://railway.app
2. Click **"New Project"** → **"Deploy from GitHub repo"**
3. Select your repo
4. Railway auto-detects Python and creates service
5. Add **Volume** for database persistence:
   - Mount at `/app/data`
6. Add environment variables in **Variables** tab
7. Deploy!

---

## Option 3: Fly.io (Alternative)

Fly.io offers $5/month free credit.

```bash
# Install flyctl
curl -L https://fly.io/install.sh | sh

# Login
fly auth login

# Launch app
cd /home/nick/nickberg-terminal
fly launch

# Create persistent volume for database
fly volumes create data --size 1

# Deploy
fly deploy

# Open app
fly open
```

---

## Environment Variables Reference

Set these in your cloud provider's dashboard:

| Variable | Description | Example |
|----------|-------------|---------|
| `FLASK_ENV` | Environment mode | `production` |
| `NICKBERG_DB_PATH` | Database location | `/opt/render/project/src/data/nickberg.db` |
| `SCRAPER_MODE` | Scraper behavior | `continuous` or `schedule` |
| `SCRAPER_INTERVAL_SECONDS` | Scrape frequency | `900` (15 min) |
| `NICKBERG_API_KEY` | API security | `your-secret-key` |
| `NEWS_BOT_TELEGRAM_TOKEN` | Telegram bot token | `123456:ABC...` |
| `NEWS_BOT_TELEGRAM_CHAT_ID` | Telegram chat ID | `-100123456789` |
| `FMP_API_KEY` | Financial data API | `your-fmp-key` |
| `POLYGON_API_KEY` | News API key | `your-polygon-key` |
| `FRED_API_KEY` | Economic data API | `your-fred-key` |

---

## Troubleshooting

### Issue: Database not persisting

**Cause**: Disk not mounted correctly

**Fix**: Check mount path matches `NICKBERG_DB_PATH`

### Issue: Scraper not running

**Cause**: Background worker not configured

**Fix**: Ensure worker service is created separately from web service

### Issue: Free tier sleeps

**Cause**: Render free tier sleeps after 15 min inactivity

**Fix**: 
- Use a uptime monitor (like UptimeRobot) to ping every 10 minutes
- Or upgrade to paid tier ($7/month)

### Issue: Out of memory

**Cause**: SQLite + scraping too much data

**Fix**: 
- Reduce `retention_days` in config
- Clear old data: `DELETE FROM articles WHERE scraped_at < date('now', '-7 days')`

---

## Custom Domain (Optional)

### Render Custom Domain

1. In Render dashboard, go to your web service
2. Click **"Settings"** → **"Custom Domains"**
3. Add your domain (e.g., `news.yourdomain.com`)
4. Follow DNS instructions

### Cloudflare (Free SSL + CDN)

1. Add domain to Cloudflare
2. Create CNAME record pointing to Render URL
3. Enable "Always Use HTTPS"

---

## Monitoring Your Deployed App

### View Logs

**Render**: Dashboard → Service → Logs tab

**CLI**:
```bash
# Render
render logs --service nickberg-terminal

# Fly.io
fly logs
```

### Health Check Endpoint

Test if app is running:
```bash
curl https://your-app.onrender.com/health
```

### Database Stats

SSH into service (if supported) or use API:
```bash
curl https://your-app.onrender.com/api/stats
```

---

## Keeping Your App Awake (Free Tier)

Free tiers sleep after inactivity. To prevent:

### Option 1: UptimeRobot (Free)

1. Sign up at https://uptimerobot.com
2. Add monitor:
   - Type: HTTP(s)
   - URL: `https://your-app.onrender.com/health`
   - Interval: 5 minutes
3. Your app stays awake!

### Option 2: Cron Job

Add to your scraper to self-ping:
```python
import requests
import threading

def keep_alive():
    while True:
        try:
            requests.get('https://your-app.onrender.com/health', timeout=10)
        except:
            pass
        time.sleep(600)  # Every 10 minutes

threading.Thread(target=keep_alive, daemon=True).start()
```

---

## Security Checklist

Before going live:

- [ ] Set `NICKBERG_API_KEY` (don't leave empty)
- [ ] Remove any hardcoded API keys from code
- [ ] Use environment variables for all secrets
- [ ] Enable HTTPS (automatic on Render/Railway)
- [ ] Set up Telegram webhook with secret token
- [ ] Review `NEWS_BOT_TELEGRAM_CHAT_ID` is correct

---

## Next Steps

Once deployed:

1. **Test on your phone** 📱
2. **Set up Telegram alerts** (optional)
3. **Add custom domain** (optional)
4. **Monitor usage** - free tiers have limits
5. **Scale up** if needed (paid plans)

---

**Need help?** Check:
- Render docs: https://render.com/docs
- Railway docs: https://docs.railway.app
- Fly.io docs: https://fly.io/docs
