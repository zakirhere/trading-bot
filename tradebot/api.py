from __future__ import annotations

import html
import hashlib
import hmac
import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, quote, urlparse
from zoneinfo import ZoneInfo

from . import config, db, journal, nl, spread_summary, state, worker

log = logging.getLogger(__name__)
COOKIE_NAME = "tradebot_session"
ET = ZoneInfo("America/New_York")


def _parse_run_at(value: str | None) -> str | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.astimezone(timezone.utc).isoformat()


def _session_value(token: str) -> str:
    return hashlib.sha256(f"tradebot:{token}".encode()).hexdigest()


def _cookie_value(cookie_header: str | None, name: str) -> str | None:
    if not cookie_header:
        return None
    for part in cookie_header.split(";"):
        if "=" not in part:
            continue
        k, v = part.strip().split("=", 1)
        if k == name:
            return v
    return None


def render_login(*, message: str | None = None) -> str:
    message_html = ""
    if message:
        message_html = f'<div class="notice">{html.escape(message)}</div>'
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Tradebot Login</title>
  <style>
    :root {{ color-scheme: dark; }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #0b1020;
      color: #e5e7eb;
    }}
    main {{
      width: min(420px, calc(100vw - 32px));
      border: 1px solid #263247;
      border-radius: 8px;
      background: #121a2b;
      padding: 22px;
      box-shadow: 0 18px 44px rgba(0, 0, 0, .35);
    }}
    h1 {{ margin: 0 0 16px; font-size: 22px; }}
    label {{ display: block; color: #94a3b8; font-size: 13px; margin-bottom: 6px; }}
    input {{
      width: 100%;
      min-height: 42px;
      border: 1px solid #263247;
      border-radius: 6px;
      background: #0f1726;
      color: #e5e7eb;
      padding: 8px 10px;
      font-size: 14px;
      box-sizing: border-box;
    }}
    button {{
      width: 100%;
      margin-top: 14px;
      min-height: 42px;
      border: 0;
      border-radius: 6px;
      background: linear-gradient(135deg, #2563eb, #4f46e5);
      color: #fff;
      font-weight: 750;
      cursor: pointer;
    }}
    .notice {{
      margin-bottom: 14px;
      padding: 10px 11px;
      border: 1px solid rgba(248, 113, 113, .42);
      border-radius: 6px;
      background: rgba(248, 113, 113, .13);
      color: #fca5a5;
      font-weight: 650;
    }}
  </style>
</head>
<body>
  <main>
    <h1>Tradebot Login</h1>
    {message_html}
    <form method="post" action="/login">
      <label for="token">Dashboard token</label>
      <input id="token" name="token" type="password" autocomplete="current-password" autofocus required>
      <button type="submit">Unlock Dashboard</button>
    </form>
  </main>
</body>
</html>"""


def _request_row(req: db.TradeRequest) -> str:
    status = html.escape(req.status)
    cells = [
        str(req.id),
        f'<span class="chip status-{status}">{status}</span>',
        html.escape(req.kind),
        f'<strong>{html.escape(req.symbol)}</strong>',
        f"{req.qty:g}",
        html.escape(req.run_at or ""),
        html.escape(req.reason or ""),
        f'<span class="mono">{html.escape(req.broker_order_id or "")}</span>',
    ]
    return "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"


def _spread_label(req: db.TradeRequest) -> str:
    payload = req.payload
    short_symbol = payload.get("short_symbol", "")
    long_symbol = payload.get("long_symbol", "")
    if short_symbol and long_symbol:
        return f"{short_symbol}/{long_symbol}"
    return ""


def _strategy_spread_row(req: db.TradeRequest) -> str:
    payload = req.payload
    status = html.escape(req.status)
    cells = [
        str(req.id),
        html.escape(str(payload.get("strategy") or "")),
        f'<span class="chip status-{status}">{status}</span>',
        html.escape(str(payload.get("direction") or "")),
        html.escape(str(payload.get("expiration_date") or "")),
        f'<span class="mono">{html.escape(_spread_label(req))}</span>',
        html.escape(str(payload.get("limit_credit", ""))),
        html.escape(req.reason or ""),
    ]
    return "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"


def _strategy_spread_dict(req: db.TradeRequest) -> dict[str, Any]:
    payload = req.payload
    return {
        "id": req.id,
        "strategy": payload.get("strategy"),
        "status": req.status,
        "symbol": req.symbol,
        "direction": payload.get("direction"),
        "expiration_date": payload.get("expiration_date"),
        "short_symbol": payload.get("short_symbol"),
        "long_symbol": payload.get("long_symbol"),
        "short_strike": payload.get("short_strike"),
        "long_strike": payload.get("long_strike"),
        "limit_credit": payload.get("limit_credit"),
        "quoted_credit": payload.get("quoted_credit", payload.get("credit")),
        "broker_order_id": req.broker_order_id,
        "client_order_id": req.client_order_id,
        "reason": req.reason,
        "created_at": req.created_at,
        "updated_at": req.updated_at,
    }


def _money_whole(value: str | Decimal | float | int | None) -> str:
    if value is None:
        return "n/a"
    amount = Decimal(str(value)).quantize(Decimal("1"))
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):,}"


def _contract_price(value: str | Decimal | float | int | None) -> str:
    if value is None:
        return "n/a"
    return f"${Decimal(str(value)):.2f}"


def _placed_et(value: str | None) -> str:
    if not value:
        return "unmatched"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ET).strftime("%Y-%m-%d %H:%M")


def _expiry_date(value: str) -> datetime:
    return datetime.strptime(value, "%y%m%d").replace(tzinfo=timezone.utc)


def _expiry_label(value: str) -> str:
    dte = (_expiry_date(value).date() - datetime.now(timezone.utc).date()).days
    return f"{_expiry_date(value).strftime('%Y-%m-%d')} ({dte} DTE)"


def _pnl_class(value: str | None) -> str:
    if value is None:
        return ""
    return "pnl-positive" if Decimal(value) >= 0 else "pnl-negative"


def _open_spread_rows(summary: spread_summary.SpreadSummary) -> str:
    rows = sorted(
        summary.open_spreads,
        key=lambda item: (item.placed_at is not None, item.placed_at or ""),
        reverse=True,
    )
    if not rows:
        return '<tr><td colspan="7" class="muted">No open broker option spreads.</td></tr>'
    out = []
    for item in rows:
        pnl_class = _pnl_class(item.estimated_pnl)
        out.append(
            "<tr>"
            f"<td>{html.escape(_placed_et(item.placed_at))}</td>"
            f'<td><span class="mono">{html.escape(f"{item.short_strike}/{item.long_strike} {item.option_type}")}</span></td>'
            f"<td>{item.qty}</td>"
            f"<td>{html.escape(_contract_price(item.entry_credit))}</td>"
            f"<td>{html.escape(_contract_price(item.current_debit))}</td>"
            f'<td class="{pnl_class}">{html.escape(_money_whole(item.estimated_pnl))}</td>'
            f"<td>{html.escape(_money_whole(item.max_loss))}</td>"
            "</tr>"
        )
    return "\n".join(out)


def _open_spread_pnl_list(summary: spread_summary.SpreadSummary) -> str:
    rows = sorted(
        summary.open_spreads,
        key=lambda item: (
            item.estimated_pnl is None,
            Decimal(item.estimated_pnl) if item.estimated_pnl is not None else Decimal("0"),
        ),
    )
    if not rows:
        return '<div class="muted">No open spreads.</div>'
    return "".join(
        "<div>"
        f'<span class="mono">{html.escape(f"{item.short_strike}/{item.long_strike} {item.option_type}")}</span>'
        f" &rarr; "
        f'<span class="{_pnl_class(item.estimated_pnl)}">{html.escape(_money_whole(item.estimated_pnl))}</span>'
        "</div>"
        for item in rows
    )


def _open_spread_expiry_list(summary: spread_summary.SpreadSummary) -> str:
    counts: dict[str, int] = {}
    for item in summary.open_spreads:
        counts[item.expiration] = counts.get(item.expiration, 0) + 1
    if not counts:
        return '<div class="muted">No expiries.</div>'
    return "".join(
        f"<div>{html.escape(_expiry_label(expiration))}: {count} spread(s)</div>"
        for expiration, count in sorted(counts.items())
    )


def _open_spread_warning(summary: spread_summary.SpreadSummary) -> str:
    warnings = []
    if summary.local_drift:
        ids = ", ".join(str(item.request_id) for item in summary.local_drift)
        warnings.append(f"Local drift: {len(summary.local_drift)} stale filled records ({ids})")
    if summary.unpaired_legs:
        warnings.append(f"Unpaired option legs: {len(summary.unpaired_legs)}")
    if summary.unmatched_broker_spread_count:
        warnings.append(f"Unmatched broker spreads: {summary.unmatched_broker_spread_count}")
    if summary.unparsable_option_symbols:
        warnings.append(f"Unparsable option symbols: {len(summary.unparsable_option_symbols)}")
    if not warnings:
        return ""
    return '<div class="spread-warning">' + html.escape(" | ".join(warnings)) + "</div>"


def _render_open_spread_summary(conn: sqlite3.Connection) -> str:
    try:
        summary = spread_summary.run(conn)
    except Exception as exc:
        log.warning("open spread summary unavailable: %s", exc)
        return f"""
        <section>
          <h2>Open Spreads</h2>
          <div class="muted">Open spread summary unavailable: {html.escape(str(exc))}</div>
        </section>
        """

    return f"""
        <section>
          <h2>Open Spreads</h2>
          <div class="spread-headline">
            <span>Open spreads: <strong>{len(summary.open_spreads)}</strong></span>
            <span>Unrealized P/L: <strong class="{_pnl_class(summary.estimated_total_pnl)}">{html.escape(_money_whole(summary.estimated_total_pnl))}</strong></span>
            <span>Open risk: <strong>{html.escape(_money_whole(summary.spread_max_loss_usd))}</strong></span>
            <span>Mode: <strong>{html.escape(summary.mode)}</strong></span>
          </div>
          {_open_spread_warning(summary)}
          <table class="compact-table">
            <thead><tr><th>Placed (ET)</th><th>Strikes</th><th>Qty</th><th>In</th><th>Now</th><th>P/L</th><th>Risk</th></tr></thead>
            <tbody>{_open_spread_rows(summary)}</tbody>
          </table>
          <div class="spread-subgrid">
            <div><h3>By P/L</h3>{_open_spread_pnl_list(summary)}</div>
            <div><h3>By Expiry</h3>{_open_spread_expiry_list(summary)}</div>
          </div>
        </section>
    """


def render_dashboard(conn: sqlite3.Connection, *, message: str | None = None) -> str:
    s = state.load()
    requests = db.list_requests(conn, limit=50)
    rows = "\n".join(_request_row(req) for req in requests)
    spread_rows = "\n".join(
        _strategy_spread_row(req)
        for req in db.list_strategy_spread_requests(conn, limit=50)
    )
    orders = "\n".join(
        "<tr>"
        f"<td>{html.escape(o.submitted_at)}</td>"
        f"<td><strong>{html.escape(o.symbol)}</strong></td>"
        f"<td>{html.escape(o.side)}</td>"
        f"<td>{o.qty:g}</td>"
        f'<td><span class="chip status-{html.escape(o.status)}">{html.escape(o.status)}</span></td>'
        f'<td><span class="mono">{html.escape(o.broker_order_id or "")}</span></td>'
        "</tr>"
        for o in reversed(s.orders[-20:])
    )
    halted = "halted" if s.halted else "running"
    halt_reason = html.escape(s.halt_reason or "")
    message_html = ""
    if message:
        message_html = f'<div class="notice">{html.escape(message)}</div>'
    open_spread_summary_html = _render_open_spread_summary(conn)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Tradebot</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #0b1020;
      --panel: #121a2b;
      --panel-2: #0f1726;
      --text: #e5e7eb;
      --muted: #94a3b8;
      --line: #263247;
      --accent: #60a5fa;
      --accent-2: #34d399;
      --accent-3: #fbbf24;
      --danger: #f87171;
      --ok: #34d399;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at 18% 8%, rgba(96, 165, 250, .14), transparent 30%),
        radial-gradient(circle at 92% 16%, rgba(52, 211, 153, .10), transparent 34%),
        var(--bg);
      color: var(--text);
    }}
    header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 20px 28px;
      border-bottom: 1px solid var(--line);
      background: linear-gradient(90deg, #090d18, #111b34 58%, #0f2c2b);
      color: #fff;
    }}
    main {{ max-width: 1280px; margin: 0 auto; padding: 24px; }}
    h1 {{ font-size: 22px; margin: 0; }}
    h2 {{ font-size: 16px; margin: 0 0 14px; }}
    h3 {{ margin: 0 0 8px; color: var(--muted); font-size: 13px; }}
    .status {{
      display: inline-block;
      border-radius: 999px;
      padding: 6px 10px;
      background: {"rgba(248, 113, 113, .16)" if s.halted else "rgba(52, 211, 153, .16)"};
      color: {"var(--danger)" if s.halted else "var(--ok)"};
      font-weight: 800;
    }}
    .grid {{ display: grid; grid-template-columns: 420px 1fr; gap: 20px; align-items: start; }}
    .left-stack {{ display: grid; gap: 16px; }}
    section {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
      box-shadow: 0 14px 34px rgba(0, 0, 0, .24);
    }}
    label {{ display: block; color: var(--muted); font-size: 13px; margin: 12px 0 6px; }}
    input, select {{
      width: 100%;
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 10px;
      font-size: 14px;
      background: var(--panel-2);
      color: var(--text);
    }}
    button {{
      min-height: 38px;
      border: 0;
      border-radius: 6px;
      padding: 8px 12px;
      font-size: 14px;
      font-weight: 650;
      background: linear-gradient(135deg, #2563eb, #4f46e5);
      color: white;
      cursor: pointer;
    }}
    .button-row {{ display: flex; gap: 10px; margin-top: 14px; }}
    .secondary {{ background: linear-gradient(135deg, #334155, #1f2937); }}
    .danger {{ background: linear-gradient(135deg, #dc2626, #7f1d1d); }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ text-align: left; border-bottom: 1px solid var(--line); padding: 9px 8px; vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 650; }}
    .compact-table th, .compact-table td {{ padding: 8px 8px; }}
    .tables {{ display: grid; gap: 18px; }}
    .console {{
      border-color: rgba(96, 165, 250, .32);
      background: linear-gradient(180deg, #131f35, #101827);
    }}
    .quick-examples {{
      display: grid;
      gap: 8px;
      margin-top: 12px;
      color: var(--muted);
      font-size: 13px;
    }}
    .example {{
      border-left: 3px solid var(--accent-2);
      padding: 7px 9px;
      background: rgba(52, 211, 153, .10);
      border-radius: 4px;
      color: #a7f3d0;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }}
    .chip {{
      display: inline-block;
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 12px;
      font-weight: 750;
      background: rgba(96, 165, 250, .16);
      color: #bfdbfe;
    }}
    .status-filled, .status-submitted {{ background: rgba(52, 211, 153, .16); color: #86efac; }}
    .status-queued {{ background: rgba(251, 191, 36, .16); color: #fde68a; }}
    .status-blocked, .status-error {{ background: rgba(248, 113, 113, .18); color: #fca5a5; }}
    .status-dry_run {{ background: rgba(167, 139, 250, .18); color: #ddd6fe; }}
    .mono {{
      overflow-wrap: anywhere;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
      color: #cbd5e1;
    }}
    .actions {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
    }}
    .actions form, .actions button {{ width: 100%; }}
    .notice {{
      margin-bottom: 18px;
      padding: 11px 12px;
      border: 1px solid rgba(251, 191, 36, .36);
      border-radius: 6px;
      background: rgba(251, 191, 36, .12);
      color: #fde68a;
      font-weight: 650;
    }}
    .muted {{ color: var(--muted); }}
    .spread-headline {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px 16px;
      margin-bottom: 12px;
      font-size: 14px;
    }}
    .spread-warning {{
      margin-bottom: 12px;
      padding: 9px 10px;
      border: 1px solid rgba(248, 113, 113, .34);
      border-radius: 6px;
      background: rgba(248, 113, 113, .12);
      color: #fecaca;
      font-weight: 650;
    }}
    .spread-subgrid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
      margin-top: 14px;
      color: #cbd5e1;
      font-size: 13px;
    }}
    .pnl-positive {{ color: #86efac; font-weight: 750; }}
    .pnl-negative {{ color: #fca5a5; font-weight: 750; }}
    textarea {{
      width: 100%;
      min-height: 130px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 10px;
      font-size: 14px;
      font-family: inherit;
      background: var(--panel-2);
      color: var(--text);
      resize: vertical;
    }}
    @media (max-width: 860px) {{
      .grid {{ grid-template-columns: 1fr; }}
      header {{ align-items: flex-start; gap: 8px; flex-direction: column; }}
      main {{ padding: 14px; }}
      .spread-subgrid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Tradebot</h1>
    <div><span class="status">{halted}</span> {halt_reason}</div>
  </header>
  <main>
    {message_html}
    <div class="grid">
      <div class="left-stack">
        <section class="console">
          <h2>Natural Language Order</h2>
          <form method="post" action="/nl-trade-requests">
            <label for="nl_text">Request</label>
            <textarea id="nl_text" name="text" required>buy 1 AAPL at noon today</textarea>
            <div class="button-row">
              <button type="submit">Queue Order</button>
            </div>
          </form>
          <div class="quick-examples">
            <div class="example">buy 1 AAPL now</div>
            <div class="example">dry run buy 2 MSFT tomorrow at 12:30 PM</div>
            <div class="example">buy 1 TSLA at market open tomorrow</div>
          </div>
        </section>
        <section>
          <h2>Controls</h2>
          <div class="actions">
            <form method="post" action="/worker/run-once"><button class="secondary" type="submit">Run Due</button></form>
            <form method="post" action="/halt"><button class="danger" type="submit">Halt</button></form>
            <form method="post" action="/resume"><button class="secondary" type="submit">Resume</button></form>
          </div>
        </section>
      </div>
      <div class="tables">
        {open_spread_summary_html}
        <section>
          <h2>Strategy Spreads</h2>
          <table>
            <thead><tr><th>ID</th><th>Strategy</th><th>Status</th><th>Direction</th><th>Expiry</th><th>Spread</th><th>Limit Credit</th><th>Reason</th></tr></thead>
            <tbody>{spread_rows}</tbody>
          </table>
        </section>
        <section>
          <h2>Trade Requests</h2>
          <table>
            <thead><tr><th>ID</th><th>Status</th><th>Kind</th><th>Symbol</th><th>Qty</th><th>Run At</th><th>Reason</th><th>Order ID</th></tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </section>
        <section>
          <h2>Orders</h2>
          <table>
            <thead><tr><th>Submitted</th><th>Symbol</th><th>Side</th><th>Qty</th><th>Status</th><th>Order ID</th></tr></thead>
            <tbody>{orders}</tbody>
          </table>
        </section>
      </div>
    </div>
  </main>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.info("api %s - %s", self.address_string(), fmt % args)

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        message = query.get("message", [None])[-1]
        if parsed.path == "/login":
            self._reply_html(render_login(message=message))
            return
        if not self._is_authorized():
            if parsed.path.startswith("/api/"):
                self._reply_json({"error": "unauthorized"}, code=401)
            else:
                self._redirect("/login")
            return
        conn = db.connect()
        db.init(conn)
        try:
            self._do_GET(parsed.path, conn, message=message)
        finally:
            conn.close()

    def _do_GET(self, path: str, conn: sqlite3.Connection, *, message: str | None = None) -> None:
        if path == "/":
            self._reply_html(render_dashboard(conn, message=message))
            return
        if path == "/api/status":
            s = state.load()
            self._reply_json(
                {
                    "halted": s.halted,
                    "halt_reason": s.halt_reason,
                    "processed_keys": len(s.processed_keys),
                    "orders": len(s.orders),
                }
            )
            return
        if path == "/api/trade-requests":
            self._reply_json(db.as_dicts(db.list_requests(conn)))
            return
        if path == "/api/strategy-spreads":
            self._reply_json(
                [
                    _strategy_spread_dict(req)
                    for req in db.list_strategy_spread_requests(conn, limit=500)
                ]
            )
            return
        if path == "/api/open-spreads":
            self._reply_json(spread_summary.to_dict(spread_summary.run(conn)))
            return
        if path == "/api/trade-journal":
            path = journal.sync_from_db(conn)
            self._reply_json({"path": str(path)})
            return
        self._reply_json({"error": "not found"}, code=404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/login":
            data = self._read_body()
            cfg = config.load_dashboard_auth_config()
            token = str(data.get("token", ""))
            if hmac.compare_digest(token, cfg.token):
                self.send_response(303)
                self.send_header("Location", "/")
                self.send_header(
                    "Set-Cookie",
                    f"{COOKIE_NAME}={_session_value(cfg.token)}; Path=/; HttpOnly; SameSite=Strict",
                )
                self.end_headers()
            else:
                self._redirect("/login?message=Invalid%20token")
            return
        if not self._is_authorized():
            if parsed.path.startswith("/api/"):
                self._reply_json({"error": "unauthorized"}, code=401)
            else:
                self._redirect("/login")
            return
        conn = db.connect()
        db.init(conn)
        try:
            self._do_POST(parsed.path, conn)
        finally:
            conn.close()

    def _do_POST(self, path: str, conn: sqlite3.Connection) -> None:
        if path in {"/trade-requests", "/api/trade-requests"}:
            data = self._read_body()
            req = db.create_stock_market_buy(
                conn,
                symbol=str(data["symbol"]),
                qty=float(data["qty"]),
                run_at=_parse_run_at(data.get("run_at")),
                dry_run=str(data.get("dry_run", "0")) == "1",
            )
            if path.startswith("/api/"):
                self._reply_json(db.as_dict(req), code=201)
            else:
                self._redirect("/")
            return
        if path in {"/nl-trade-requests", "/api/nl-trade-requests"}:
            data = self._read_body()
            try:
                parsed = nl.parse_stock_buy(str(data.get("text", "")))
                req = db.create_stock_market_buy(
                    conn,
                    symbol=parsed.symbol,
                    qty=parsed.qty,
                    run_at=parsed.run_at,
                    dry_run=parsed.dry_run,
                )
            except nl.ParseError as exc:
                if path.startswith("/api/"):
                    self._reply_json({"error": str(exc)}, code=400)
                else:
                    self._redirect(f"/?message={quote(str(exc))}")
                return
            if path.startswith("/api/"):
                self._reply_json(db.as_dict(req), code=201)
            else:
                self._redirect(f"/?message={quote(f'Queued request #{req.id}: {req.symbol} x {req.qty:g}')}")
            return
        if path == "/worker/run-once":
            worker.run_once(conn)
            self._redirect("/")
            return
        if path in {"/halt", "/api/halt"}:
            with state.transaction() as s:
                s.halted = True
                s.halt_reason = "dashboard"
            self._redirect_or_json(path, {"halted": True})
            return
        if path in {"/resume", "/api/resume"}:
            with state.transaction() as s:
                s.halted = False
                s.halt_reason = None
            self._redirect_or_json(path, {"halted": False})
            return
        if path == "/api/trade-journal/sync":
            path = journal.sync_from_db(conn)
            self._reply_json({"path": str(path)})
            return
        self._reply_json({"error": "not found"}, code=404)

    def _is_authorized(self) -> bool:
        cfg = config.load_dashboard_auth_config()
        expected_session = _session_value(cfg.token)
        cookie_session = _cookie_value(self.headers.get("Cookie"), COOKIE_NAME)
        if cookie_session and hmac.compare_digest(cookie_session, expected_session):
            return True
        auth = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if auth.startswith(prefix):
            supplied = auth[len(prefix):].strip()
            return hmac.compare_digest(supplied, cfg.token)
        return False

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode()
        if self.headers.get("Content-Type", "").startswith("application/json"):
            return json.loads(raw or "{}")
        parsed = parse_qs(raw, keep_blank_values=True)
        return {k: v[-1] for k, v in parsed.items()}

    def _reply_json(self, body: Any, *, code: int = 200) -> None:
        data = json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _reply_html(self, body: str, *, code: int = 200) -> None:
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def _redirect_or_json(self, path: str, body: dict[str, Any]) -> None:
        if path.startswith("/api/"):
            self._reply_json(body)
        else:
            self._redirect("/")


def make_server(host: str = config.SERVICE_HOST, port: int = config.SERVICE_PORT) -> ThreadingHTTPServer:
    conn = db.connect()
    db.init(conn)
    conn.close()
    return ThreadingHTTPServer((host, port), Handler)


def start_in_thread(host: str = config.SERVICE_HOST, port: int = config.SERVICE_PORT) -> ThreadingHTTPServer:
    server = make_server(host, port)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="api")
    thread.start()
    log.info("dashboard listening on http://%s:%d", host, port)
    return server
