from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Hardcoded risk caps. Per CLAUDE.md these are guardrails, not knobs —
# config can only make them tighter via overrides on top, never looser.
MAX_RISK_PER_TRADE_USD = 500
MAX_TOTAL_OPEN_RISK_USD = 2000
MAX_CONCURRENT_POSITIONS = 5
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
    is_live = env.get("TRADEBOT_LIVE", "0") == "1"

    if not key_id or not secret or "PASTE" in key_id or "PASTE" in secret:
        raise RuntimeError(f"Alpaca credentials missing or unset in {env_path}")

    # Hard fence: refuse mismatched live flag and base URL.
    if is_live and "paper" in base_url:
        raise RuntimeError("TRADEBOT_LIVE=1 but base_url points at paper — config inconsistent")
    if not is_live and "paper" not in base_url:
        raise RuntimeError(f"TRADEBOT_LIVE=0 but base_url points at live ({base_url}) — refusing")

    return AlpacaConfig(key_id=key_id, secret_key=secret, base_url=base_url, is_live=is_live)
