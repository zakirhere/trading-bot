from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Hardcoded risk caps. Per CLAUDE.md these are guardrails, not knobs —
# config can only make them tighter via overrides on top, never looser.
MAX_RISK_PER_TRADE_USD = 500
MAX_TOTAL_OPEN_RISK_USD = 2000
MAX_CONCURRENT_POSITIONS = 20
DAILY_LOSS_LIMIT_PCT = -2.0
NO_NEW_TRADES_BEFORE_CLOSE_MIN = 30

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = Path.home() / ".tradebot"
STATE_FILE = STATE_DIR / "state.json"

KILLSWITCH_HOST = "127.0.0.1"
KILLSWITCH_PORT = 8765


@dataclass(frozen=True)
class AlpacaConfig:
    key_id: str
    secret_key: str
    base_url: str
    is_live: bool
    mode: str


@dataclass(frozen=True)
class NotifyConfig:
    provider: str
    slack_webhook_url: str | None

    @property
    def enabled(self) -> bool:
        return self.provider == "slack" and bool(self.slack_webhook_url)


def _load_dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def load_alpaca_config(env_path: Path | None = None) -> AlpacaConfig:
    if env_path is None:
        env_path = PROJECT_ROOT / ".env"
    file_env = _load_dotenv(env_path)
    env = {**file_env, **os.environ}

    key_id = env.get("ALPACA_API_KEY_ID", "")
    secret = env.get("ALPACA_API_SECRET_KEY", "")
    base_url = env.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/")
    live_value = env.get("TRADEBOT_LIVE", "0")
    is_live = live_value == "1"
    confirm_live = env.get("TRADEBOT_CONFIRM_LIVE_ALPACA", "")

    if not key_id or not secret or "PASTE" in key_id or "PASTE" in secret:
        raise RuntimeError(f"Alpaca credentials missing or unset in {env_path}")

    # Hard fence: refuse mismatched live flag and base URL.
    if live_value not in {"0", "1"}:
        raise RuntimeError("TRADEBOT_LIVE must be exactly 0 or 1")
    if is_live and base_url != "https://api.alpaca.markets":
        raise RuntimeError("TRADEBOT_LIVE=1 requires ALPACA_BASE_URL=https://api.alpaca.markets")
    if is_live and confirm_live != "I_UNDERSTAND_THIS_USES_REAL_MONEY":
        raise RuntimeError("live Alpaca trading requires TRADEBOT_CONFIRM_LIVE_ALPACA")
    if not is_live and base_url != "https://paper-api.alpaca.markets":
        raise RuntimeError(f"TRADEBOT_LIVE=0 but base_url points at live ({base_url}) — refusing")

    mode = "live" if is_live else "paper"
    return AlpacaConfig(
        key_id=key_id,
        secret_key=secret,
        base_url=base_url,
        is_live=is_live,
        mode=mode,
    )


def load_notify_config(env_path: Path | None = None) -> NotifyConfig:
    if env_path is None:
        env_path = PROJECT_ROOT / ".env"
    file_env = _load_dotenv(env_path)
    env = {**file_env, **os.environ}

    provider = env.get("NOTIFY_PROVIDER", "").strip().lower()
    webhook = env.get("SLACK_WEBHOOK_URL", "").strip()

    if provider not in {"", "slack"}:
        raise RuntimeError(f"unsupported NOTIFY_PROVIDER={provider!r}")
    if webhook and not webhook.startswith("https://hooks.slack.com/"):
        raise RuntimeError("SLACK_WEBHOOK_URL must start with https://hooks.slack.com/")

    return NotifyConfig(provider=provider, slack_webhook_url=webhook or None)
