from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, state

log = logging.getLogger(__name__)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.info("killswitch %s - %s", self.address_string(), fmt % args)

    def do_POST(self):
        if self.path == "/halt":
            with state.transaction() as s:
                s.halted = True
                s.halt_reason = "manual /halt"
            self._reply(200, {"halted": True})
        else:
            self._reply(404, {"error": "not found"})

    def do_GET(self):
        if self.path == "/status":
            s = state.load()
            self._reply(
                200,
                {
                    "halted": s.halted,
                    "halt_reason": s.halt_reason,
                    "processed_keys": s.processed_keys,
                    "orders": [asdict(o) for o in s.orders],
                },
            )
        else:
            self._reply(404, {"error": "not found"})

    def _reply(self, code: int, body: dict):
        data = json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def start_in_thread() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((config.KILLSWITCH_HOST, config.KILLSWITCH_PORT), _Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True, name="killswitch")
    t.start()
    log.info(
        "killswitch listening on http://%s:%d",
        config.KILLSWITCH_HOST,
        config.KILLSWITCH_PORT,
    )
    return server
