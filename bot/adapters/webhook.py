"""通用 Webhook 适配器：把消息 POST 给任意 HTTP 端点，把「进来」交给调用方。

存在的意义有两个：

1. **证明适配器抽象是真的**。如果只有 OneBot 一个实现，BaseAdapter 里那些
   `chat_id_of` / `CAPABILITIES` / 可选能力降级就都只是摆设；
2. **给不写代码的接法**。飞书/钉钉/企业微信机器人、Bark、ntfy、甚至自己写的
   中转服务，都接受「POST 一段 JSON」——不用为每家写一个适配器。

它**只负责发**。收消息要平台自己 POST 到 `/onebot/event`（同样的 Event 结构），
所以 `CAPABILITIES` 里 group_member_list / message_lookup 都是 False —— 插件
读到 False 就该走降级路径，而不是撞一个 AttributeError。

配置项：

    "adapter":     "webhook"
    "webhook_url": "https://example.com/hook"
    "webhook_token": ""        # 有就带 Authorization: Bearer <token>
    "webhook_field": "text"    # 正文放在哪个字段（Bark 用 "body"，ntfy 用 "message"）
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from .base import AdapterError, BaseAdapter, register

MAX_IMAGE_BYTES = 10 * 1024 * 1024


@register
class WebhookAdapter(BaseAdapter):
    """通用 HTTP 出站：任何「POST JSON」的平台都能接。"""

    platform = "webhook"
    title = "通用 Webhook"
    CAPABILITIES = {
        "text": True, "image": True, "voice": False,
        "group_member_list": False, "message_lookup": False,
        "request_approval": False, "recall": False,
    }

    def __init__(self, config: Optional[dict] = None, **kw) -> None:
        super().__init__(config, **kw)
        self.url = str(self.config.get("webhook_url") or "").strip()
        self.token = str(self.config.get("webhook_token") or "").strip()
        self.field = str(self.config.get("webhook_field") or "text").strip() or "text"
        self.timeout = float(self.config.get("webhook_timeout") or 15)

    # ---------- 内部 ----------
    def _post(self, payload: dict) -> dict:
        if not self.url:
            raise AdapterError("webhook_url 没配置")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(
            self.url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = r.read(2048).decode("utf-8", "replace")
            return {"ok": True, "status": 200, "body": body[:500], "message_id": ""}
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read(300).decode("utf-8", "replace")
            except Exception:                                     # noqa: BLE001
                pass
            raise AdapterError(f"webhook {e.code} {e.reason}：{detail}") from e
        except Exception as e:                                    # noqa: BLE001
            raise AdapterError(f"webhook 连不上：{type(e).__name__}: {e}") from e

    # ---------- 传输 ----------
    def send_text(self, chat_id: str, text: str, *,
                  reply_to: Optional[str] = None,
                  at_uid: Optional[str] = None) -> Any:
        payload = {"chat_id": chat_id, "reply_to": reply_to or "",
                   "at_uid": at_uid or "", "platform": self.platform}
        payload[self.field] = text
        return self._post(payload)

    def send_image(self, chat_id: str, path: str, caption: Optional[str] = None, *,
                   reply_to: Optional[str] = None,
                   at_uid: Optional[str] = None) -> Any:
        p = Path(path)
        try:
            raw = p.read_bytes()
        except OSError as e:
            raise AdapterError(f"读不到图片 {p}：{e}") from e
        if len(raw) > MAX_IMAGE_BYTES:
            raise AdapterError(f"图片过大（{len(raw)} 字节），webhook 不发")
        payload = {"chat_id": chat_id, "platform": self.platform,
                   "image_base64": base64.b64encode(raw).decode("ascii"),
                   "image_name": p.name}
        payload[self.field] = caption or ""
        return self._post(payload)

    # ---------- 自检 / 配置 ----------
    def health(self) -> dict:
        if not self.url:
            return {"ok": False, "platform": self.platform, "error": "webhook_url 未配置"}
        return {"ok": True, "platform": self.platform, "url": self.url,
                "note": "只做出站；入站由平台 POST 到 /onebot/event"}

    def describe(self) -> dict:
        d = super().describe()
        d.update({"url": self.url, "has_token": bool(self.token), "field": self.field})
        return d

    def reconfigure(self, config: dict) -> bool:
        super().reconfigure(config)
        self.url = str(self.config.get("webhook_url") or self.url).strip()
        self.token = str(self.config.get("webhook_token") or "").strip()
        self.field = str(self.config.get("webhook_field") or self.field).strip() or "text"
        return True
