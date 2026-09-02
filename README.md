# XAUUSD Setup Scanner Bot

Scans gold (XAUUSD) across your 3 personal analysis methods and pushes
alerts to Telegram, with a chart image marking entry/SL/TP and a
position-size suggestion scaled to your account balance.

## Status

- ✅ **Method 1 (Combined)** — real MSS (liquidity grab + body-close break),
  real STL/IDM/New STL Confirmation Point structure, OB confluence across
  Daily/4H/1H/15m (all 4 documented combos), entry-OB-aligns-with-HTF-OB check
- ✅ **Method 2 (Monthly-Daily-Hourly-5m)** — Simple MSS (valid-swing
  filtered), Fib-pullback STL/Confirmation Point structure (25%/37.5%/37.5%
  thresholds), OB/FVG entry
- ✅ **Method 3 (Liquidity + Structure)** — liquidity sweep detection, SAR
  strategy confluence, 5 confirmation models (MSS preferred, falls back
  through CISD/Unicorn Model/Turtle Soup/SCOB), entry-zone confluence
  (5m/15m/30m/1H), real SL (recent price extreme) and TP (opposite-side
  liquidity) with sanity checks
- ✅ **LuxAlgo Smart Money Concepts** — legitimately ported (open-source,
  CC BY-NC-SA 4.0), BOS/CHoCH, order blocks, EQH/EQL, FVG, premium/discount
  zones
- ✅ **Position sizing** — scales automatically with your account balance
  (see Configuration below)
- ✅ **Duplicate-alert prevention** — won't spam the same setup repeatedly
- ✅ **Error handling** — retries with backoff on yfinance/Telegram
  failures; one method failing doesn't block the others
- 🟡 **Guardeer (closed LuxAlgo indicator)** — not ported (licensed for
  TradingView use only, not reproduction) — relayed via TradingView
  webhook instead (see `webhook-receiver/`)

## Setup

1. **Create the GitHub repo:** push this folder to a new GitHub repository.

2. **Add your secrets:** in the repo, go to
   `Settings → Secrets and variables → Actions → New repository secret`
   and add:
   - `TELEGRAM_BOT_TOKEN` — your bot token from BotFather
   - `TELEGRAM_CHAT_ID` — your Telegram chat/user ID (message
     [@userinfobot](https://t.me/userinfobot) to get yours)

3. **Enable Actions:** check the repo's "Actions" tab and enable workflows
   if prompted (sometimes off by default on freshly pushed repos).

4. **That's it.** The workflow in `.github/workflows/run_bot.yml` runs every
   15 minutes automatically via GitHub Actions (free on public repos, and
   free up to a monthly minute quota on private repos). You can also trigger
   a manual run any time from the repo's "Actions" tab →
   "XAUUSD Setup Scanner" → "Run workflow".

## Configuration

**Account balance / risk %** — edit `config/account.json`:
```json
{
  "account_balance_usd": 1000,
  "risk_percent_per_trade": 2.0
}
```
Update `account_balance_usd` whenever your real balance changes (deposits,
withdrawals, or just to keep it current). Every future alert's suggested
lot size automatically scales with it — no code changes needed. Easiest
way to edit: open the file in GitHub's web UI, click the pencil icon,
change the number, commit.

## Guardeer webhook setup (optional)

If you want Guardeer's native alerts (BOS/CHoCH/EQH/EQL/FVG/OB
touch/zones) relayed to the same Telegram bot:

1. Free Cloudflare account → Workers & Pages → Create Worker
2. Paste in `webhook-receiver/worker.js`
3. Add the same `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` as Worker secrets
4. Deploy, copy the Worker URL
5. In TradingView, on a chart with Guardeer loaded, create an Alert on any
   of its `alertcondition()`s, and paste the Worker URL under
   Notifications → Webhook URL

Full details are in the comments at the top of `webhook-receiver/worker.js`.

## Local testing (optional, before pushing to GitHub)

```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN="your_token"
export TELEGRAM_CHAT_ID="your_chat_id"
python -m src.main
```

## Known limitations (read before trusting live signals)

- **Never run against real yfinance/Telegram data end-to-end** — all
  testing was against real historical OANDA CSVs (uploaded manually) and
  synthetic data, since the development sandbox had no live network
  access. The first real scheduled run is genuinely the first time this
  hits live yfinance and a live Telegram send.
- Confluence scenarios capture the core mechanism described, not
  necessarily every nuance of every described trading scenario.
- No live broker connection — position sizing uses a manually-updated
  balance figure, not your real-time account equity.
- **Recommended:** treat the first 1–2 weeks as paper-trading validation.
  Watch what it alerts, compare against your own manual analysis, before
  acting on any signal live.
