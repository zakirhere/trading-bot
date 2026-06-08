# trading-bot

Personal trading bot scaffold. Defaults are intentionally conservative: paper
Alpaca endpoint, local state, hard risk caps, and explicit live opt-in only.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Put Alpaca paper credentials in `.env`. Do not commit `.env`.

## Safe Checks

Check local state:

```bash
.venv/bin/python -m tradebot.daemon --status
```

Check Alpaca connectivity without running a signal or placing an order:

```bash
.venv/bin/python -m tradebot.daemon --account-check
```

Rehearse the signal without submitting an order:

```bash
.venv/bin/python -m tradebot.daemon --dry-run --no-server
```

Run strategy backtests without placing orders:

```bash
.venv/bin/python -m tradebot.daemon --backtest-spy-credit 2026-06-05
.venv/bin/python -m tradebot.daemon --backtest-spy-income-credit 2026-06-05
```

The SPY income-credit backtest scans 5 total daily entries, checks both call and
put credit spreads, targets roughly $0.60 credit on $1-wide spreads, prevents
duplicate expiry/strike combinations, and marks winners closed at 50% profit.

## Alpaca Data Feeds

The paper/basic Alpaca data plan has entitlement limits. Live automation must
request:

```text
stock bars: feed=iex
latest option quotes: feed=indicative
```

Historical option bars do not accept a `feed` query parameter. Current/future
OPRA-style option bars without the right subscription return `403 Forbidden`;
option bars with an unexpected feed parameter return `400 Bad Request`. For
live paper selection, use latest indicative option quotes instead.

For multi-leg option orders, Alpaca uses signed net prices:

```text
debit limit: positive limit_price
credit limit: negative limit_price
```

Example: a target `$0.58` credit spread must be submitted as
`limit_price="-0.58"`. Submitting `"+0.58"` is interpreted as a debit-side
limit and can fill for much less credit than intended.

The launchd job is also dry-run by default. A real paper order requires running
the daemon without `--dry-run`.

## Persistent Service

The preferred local workflow is one persistent service with a durable SQLite
queue instead of one launchd job per trade.

Run it manually:

```bash
.venv/bin/python -m tradebot.daemon --serve
```

Open the dashboard:

```text
http://127.0.0.1:8787
```

The dashboard requires a token. If `DASHBOARD_TOKEN` is not set in `.env`, the
service generates one locally:

```bash
cat ~/.tradebot/dashboard_token
```

You can also set your own token in `.env`:

```bash
DASHBOARD_TOKEN=replace-with-a-long-random-token
```

Queue a stock market buy through the API:

```bash
curl -X POST http://127.0.0.1:8787/api/trade-requests \
  -H "Authorization: Bearer $(cat ~/.tradebot/dashboard_token)" \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"AAPL","qty":1,"run_at":"2026-06-05T12:00:00-07:00"}'
```

Queued requests are stored at `~/.tradebot/tradebot.sqlite`. The worker polls
the queue, applies the same paper/live config and risk gates, then submits due
orders.

## Slack Notifications

Slack alerts use an incoming webhook URL stored only in `.env`. Per Slack's
incoming webhook docs, the webhook URL accepts a JSON payload with message text
and posts to the channel chosen when the webhook is created.

Create a Slack app in your personal workspace, enable Incoming Webhooks, add a
webhook to a private channel such as `#tradebot-alerts`, then set:

```bash
NOTIFY_PROVIDER=slack
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
```

Never commit the webhook URL. Notifications are skipped when these values are
unset.

## Mac Mini Deployment

Use the Mac mini as the always-on runner and keep this development machine as
the place where code changes are made.

On the Mac mini:

```bash
git clone git@github.com-zakirhere:zakirhere/trading-bot.git
cd trading-bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with Alpaca paper credentials, then verify:

```bash
.venv/bin/python -m tradebot.daemon --account-check
.venv/bin/python -m tradebot.daemon --dry-run --no-server
```

Install the Mac mini launchd job:

```bash
deploy/macos/install-service.sh
```

The deployed service starts at login and keeps running. Check it:

```bash
deploy/macos/status.sh
```

Remove it:

```bash
deploy/macos/uninstall-launchd.sh
deploy/macos/uninstall-service.sh
```

For updates from this development machine, push to GitHub, then SSH to the Mac
mini and run:

```bash
git pull
deploy/macos/uninstall-service.sh
deploy/macos/install-service.sh
```
