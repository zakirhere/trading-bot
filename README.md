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
