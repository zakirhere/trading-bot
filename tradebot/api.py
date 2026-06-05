from __future__ import annotations

import html
import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from . import config, db, nl, state, worker

log = logging.getLogger(__name__)


def _parse_run_at(value: str | None) -> str | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.astimezone(timezone.utc).isoformat()


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


def render_dashboard(conn: sqlite3.Connection, *, message: str | None = None) -> str:
    s = state.load()
    requests = db.list_requests(conn, limit=50)
    rows = "\n".join(_request_row(req) for req in requests)
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
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Tradebot</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f8;
      --panel: #ffffff;
      --text: #1f2933;
      --muted: #667085;
      --line: #d9dee7;
      --accent: #3157d5;
      --accent-2: #00a884;
      --accent-3: #f59e0b;
      --danger: #b42318;
      --ok: #027a48;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background:
        linear-gradient(135deg, rgba(49, 87, 213, .10), rgba(0, 168, 132, .08) 38%, rgba(245, 158, 11, .10)),
        var(--bg);
      color: var(--text);
    }}
    header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 20px 28px;
      border-bottom: 1px solid var(--line);
      background: linear-gradient(90deg, #111827, #1e3a8a 55%, #047857);
      color: #fff;
    }}
    main {{ max-width: 1280px; margin: 0 auto; padding: 24px; }}
    h1 {{ font-size: 22px; margin: 0; }}
    h2 {{ font-size: 16px; margin: 0 0 14px; }}
    .status {{
      display: inline-block;
      border-radius: 999px;
      padding: 6px 10px;
      background: {"#fee4e2" if s.halted else "#dcfae6"};
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
      box-shadow: 0 10px 24px rgba(31, 41, 51, .06);
    }}
    label {{ display: block; color: var(--muted); font-size: 13px; margin: 12px 0 6px; }}
    input, select {{
      width: 100%;
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 10px;
      font-size: 14px;
      background: #fff;
      color: var(--text);
    }}
    button {{
      min-height: 38px;
      border: 0;
      border-radius: 6px;
      padding: 8px 12px;
      font-size: 14px;
      font-weight: 650;
      background: linear-gradient(135deg, var(--accent), #4f46e5);
      color: white;
      cursor: pointer;
    }}
    .button-row {{ display: flex; gap: 10px; margin-top: 14px; }}
    .secondary {{ background: linear-gradient(135deg, #475467, #344054); }}
    .danger {{ background: linear-gradient(135deg, var(--danger), #7a271a); }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ text-align: left; border-bottom: 1px solid var(--line); padding: 9px 8px; vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 650; }}
    .tables {{ display: grid; gap: 18px; }}
    .console {{
      border-color: rgba(49, 87, 213, .28);
      background: linear-gradient(180deg, #ffffff, #f7f9ff);
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
      background: #ecfdf3;
      border-radius: 4px;
      color: #05603a;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }}
    .chip {{
      display: inline-block;
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 12px;
      font-weight: 750;
      background: #eef4ff;
      color: #3538cd;
    }}
    .status-filled, .status-submitted {{ background: #dcfae6; color: #027a48; }}
    .status-queued {{ background: #fef0c7; color: #b54708; }}
    .status-blocked, .status-error {{ background: #fee4e2; color: #b42318; }}
    .status-dry_run {{ background: #f4ebff; color: #6941c6; }}
    .mono {{
      overflow-wrap: anywhere;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
      color: #475467;
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
      border: 1px solid #fedf89;
      border-radius: 6px;
      background: #fffaeb;
      color: #7a2e0e;
      font-weight: 650;
    }}
    textarea {{
      width: 100%;
      min-height: 130px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 10px;
      font-size: 14px;
      font-family: inherit;
      resize: vertical;
    }}
    @media (max-width: 860px) {{
      .grid {{ grid-template-columns: 1fr; }}
      header {{ align-items: flex-start; gap: 8px; flex-direction: column; }}
      main {{ padding: 14px; }}
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
        self._reply_json({"error": "not found"}, code=404)

    def do_POST(self):
        parsed = urlparse(self.path)
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
        self._reply_json({"error": "not found"}, code=404)

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
