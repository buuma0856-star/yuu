"""LINE Messaging API の最小クライアント (標準ライブラリのみ)。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request

API_BASE = "https://api.line.me/v2/bot"
MAX_TEXT = 4900  # 1メッセージ 5000 文字まで
MAX_MESSAGES = 5  # 1回の送信で 5 件まで

log = logging.getLogger(__name__)


def verify_signature(channel_secret: str, body: bytes, signature: str) -> bool:
    digest = hmac.new(channel_secret.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature or "")


def split_text(text: str) -> list[str]:
    """長い文章を LINE の文字数制限に収まるよう行単位で分割する。"""
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > MAX_TEXT:
            chunks.append(line[:MAX_TEXT])
            line = line[MAX_TEXT:]
        if len(current) + len(line) + 1 > MAX_TEXT:
            chunks.append(current.rstrip("\n"))
            current = ""
        current += line + "\n"
    if current.strip():
        chunks.append(current.rstrip("\n"))
    if len(chunks) > MAX_MESSAGES:
        chunks = chunks[: MAX_MESSAGES - 1] + ["(長いため省略しました。全体はWeb画面で確認してください)"]
    return chunks


class LineClient:
    def __init__(self, access_token: str):
        self.access_token = access_token

    def _post(self, path: str, payload: dict) -> bool:
        req = urllib.request.Request(
            API_BASE + path,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.access_token}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10):
                return True
        except urllib.error.HTTPError as e:
            log.error("LINE API %s failed: %s %s", path, e.code, e.read().decode(errors="replace"))
        except urllib.error.URLError as e:
            log.error("LINE API %s failed: %s", path, e)
        return False

    def reply(self, reply_token: str, text: str) -> bool:
        messages = [{"type": "text", "text": t} for t in split_text(text)]
        return self._post("/message/reply", {"replyToken": reply_token, "messages": messages})

    def push(self, user_id: str, text: str) -> bool:
        messages = [{"type": "text", "text": t} for t in split_text(text)]
        return self._post("/message/push", {"to": user_id, "messages": messages})

    def get_profile(self, user_id: str) -> dict | None:
        req = urllib.request.Request(
            f"{API_BASE}/profile/{user_id}",
            headers={"Authorization": f"Bearer {self.access_token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                return json.loads(res.read())
        except (urllib.error.URLError, ValueError) as e:
            log.warning("LINE profile fetch failed: %s", e)
            return None
