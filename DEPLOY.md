# Trader-VIX — Deploy Guide

## Infrastructure
Runs on Railway. One service, two strategies (swing + 0DTE) in a single process.
Flask dashboard on the PORT env var (default 3005).

---

## First-time setup

### 1. Create Railway service
Link the `Trader-VIX` GitHub repo to a new Railway service.
Railway auto-deploys on every push to `main`.

### 2. Set environment variables in Railway dashboard
Copy all values from `.env.example`. Required before first deploy:
- `TASTYTRADE_USERNAME`, `TASTYTRADE_PASSWORD`, `TASTYTRADE_ACCOUNT_NUM`
- `TASTYTRADE_PAPER=true` (keep true until 90-day track record)
- `DASHBOARD_PASSWORD`, `SECRET_KEY`
- `RESEND_API_KEY`, `NOTIFY_EMAIL`

### 3. Tastytrade paper account
1. Create account at tastytrade.com
2. Enable paper trading at manage.tastytrade.com under Developer Tools
3. Request API access (approved immediately for paper accounts)
4. Get account number from the paper trading dashboard

### 4. OANDA (forex carry — optional, add later)
1. Create free practice account at oanda.com
2. Get API token from My Account > Manage API Access
3. Add OANDA_API_KEY, OANDA_ACCOUNT_ID, OANDA_PAPER=true to Railway env vars
4. Set CARRY_ENABLED=true when ready

### 5. Verify deployment
- Railway logs: should see "Trader-VIX starting" within 60 seconds
- `https://your-service.up.railway.app/health` should return `{"status": "ok"}`
- Visit dashboard URL, log in with DASHBOARD_PASSWORD

---

## Gladys deploy lock

The `/health` endpoint returns HTTP 503 when `deploy_locked=true`.
Gladys pre-deploy hook for the `Trader-VIX` repo:

```bash
HEALTH=$(curl -s -o /dev/null -w "%{http_code}" https://your-service.up.railway.app/health)
if [ "$HEALTH" = "503" ]; then
  echo "Deploy blocked: 0DTE position open during market hours"
  exit 1
fi
```

Manual rule: NEVER force-deploy between 9:30 AM and 4:15 PM ET on trading days.
Expiration Fridays: extend blackout to 4:45 PM ET.

---

## Go-live checklist (before TASTYTRADE_PAPER=false)

- [ ] 60+ trading days paper trading completed
- [ ] Sharpe > 0.8 over paper period
- [ ] Max drawdown < 20% over paper period
- [ ] 20+ swing trades + 30+ 0DTE trades logged and reviewed
- [ ] All exit conditions verified firing correctly
- [ ] Connectivity watchdog tested (email received on simulated outage)
- [ ] Contingency orders confirmed placed at broker on 0DTE open
- [ ] Decision logged in Notion Trader-VIX spec page

---

## Monitoring

- Railway logs: real-time
- `/health`: set up UptimeRobot free monitor (1-min ping, SMS alert)
- Morning brief email: 9:00 AM ET daily with 0DTE go/no-go
- Trade alerts: every open and close via Resend

---

## Rollback
Railway keeps all previous deploys. Click Rollback in the Railway dashboard.
