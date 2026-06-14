from __future__ import annotations

import sqlite3
from pathlib import Path

from . import config, db


def sync_from_db(
    conn: sqlite3.Connection,
    *,
    path: Path = config.TRADE_JOURNAL_FILE,
    limit: int = 500,
) -> Path:
    requests = list(reversed(db.list_requests(conn, limit=limit)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(requests), encoding="utf-8")
    path.chmod(0o600)
    return path


def render_markdown(requests: list[db.TradeRequest]) -> str:
    lines = [
        "# Tradebot Trade Journal",
        "",
        "Generated from the local SQLite queue. SQLite remains the source of truth.",
        "",
        "| ID | Created UTC | Status | Strategy | Kind | Symbol | Qty | Details | Broker Order | Reason |",
        "|---:|---|---|---|---|---|---:|---|---|---|",
    ]
    for req in requests:
        lines.append(request_row(req))
    lines.append("")
    return "\n".join(lines)


def request_row(req: db.TradeRequest) -> str:
    return "| " + " | ".join(
        [
            str(req.id),
            escape_md(req.created_at),
            escape_md(req.status),
            escape_md(str(req.payload.get("strategy") or "")),
            escape_md(req.kind),
            escape_md(req.symbol),
            f"{req.qty:g}",
            escape_md(request_details(req)),
            escape_md(req.broker_order_id or ""),
            escape_md(req.reason or ""),
        ]
    ) + " |"


def request_details(req: db.TradeRequest) -> str:
    if req.kind == "option_spread_open":
        payload = req.payload
        short_symbol = payload.get("short_symbol", "")
        long_symbol = payload.get("long_symbol", "")
        expiry = payload.get("expiration_date", "")
        direction = payload.get("direction", "")
        credit = payload.get("limit_credit", payload.get("credit", ""))
        return f"{direction} {expiry} {short_symbol}/{long_symbol} credit={credit}"
    if req.kind == "option_spread_close":
        payload = req.payload
        short_symbol = payload.get("short_symbol", "")
        long_symbol = payload.get("long_symbol", "")
        expiry = payload.get("expiration_date", "")
        direction = payload.get("direction", "")
        debit = payload.get("limit_debit", "")
        return f"close {direction} {expiry} {short_symbol}/{long_symbol} debit={debit}"
    if req.kind == "stock_market_buy":
        timing = f" run_at={req.run_at}" if req.run_at else ""
        return f"{req.side} market{timing}"
    return ""


def escape_md(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")
