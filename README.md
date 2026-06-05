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
