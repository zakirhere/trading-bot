from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import config

NEVER_TRADE_OPTION_UNDERLYINGS = {"AAPL"}


STATUS_QUEUED = "queued"
STATUS_SUBMITTED = "submitted"
STATUS_FILLED = "filled"
STATUS_DRY_RUN = "dry_run"
STATUS_BLOCKED = "blocked"
STATUS_ERROR = "error"


@dataclass(frozen=True)
class TradeRequest:
    id: int
    kind: str
    symbol: str
    qty: float
    side: str
    order_type: str
    run_at: str | None
    status: str
    dry_run: bool
    broker_order_id: str | None
    client_order_id: str | None
    reason: str | None
    payload: dict[str, Any]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class StrategyEvent:
    id: int
    strategy: str
    symbol: str
    event_type: str
    payload: dict[str, Any]
    created_at: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or config.DB_FILE
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trade_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            symbol TEXT NOT NULL,
            qty REAL NOT NULL,
            side TEXT NOT NULL,
            order_type TEXT NOT NULL,
            run_at TEXT,
            status TEXT NOT NULL,
            dry_run INTEGER NOT NULL DEFAULT 0,
            broker_order_id TEXT,
            client_order_id TEXT,
            reason TEXT,
            payload TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_trade_requests_status_run_at
        ON trade_requests(status, run_at)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS strategy_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy TEXT NOT NULL,
            symbol TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_strategy_events_strategy_created
        ON strategy_events(strategy, created_at)
        """
    )
    conn.commit()


def _row_to_request(row: sqlite3.Row) -> TradeRequest:
    return TradeRequest(
        id=int(row["id"]),
        kind=row["kind"],
        symbol=row["symbol"],
        qty=float(row["qty"]),
        side=row["side"],
        order_type=row["order_type"],
        run_at=row["run_at"],
        status=row["status"],
        dry_run=bool(row["dry_run"]),
        broker_order_id=row["broker_order_id"],
        client_order_id=row["client_order_id"],
        reason=row["reason"],
        payload=json.loads(row["payload"] or "{}"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_strategy_event(row: sqlite3.Row) -> StrategyEvent:
    return StrategyEvent(
        id=int(row["id"]),
        strategy=row["strategy"],
        symbol=row["symbol"],
        event_type=row["event_type"],
        payload=json.loads(row["payload"] or "{}"),
        created_at=row["created_at"],
    )


def create_stock_market_buy(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    qty: float,
    run_at: str | None = None,
    dry_run: bool = False,
) -> TradeRequest:
    now = utc_now()
    cur = conn.execute(
        """
        INSERT INTO trade_requests (
            kind, symbol, qty, side, order_type, run_at, status, dry_run,
            payload, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "stock_market_buy",
            symbol.upper(),
            qty,
            "buy",
            "market",
            run_at,
            STATUS_QUEUED,
            int(dry_run),
            "{}",
            now,
            now,
        ),
    )
    conn.commit()
    return get(conn, int(cur.lastrowid))


def create_option_spread_open(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    qty: int,
    side: str,
    limit_credit: float,
    run_at: str | None = None,
    dry_run: bool = False,
    payload: dict[str, Any],
) -> TradeRequest:
    if symbol.upper() in NEVER_TRADE_OPTION_UNDERLYINGS:
        raise ValueError(f"{symbol.upper()} options are blocked by NEVER_TRADE_OPTION_UNDERLYINGS")
    now = utc_now()
    cur = conn.execute(
        """
        INSERT INTO trade_requests (
            kind, symbol, qty, side, order_type, run_at, status, dry_run,
            payload, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "option_spread_open",
            symbol.upper(),
            qty,
            side,
            "limit_credit",
            run_at,
            STATUS_QUEUED,
            int(dry_run),
            json.dumps({**payload, "limit_credit": limit_credit}, sort_keys=True),
            now,
            now,
        ),
    )
    conn.commit()
    return get(conn, int(cur.lastrowid))


def get(conn: sqlite3.Connection, request_id: int) -> TradeRequest:
    row = conn.execute(
        "SELECT * FROM trade_requests WHERE id = ?", (request_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"trade request {request_id} not found")
    return _row_to_request(row)


def list_requests(conn: sqlite3.Connection, *, limit: int = 100) -> list[TradeRequest]:
    rows = conn.execute(
        """
        SELECT * FROM trade_requests
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_row_to_request(row) for row in rows]


