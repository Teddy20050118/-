"""Small LINE Messaging API client and webhook helpers (stdlib only)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from typing import Any, Dict
from urllib import error, request


LINE_API = "https://api.line.me/v2/bot"
LINE_DATA_API = "https://api-data.line.me/v2/bot"


def verify_signature(body: bytes, signature: str, channel_secret: str) -> bool:
    if not signature or not channel_secret:
        return False
    expected = base64.b64encode(hmac.new(channel_secret.encode("utf-8"), body, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(expected, signature)


def extract_restaurant_name(text: str) -> str:
    match = re.match(r"^\s*(?:餐廳|店家|店名)\s*[：:]\s*(.+?)\s*$", text or "")
    return match.group(1).strip()[:120] if match else ""


def source_key(source: Dict[str, Any]) -> str:
    return str(source.get("userId") or source.get("groupId") or source.get("roomId") or "")


def push_target(source: Dict[str, Any]) -> str:
    return str(source.get("groupId") or source.get("roomId") or source.get("userId") or "")


def _line_request(url: str, token: str, *, payload: Dict[str, Any] | None = None) -> bytes:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Authorization": f"Bearer {token}"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    req = request.Request(url, data=data, headers=headers, method="POST" if payload is not None else "GET")
    try:
        with request.urlopen(req, timeout=30) as response:
            return response.read()
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LINE API HTTP {exc.code}: {detail}") from exc


def get_message_content(message_id: str, token: str) -> bytes:
    return _line_request(f"{LINE_DATA_API}/message/{message_id}/content", token)


def reply_text(reply_token: str, text: str, token: str) -> None:
    _line_request(
        f"{LINE_API}/message/reply",
        token,
        payload={"replyToken": reply_token, "messages": [{"type": "text", "text": text[:5000]}]},
    )


def push_text(target: str, text: str, token: str) -> None:
    _line_request(
        f"{LINE_API}/message/push",
        token,
        payload={"to": target, "messages": [{"type": "text", "text": text[:5000]}]},
    )

