from __future__ import annotations

import hashlib
from datetime import date


def make_key(strategy: str, symbol: str, intent: dict, on_date: date | None = None) -> str:
    on_date = on_date or date.today()
    intent_str = "|".join(f"{k}={intent[k]}" for k in sorted(intent))
    intent_hash = hashlib.sha256(intent_str.encode()).hexdigest()[:8]
    return f"{strategy}_{symbol}_{on_date.isoformat()}_{intent_hash}"


def is_processed(key: str, processed_keys: list[str]) -> bool:
    return key in processed_keys
