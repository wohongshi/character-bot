"""QQ 官方机器人适配器（QQ 开放平台 / QQBot）。

和 OneBot 11 的区别：这是腾讯**官方**的机器人接口，不需要挂协议端（NapCat），
但能力受平台限制（不能读群成员、不能撤回别人的消息、主动消息有配额）。

入站：平台的 Webhook 推送到 `/qqofficial/event`。
      第一次配置会先发 `op:13`（Webhook 校验），要用 Ed25519 对
      `event_ts + plain_token` 签名回给它；之后每条事件都带着
      `X-Signature-Ed25519` 头，同样验签。
出站：`getAppAccessToken` 拿 access_token，再调
      `/v2/groups/{group_openid}/messages` 或 `/v2/users/{openid}/messages`。

配置（state/bot_config.json，扁平存放）：

    qq_app_id       QQ 开放平台 → 机器人资料里的 AppID
    qq_app_secret   AppSecret
    qq_bot_secret   「机器人密钥」/ BotSecret —— Ed25519 签名的种子（32 字节）
    qq_sandbox      true = 走沙箱环境

QQ 有两条限制必须知道：
1. **被动回复**要带 `msg_id`（5 分钟内有效），这里会自动带最近一条；
   没有 msg_id 时属于「主动消息」，只在有配额时能发。
2. 官方接口**不提供**群成员列表 / 撤回 / 审批，`CAPABILITIES` 里都是 false。
"""
from __future__ import annotations

import json
from typing import Any, Optional

from .base import AdapterError, BaseAdapter, Inbound, register
from .http import HttpError, TokenCache, header_lookup, parse_qs, request_json

TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
API_PROD = "https://api.sgroup.qq.com"
API_SANDBOX = "https://sandbox.api.sgroup.qq.com"

# 这两类事件带 @ 语义（群里只有被 @ 才推过来）
_GROUP_EVENTS = ("GROUP_AT_MESSAGE_CREATE", "GROUP_ADD_ROBOT")
_C2C_EVENTS = ("C2C_MESSAGE_CREATE", "FRIEND_ADD")


