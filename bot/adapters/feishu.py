"""飞书（Lark）适配器：自建应用，群聊与单聊都能收发。

入站：飞书把事件推到你填的「请求地址」（本框架是 `/feishu/event`）。
出站：`tenant_access_token` + `im/v1/messages`。

配置（state/bot_config.json，扁平存放）：

    feishu_app_id           cli_xxx
    feishu_app_secret       应用凭证里的 App Secret
    feishu_verification_token  事件订阅页的 Verification Token（必填，用来验真）
    feishu_bot_open_id     可选：机器人自己的 open_id，用来判断「有没有 @ 我」

**不支持加密回调**：飞书可以在事件订阅里开 Encrypt Key，那是 AES-256-CBC，
标准库没有 AES。要接这个适配器，请在飞书后台把 Encrypt Key 留空。
（留空不影响安全：`verification_token` 仍然是校验的。）
"""
from __future__ import annotations

import json
from typing import Any, Optional

from .base import AdapterError, BaseAdapter, Inbound, register
from .http import HttpError, TokenCache, header_lookup, parse_qs, request_json, safe_equal

API = "https://open.feishu.cn/open-apis"


@register
class FeishuAdapter(BaseAdapter):
    """飞书自建应用：群里 @ 它、单聊直接说话都能通。"""

    platform = "feishu"
    title = "飞书（Lark）"
    DESCRIPTION = "飞书自建应用：群聊 @ 与单聊都能收发"
    VERSION = "1.0"
    INGRESS = "/feishu/event"
    CAPABILITIES = {
        "text": True, "image": False, "voice": False,
        "group_member_list": False, "message_lookup": False,
        "request_approval": False, "recall": False,
    }
    CONFIG_SCHEMA = {
        "feishu_app_id": {"type": "str", "label": "App ID", "default": "",
                          "description": "开发者后台 → 凭证与基础信息"},
        "feishu_app_secret": {"type": "str", "label": "App Secret", "default": "",
                              "secret": True, "description": "同上，注意保密"},
        "feishu_verification_token": {"type": "str", "label": "Verification Token",
                                      "default": "",
                                      "description": "事件订阅页里的 Token，用来校验回调来源"},
        "feishu_bot_open_id": {"type": "str", "label": "机器人 open_id（可选）",
                               "default": "",
                               "description": "填了才能准确判断群里是不是 @ 了它"},
    }

    def __init__(self, config: Optional[dict] = None, **kw) -> None:
        super().__init__(config, **kw)
        cfg = self.config
        self.app_id = str(cfg.get("feishu_app_id") or "").strip()
        self.app_secret = str(cfg.get("feishu_app_secret") or "").strip()
        self.verify_token = str(cfg.get("feishu_verification_token") or "").strip()
        self.bot_open_id = str(cfg.get("feishu_bot_open_id") or "").strip()
        self._token = TokenCache("飞书 tenant_access_token")

    # ---------- 入站 ----------
    def parse_inbound(self, method: str, path: str, query: dict,
                      headers: dict, body: bytes) -> Inbound:
        if method.upper() != "POST":
            return Inbound(status=405, error="飞书回调只接受 POST")
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except ValueError:
            return Inbound(status=400, error="请求体不是 JSON")
        if not isinstance(payload, dict):
            return Inbound(status=400, error="请求体不是对象")

        if "encrypt" in payload:
            return Inbound(status=400, error="飞书这边开了 Encrypt Key，标准库解不了 AES——"
                                             "请在事件订阅页把 Encrypt Key 清空")

        # URL 校验：飞书会先发一个 challenge，原样回给它
        if "challenge" in payload:
            got = str(payload.get("token") or "")
            if self.verify_token and not safe_equal(got, self.verify_token):
                return Inbound(status=403, error="verification token 不匹配")
            reply = json.dumps({"challenge": payload["challenge"]}, ensure_ascii=False)
            return Inbound(reply=("application/json; charset=utf-8", reply.encode("utf-8")))

        header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
        if self.verify_token:
            got = str(header.get("token") or payload.get("token") or "")
            if not safe_equal(got, self.verify_token):
                return Inbound(status=403, error="verification token 不匹配")

        etype = str(header.get("event_type") or "")
        if etype != "im.message.receive_v1":
            return Inbound(reply=("application/json", b'{"code":0}'))   # 其它事件直接放行

        event = payload.get("event") or {}
        msg = event.get("message") or {}
        sender = event.get("sender") or {}
        sid = sender.get("sender_id") or {}
        open_id = str(sid.get("open_id") or "")
        chat_id = str(msg.get("chat_id") or "")
        chat_type = str(msg.get("chat_type") or "p2p")
        if not open_id or not chat_id:
            return Inbound(reply=("application/json", b'{"code":0}'))

        text, mentioned = self._content(msg)
        if not text:
            return Inbound(reply=("application/json", b'{"code":0}'))

        is_group = chat_type != "p2p"
        segs = []
        if is_group and mentioned:
            segs.append({"type": "at", "data": {"qq": self.self_id or self.platform}})
        segs.append({"type": "text", "data": {"text": text}})
        ev = {
            "post_type": "message",
            "message_type": "group" if is_group else "private",
            "self_id": self.self_id,
            "user_id": open_id,
            "group_id": chat_id if is_group else "",
            "message_id": str(msg.get("message_id") or ""),
            "message": segs,
            "sender": {"user_id": open_id, "nickname": "", "card": "",
                       "role": "member"},
        }
        return Inbound(event=ev, reply=("application/json", b'{"code":0}'))

    def _content(self, msg: dict) -> tuple[str, bool]:
        """取出文本 + 是否 @ 了机器人。非文本消息返回空串（这一版不收图）。"""
        mtype = str(msg.get("message_type") or "")
        try:
            content = json.loads(msg.get("content") or "{}")
        except ValueError:
            content = {}
        if not isinstance(content, dict):
            return "", False
        if mtype != "text":
            return "", False
        text = str(content.get("text") or "")
        mentioned = False
        for m in (msg.get("mentions") or []):
            if not isinstance(m, dict):
                continue
            key = str(m.get("key") or "")
            mid = str((m.get("id") or {}).get("open_id") or "")
            if self.bot_open_id and mid:
                hit = safe_equal(mid, self.bot_open_id)
            else:
                hit = bool(key)          # 没配 open_id 时：只要有 @ 就算叫到它
            if hit:
                mentioned = True
                text = text.replace(key, "")
        return text.strip(), mentioned

    # ---------- 出站 ----------
    def _access_token(self) -> str:
        if not (self.app_id and self.app_secret):
            raise AdapterError("飞书没配 App ID / App Secret")

        def fetch():
            data = request_json(f"{API}/auth/v3/tenant_access_token/internal",
                                payload={"app_id": self.app_id,
                                         "app_secret": self.app_secret})
            if int(data.get("code") or 0) != 0:
                raise AdapterError(f"飞书取 token 失败：{data.get('msg') or data}")
            return str(data.get("tenant_access_token") or ""), float(data.get("expire") or 6000)

        return self._token.get(fetch)

    @staticmethod
    def _split(chat_id: str) -> tuple[str, str]:
        kind, _, target = str(chat_id or "").partition(":")
        kind = kind.strip().lower()
        if kind not in ("group", "private") or not target.strip():
            raise AdapterError(f"飞书 chat_id 形态不对：{chat_id!r}（要 group:<chat_id> / private:<open_id>）")
        return ("chat_id" if kind == "group" else "open_id"), target.strip()

    def send_text(self, chat_id: str, text: str, *,
                  reply_to: Optional[str] = None,
                  at_uid: Optional[str] = None) -> Any:
        rid_type, rid = self._split(chat_id)
        token = self._access_token()
        body = {"receive_id": rid, "msg_type": "text",
                "content": json.dumps({"text": str(text)}, ensure_ascii=False)}
        if reply_to:
            body["uuid"] = str(reply_to)[:50]
        try:
            data = request_json(f"{API}/im/v1/messages?receive_id_type={rid_type}",
                                payload=body, headers={"Authorization": f"Bearer {token}"})
        except HttpError as e:
            raise AdapterError(f"飞书发送失败：{e}") from e
        if int(data.get("code") or 0) != 0:
            raise AdapterError(f"飞书发送失败：{data.get('msg') or data}")
        mid = str(((data.get("data") or {}).get("message_id")) or "")
        return {"ok": True, "message_id": mid, "platform": self.platform}

    # ---------- 自检 ----------
    def health(self) -> dict:
        if not (self.app_id and self.app_secret):
            return {"ok": False, "platform": self.platform,
                    "error": "还没填 App ID / App Secret"}
        try:
            self._access_token()
            return {"ok": True, "platform": self.platform, "app_id": self.app_id,
                    "ingress": self.INGRESS, **self._token.peek()}
        except Exception as e:                                    # noqa: BLE001
            return {"ok": False, "platform": self.platform,
                    "error": f"{type(e).__name__}: {e}"}

    def describe(self) -> dict:
        d = super().describe()
        d.update({"app_id": self.app_id, "has_secret": bool(self.app_secret),
                  "verification_token": bool(self.verify_token)})
        return d

    def reconfigure(self, config: dict) -> bool:
        super().reconfigure(config)
        old = (self.app_id, self.app_secret)
        self.app_id = str(self.config.get("feishu_app_id") or "").strip()
        self.app_secret = str(self.config.get("feishu_app_secret") or "").strip()
        self.verify_token = str(self.config.get("feishu_verification_token") or "").strip()
        self.bot_open_id = str(self.config.get("feishu_bot_open_id") or "").strip()
        if old != (self.app_id, self.app_secret):
            self._token.clear()               # 换了应用就得换 token
        return True
