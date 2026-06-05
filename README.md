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

The launchd job is also dry-run by default. A real paper order requires running
the daemon without `--dry-run`.

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
deploy/macos/install-launchd.sh
```

The deployed launchd job runs with `--dry-run --no-server` by default. Check it:

```bash
deploy/macos/status.sh
```

Remove it:

```bash
deploy/macos/uninstall-launchd.sh
```

For updates from this development machine, push to GitHub, then SSH to the Mac
mini and run:

```bash
git pull
deploy/macos/uninstall-launchd.sh
deploy/macos/install-launchd.sh
```
