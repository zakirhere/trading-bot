from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from threading import Lock
from typing import Iterator

from . import config


@dataclass
class OrderRecord:
    idempotency_key: str
    broker_order_id: str | None
    symbol: str
    side: str
    qty: float
    submitted_at: str
    status: str


@dataclass
class State:
    halted: bool = False
    halt_reason: str | None = None
    processed_keys: list[str] = field(default_factory=list)
    orders: list[OrderRecord] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "State":
        return cls(
            halted=d.get("halted", False),
            halt_reason=d.get("halt_reason"),
            processed_keys=list(d.get("processed_keys", [])),
            orders=[OrderRecord(**o) for o in d.get("orders", [])],
        )


_lock = Lock()


def load() -> State:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not config.STATE_FILE.exists():
        return State()
    with config.STATE_FILE.open() as f:
        return State.from_dict(json.load(f))


def save(s: State) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=config.STATE_DIR, prefix=".state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(asdict(s), f, indent=2, default=str)
        os.replace(tmp, config.STATE_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@contextmanager
def transaction() -> Iterator[State]:
    with _lock:
        s = load()
        yield s
        save(s)
