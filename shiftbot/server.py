"""Webhook サーバー (LINE からのメッセージ受信) とシフト表の Web 表示。"""

from __future__ import annotations

import html
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .bot import ShiftBot, parse_month_arg
from .config import Settings
from .line_api import verify_signature
from .service import ShiftService

log = logging.getLogger(__name__)


def make_handler(bot: ShiftBot, settings: Settings):
    service = bot.service

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: str, content_type: str = "text/plain; charset=utf-8",
                  extra: dict | None = None) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            if urlparse(self.path).path != "/callback":
                return self._send(404, "not found")
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if not verify_signature(settings.channel_secret, body,
                                    self.headers.get("X-Line-Signature", "")):
                return self._send(403, "invalid signature")
            try:
                events = json.loads(body).get("events", [])
            except ValueError:
                return self._send(400, "bad request")
            for event in events:
                try:
                    bot.handle_event(event)
                except Exception:
                    log.exception("event handling failed: %s", event)
            self._send(200, "OK")

        def do_GET(self) -> None:
            url = urlparse(self.path)
            if url.path == "/":
                return self._send(200, "shiftbot is running")
            if url.path not in ("/shifts", "/shifts.csv"):
                return self._send(404, "not found")
            qs = parse_qs(url.query)
            token = qs.get("token", [""])[0]
            if not settings.web_token or token != settings.web_token:
                return self._send(403, "forbidden")
            year, month = parse_month_arg(qs.get("month", [""])[0], service)
            if url.path == "/shifts.csv":
                return self._send(200, service.csv_text(year, month), "text/csv; charset=utf-8", {
                    "Content-Disposition": f'attachment; filename="shift-{year}-{month:02d}.csv"'})
            self._send(200, render_html(service, year, month), "text/html; charset=utf-8")

        def log_message(self, fmt: str, *args) -> None:
            log.info("%s - %s", self.address_string(), fmt % args)

    return Handler


def render_html(service: ShiftService, year: int, month: int) -> str:
    import csv
    import io

    rows = list(csv.reader(io.StringIO(service.csv_text(year, month).lstrip("﻿"))))
    head, body = rows[0], rows[1:]
    th = "".join(f"<th>{html.escape(c)}</th>" for c in head)
    trs = "".join(
        "<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in r) + "</tr>" for r in body
    )
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{year}年{month}月 シフト表</title>
<style>
body{{font-family:sans-serif;margin:16px}} table{{border-collapse:collapse}}
th,td{{border:1px solid #ccc;padding:4px 8px;white-space:nowrap}} th{{background:#f3f3f3}}
.wrap{{overflow-x:auto}}
</style></head><body><h1>{year}年{month}月 シフト表</h1>
<div class="wrap"><table><tr>{th}</tr>{trs}</table></div>
<p><pre>{html.escape(service.table_text(year, month))}</pre></p></body></html>"""


def start_reminder_loop(service: ShiftService, interval: int = 300) -> threading.Thread:
    def loop() -> None:
        while True:
            try:
                service.run_scheduled_reminder()
            except Exception:
                log.exception("reminder failed")
            time.sleep(interval)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return thread


def serve(bot: ShiftBot, settings: Settings) -> None:
    if not settings.channel_secret or not settings.channel_access_token:
        raise SystemExit("LINE_CHANNEL_SECRET と LINE_CHANNEL_ACCESS_TOKEN を .env に設定してください")
    start_reminder_loop(bot.service)
    server = ThreadingHTTPServer((settings.host, settings.port), make_handler(bot, settings))
    log.info("listening on %s:%s (webhook: /callback)", settings.host, settings.port)
    server.serve_forever()