def list_requests_by_status(
    conn: sqlite3.Connection,
    *,
    status: str,
    limit: int = 100,
) -> list[TradeRequest]:
    rows = conn.execute(
        """
        SELECT * FROM trade_requests
        WHERE status = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (status, limit),
    ).fetchall()
    return [_row_to_request(row) for row in rows]


def list_strategy_spread_requests(
    conn: sqlite3.Connection,
    *,
    strategy: str | None = None,
    limit: int = 100,
) -> list[TradeRequest]:
    params: list[Any] = []
    where = ["kind = 'option_spread_open'"]
    if strategy is not None:
        where.append("json_extract(payload, '$.strategy') = ?")
        params.append(strategy)
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT * FROM trade_requests
        WHERE {' AND '.join(where)}
        ORDER BY id DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [_row_to_request(row) for row in rows]


def due_requests(conn: sqlite3.Connection, *, now: str | None = None) -> list[TradeRequest]:
    now = now or utc_now()
    rows = conn.execute(
        """
        SELECT * FROM trade_requests
        WHERE status = ?
          AND (run_at IS NULL OR run_at <= ?)
        ORDER BY COALESCE(run_at, created_at), id
        """,
        (STATUS_QUEUED, now),
    ).fetchall()
    return [_row_to_request(row) for row in rows]


def update_status(
    conn: sqlite3.Connection,
    request_id: int,
    *,
    status: str,
    broker_order_id: str | None = None,
    client_order_id: str | None = None,
    reason: str | None = None,
) -> TradeRequest:
    conn.execute(
        """
        UPDATE trade_requests
        SET status = ?,
            broker_order_id = COALESCE(?, broker_order_id),
            client_order_id = COALESCE(?, client_order_id),
            reason = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (status, broker_order_id, client_order_id, reason, utc_now(), request_id),
    )
    conn.commit()
    return get(conn, request_id)


def create_strategy_event(
    conn: sqlite3.Connection,
    *,
    strategy: str,
    symbol: str,
    event_type: str,
    payload: dict[str, Any],
) -> StrategyEvent:
    now = utc_now()
    cur = conn.execute(
        """
        INSERT INTO strategy_events (
            strategy, symbol, event_type, payload, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (strategy, symbol.upper(), event_type, json.dumps(payload, sort_keys=True), now),
    )
    conn.commit()
    return get_strategy_event(conn, int(cur.lastrowid))


def get_strategy_event(conn: sqlite3.Connection, event_id: int) -> StrategyEvent:
    row = conn.execute(
        "SELECT * FROM strategy_events WHERE id = ?", (event_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"strategy event {event_id} not found")
    return _row_to_strategy_event(row)


def list_strategy_events(
    conn: sqlite3.Connection,
    *,
    strategy: str,
    since: str,
    limit: int = 100,
) -> list[StrategyEvent]:
    rows = conn.execute(
        """
        SELECT * FROM strategy_events
        WHERE strategy = ?
          AND created_at >= ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (strategy, since, limit),
    ).fetchall()
    return [_row_to_strategy_event(row) for row in rows]


def as_dict(req: TradeRequest) -> dict[str, Any]:
    return {
        "id": req.id,
        "kind": req.kind,
        "symbol": req.symbol,
        "qty": req.qty,
        "side": req.side,
        "order_type": req.order_type,
        "run_at": req.run_at,
        "status": req.status,
        "dry_run": req.dry_run,
        "broker_order_id": req.broker_order_id,
        "client_order_id": req.client_order_id,
        "reason": req.reason,
        "payload": req.payload,
        "created_at": req.created_at,
        "updated_at": req.updated_at,
    }


def as_dicts(requests: Iterable[TradeRequest]) -> list[dict[str, Any]]:
    return [as_dict(req) for req in requests]
