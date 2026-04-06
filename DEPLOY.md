# Trader-VIX — Deploy Guide

## Infrastructure
Runs on Railway. One service, two strategies (swing + 0DTE) in a single process.
Flask dashboard on port 3005. Forex carry (OANDA) is a separate optional module.

---

## First-time setup

### 1. Create Railway service
Link the `Trader-VIX` GitHub repo to a new Railway service.
Railway auto-deploys on every push to `main`.

### 2. Set environment variables in Railway dashboard
Copy all values from `.env.example`. Required before first deploy:
- `TASTYTRADE_USERNAME`, `TASTYTRADE_PASSWORD`, `TASTYTRADE_ACCOUNT_NUM`
- `TASTYTRADE_PAPER=true` (keep true until 90-day track record established)
- `DASHBOARD_PASSWORD`, `SECRET_KEY`
- `RESEND_API_KEY`, `NOTIFY_EMAIL`

### 3. Tastytrade paper account
1. Create account at tastytrade.com
2. Enable paper trading at `manage.tastytrade.com` under Developer Tools
3. Request API access (approved immediately for paper accounts)
4. Get account number from paper trading dashboard
5. Add credentials to Railway env vars

### 4. Verify
- Railway logs should show "Trader-VIX starting" within 60 seconds
- `GET /health` returns `{"status": "ok"}`
- Dashboard loads at your Railway URL, login with `DASHBOARD_PASSWORD`

---

## Gladys deploy lock

The `/health` endpoint returns HTTP 503 when `deploy_locked=true`.
Add to Gladys pre-deploy hook for `Trader-VIX`:

```bash
HEALTH=$(curl -s -o /dev/null -w "%{http_code}" https://your-service.up.railway.app/health)
if [ "$HEALTH" = "503" ]; then
  echo "Deploy blocked: 0DTE position open during market hours"
  exit 1
fi
```

NEVER force-deploy between 9:30 AM and 4:15 PM ET on trading days.
On expiration Fridays, extend to 4:45 PM ET.

---

## Go-live checklist (before TASTYTRADE_PAPER=false)

- [ ] 60+ trading days paper trading completed
- [ ] Sharpe > 0.8 over paper period
- [ ] Max drawdown < 20% over paper period
- [ ] 20+ completed swing trades logged
- [ ] 30+ completed 0DTE trades logged
- [ ] All exit conditions verified (50% profit, 2x stop, time stop)
- [ ] Connectivity watchdog tested
- [ ] Contingency orders verified at broker on 0DTE entry
- [ ] Decision logged in Notion Trader-VIX spec

---

## Adding OANDA (forex carry)

1. Create free practice account at oanda.com
2. Get API token: My Account > Manage API Access
3. Add to Railway env vars: `OANDA_API_KEY`, `OANDA_ACCOUNT_ID`, `OANDA_PAPER=true`
4. Set `CARRY_ENABLED=true`
5. Set `CARRY_CAPITAL_PCT=0.20` (adjust SWING_CAPITAL_PCT to 0.60 accordingly)

---

## Monitoring

- Railway logs: real-time process output
- `/health`: poll via UptimeRobot (free, set up at uptimerobot.com)
- Morning brief email: 9:00 AM ET, includes 0DTE go/no-go signal
- Trade emails: every open and close via Resend

---

## Rollback
Railway keeps all previous deploys. Click Rollback in the Railway dashboard.