@register
class QQOfficialAdapter(BaseAdapter):
    """QQ 开放平台的官方机器人（QQBot）接口。"""

    platform = "qqofficial"
    title = "QQ 官方机器人"
    DESCRIPTION = "QQ 开放平台官方接口：群里 @ 它、私聊都能收发（无需协议端）"
    VERSION = "1.0"
    INGRESS = "/qqofficial/event"
    CAPABILITIES = {
        "text": True, "image": False, "voice": False,
        "group_member_list": False, "message_lookup": False,
        "request_approval": False, "recall": False,
    }
    CONFIG_SCHEMA = {
        "qq_app_id": {"type": "str", "label": "AppID", "default": "",
                      "description": "QQ 开放平台 → 机器人资料"},
        "qq_app_secret": {"type": "str", "label": "AppSecret", "default": "",
                          "secret": True, "description": "同一页"},
        "qq_bot_secret": {"type": "str", "label": "机器人密钥（BotSecret）",
                          "default": "", "secret": True,
                          "description": "Ed25519 校验用，32 字节"},
        "qq_sandbox": {"type": "bool", "label": "沙箱环境", "default": False,
                       "description": "刚创建的机器人可先用沙箱联调"},
    }

    def __init__(self, config: Optional[dict] = None, **kw) -> None:
        super().__init__(config, **kw)
        cfg = self.config
        self.app_id = str(cfg.get("qq_app_id") or "").strip()
        self.app_secret = str(cfg.get("qq_app_secret") or "").strip()
        self.bot_secret = str(cfg.get("qq_bot_secret") or "").strip()
        self.sandbox = bool(cfg.get("qq_sandbox"))
        self._token = TokenCache("QQ 官方 access_token", ttl_default=7200)
        self._seq: dict = {}          # chat_id → 被动回复序号（同一 msg_id 最多回 5 条）

    # ---------- 签名 ----------
    def _seed(self) -> bytes:
        """BotSecret → 32 字节 Ed25519 seed（不足补零，超长截断）。"""
        raw = self.bot_secret.encode("utf-8")
        if not raw:
            raise AdapterError("QQ 官方机器人没配 BotSecret，无法验签")
        return (raw + b"\0" * 32)[:32]

    def _public_key(self) -> bytes:
        from bot import ed25519
        return ed25519.public_key(self._seed())

    def sign_challenge(self, plain_token: str, event_ts: str) -> str:
        """op 13 的应答：对 `event_ts + plain_token` 签名，回十六进制。"""
        from bot import ed25519
        return ed25519.sign(self._seed(), f"{event_ts}{plain_token}".encode("utf-8")).hex()

    def verify_signature(self, signature_hex: str, timestamp: str, body: bytes) -> bool:
        from bot import ed25519
        try:
            sig = bytes.fromhex(str(signature_hex or ""))
        except ValueError:
            return False
        try:
            return ed25519.verify(self._public_key(), f"{timestamp}".encode() + body, sig)
        except Exception:                                          # noqa: BLE001
            return False

    # ---------- 入站 ----------
    def parse_inbound(self, method: str, path: str, query: dict,
                      headers: dict, body: bytes) -> Inbound:
        if method.upper() != "POST":
            return Inbound(status=405, error="QQ 官方回调只接受 POST")
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except ValueError:
            return Inbound(status=400, error="请求体不是 JSON")
        if not isinstance(payload, dict):
            return Inbound(status=400, error="请求体不是对象")

        op = payload.get("op")
        d = payload.get("d") if isinstance(payload.get("d"), dict) else {}

        if op == 13:                                   # Webhook 地址校验
            if not self.bot_secret:
                return Inbound(status=503, error="还没填 BotSecret，无法应答校验")
            token = str(d.get("plain_token") or "")
            ts = str(d.get("event_ts") or "")
            reply = json.dumps({"plain_token": token,
                                "signature": self.sign_challenge(token, ts)},
                               ensure_ascii=False)
            return Inbound(reply=("application/json; charset=utf-8", reply.encode("utf-8")))

        sig = header_lookup(headers, "X-Signature-Ed25519")
        ts = header_lookup(headers, "X-Signature-Timestamp")
        if self.bot_secret:
            if not (sig and ts):
                return Inbound(status=401, error="缺少签名头")
            if not self.verify_signature(sig, ts, body):
                return Inbound(status=401, error="Ed25519 验签失败")
        else:
            return Inbound(status=503, error="还没填 BotSecret，拒绝未经校验的事件")

        etype = str(payload.get("t") or "")
        if etype not in _GROUP_EVENTS + _C2C_EVENTS:
            return Inbound(reply=("application/json", b'{"op":12}'))   # HTTP 回调 ACK
        if etype in ("GROUP_ADD_ROBOT", "FRIEND_ADD"):
            return Inbound(reply=("application/json", b'{"op":12}'))

        content = str(d.get("content") or "").strip()
        author = d.get("author") if isinstance(d.get("author"), dict) else {}
        uid = str(author.get("user_openid") or author.get("id") or "")
        gid = str(d.get("group_openid") or "")
        if not content or not uid:
            return Inbound(reply=("application/json", b'{"op":12}'))

        is_group = etype in _GROUP_EVENTS
        segs = []
        if is_group:
            # 官方接口只在被 @ 时才推群消息，所以这条天然算「叫到它」
            segs.append({"type": "at", "data": {"qq": self.self_id or self.platform}})
        segs.append({"type": "text", "data": {"text": content}})
        ev = {
            "post_type": "message",
            "message_type": "group" if is_group else "private",
            "self_id": self.self_id,
            "user_id": uid,
            "group_id": gid if is_group else "",
            "message_id": str(d.get("id") or ""),
            "message": segs,
            "sender": {"user_id": uid, "nickname": "", "card": "", "role": "member"},
        }
        return Inbound(event=ev, reply=("application/json", b'{"op":12}'))

    # ---------- 出站 ----------
    def _base(self) -> str:
        return API_SANDBOX if self.sandbox else API_PROD

    def _access_token(self) -> str:
        if not (self.app_id and self.app_secret):
            raise AdapterError("QQ 官方机器人没配 AppID / AppSecret")

        def fetch():
            data = request_json(TOKEN_URL,
                                payload={"appId": self.app_id,
                                         "clientSecret": self.app_secret},
                                timeout=20)
            if not data.get("access_token"):
                raise AdapterError(f"QQ 取 token 失败：{data.get('message') or data}")
            return str(data["access_token"]), float(data.get("expires_in") or 7200)

        return self._token.get(fetch)

    def send_text(self, chat_id: str, text: str, *,
                  reply_to: Optional[str] = None,
                  at_uid: Optional[str] = None) -> Any:
        kind, _, target = str(chat_id or "").partition(":")
        kind = kind.strip().lower()
        target = target.strip()
        if kind not in ("group", "private") or not target:
            raise AdapterError(f"QQ 官方 chat_id 形态不对：{chat_id!r}")
        token = self._access_token()
        seq = self._seq.get(chat_id, 0) + 1
        self._seq[chat_id] = seq if seq <= 5 else 1
        body: dict = {"content": str(text), "msg_type": 0, "msg_seq": self._seq[chat_id]}
        if reply_to:                       # 被动回复：带 msg_id 才不消耗主动消息配额
            body["msg_id"] = str(reply_to)
        url = (f"{self._base()}/v2/groups/{target}/messages" if kind == "group"
               else f"{self._base()}/v2/users/{target}/messages")
        try:
            data = request_json(url, payload=body,
                                headers={"Authorization": f"QQBot {token}"}, timeout=20)
        except HttpError as e:
            raise AdapterError(f"QQ 官方发送失败：{e}") from e
        return {"ok": True, "message_id": str(data.get("id") or ""),
                "platform": self.platform}

    # ---------- 自检 ----------
    def health(self) -> dict:
        if not (self.app_id and self.app_secret and self.bot_secret):
            return {"ok": False, "platform": self.platform,
                    "error": "AppID / AppSecret / BotSecret 三者都要填"}
        try:
            self._access_token()
            return {"ok": True, "platform": self.platform,
                    "app_id": self.app_id, "sandbox": self.sandbox,
                    "ingress": self.INGRESS, **self._token.peek()}
        except Exception as e:                                    # noqa: BLE001
            return {"ok": False, "platform": self.platform,
                    "error": f"{type(e).__name__}: {e}"}

    def describe(self) -> dict:
        d = super().describe()
        d.update({"app_id": self.app_id, "has_secret": bool(self.app_secret),
                  "has_bot_secret": bool(self.bot_secret), "sandbox": self.sandbox})
        return d

    def reconfigure(self, config: dict) -> bool:
        super().reconfigure(config)
        old = (self.app_id, self.app_secret)
        self.app_id = str(self.config.get("qq_app_id") or "").strip()
        self.app_secret = str(self.config.get("qq_app_secret") or "").strip()
        self.bot_secret = str(self.config.get("qq_bot_secret") or "").strip()
        self.sandbox = bool(self.config.get("qq_sandbox"))
        if old != (self.app_id, self.app_secret):
            self._token.clear()
        return True
