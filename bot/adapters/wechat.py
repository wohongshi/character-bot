"""微信公众号适配器（服务号 / 订阅号）。

入站：微信把消息以 XML POST 到 `/wechat/event`；首次配置会先发 GET 做签名校验。
出站：客服消息接口 `cgi-bin/message/custom/send`（需要已认证的服务号）。

配置（state/bot_config.json，扁平存放）：

    wechat_app_id        公众号 AppID
    wechat_app_secret    公众号 AppSecret
    wechat_token         服务器配置里的 Token（必填，用来验签）

**为什么不用「被动回复」**：被动回复要求 5 秒内把回复塞进 HTTP 响应体里，
而这个框架走的是「模型生成 → 适配器发送」，生成经常超过 5 秒。所以入站一律
立刻返回空串（微信视为「不回复」），随后用客服消息主动发。代价是：
**未认证的订阅号发不出消息**（客服消息接口不可用），这是平台限制，不是 bug。

**不支持安全模式（AES）**：标准库没有 AES。请在公众号后台「服务器配置」里
把消息加解密方式选成「明文模式」。
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from typing import Any, Optional

from .base import AdapterError, BaseAdapter, Inbound, register
from .http import HttpError, TokenCache, parse_qs, request_json, safe_equal, sha1_signature

API = "https://api.weixin.qq.com"
MAX_BODY = 256 * 1024


@register
class WeChatAdapter(BaseAdapter):
    """微信公众号：关注者发消息过来，用客服消息接口回。"""

    platform = "wechat"
    title = "微信公众号"
    DESCRIPTION = "公众号消息：明文模式 + 客服消息回复（需认证服务号）"
    VERSION = "1.0"
    INGRESS = "/wechat/event"
    CAPABILITIES = {
        "text": True, "image": False, "voice": False,
        "group_member_list": False, "message_lookup": False,
        "request_approval": False, "recall": False,
    }
    CONFIG_SCHEMA = {
        "wechat_app_id": {"type": "str", "label": "AppID", "default": "",
                          "description": "公众号后台 → 设置与开发 → 基本配置"},
        "wechat_app_secret": {"type": "str", "label": "AppSecret", "default": "",
                              "secret": True, "description": "同上"},
        "wechat_token": {"type": "str", "label": "服务器 Token", "default": "",
                         "description": "服务器配置里自己填的 Token，用于验签"},
    }

    def __init__(self, config: Optional[dict] = None, **kw) -> None:
        super().__init__(config, **kw)
        cfg = self.config
        self.app_id = str(cfg.get("wechat_app_id") or "").strip()
        self.app_secret = str(cfg.get("wechat_app_secret") or "").strip()
        self.token = str(cfg.get("wechat_token") or "").strip()
        self._token = TokenCache("微信 access_token", ttl_default=7000)

    # ---------- 入站 ----------
    def _signature_ok(self, q: dict) -> bool:
        if not self.token:
            return False
        want = sha1_signature(self.token, q.get("timestamp", ""), q.get("nonce", ""))
        return safe_equal(q.get("signature", ""), want)

    def parse_inbound(self, method: str, path: str, query: dict,
                      headers: dict, body: bytes) -> Inbound:
        q = parse_qs(query)
        m = method.upper()
        if m == "GET":                                    # 后台点「提交」时的校验
            if not self.token:
                return Inbound(status=503, error="还没填服务器 Token")
            if not self._signature_ok(q):
                return Inbound(status=403, error="签名不匹配")
            return Inbound(reply=("text/plain; charset=utf-8",
                                  str(q.get("echostr") or "").encode("utf-8")))
        if m != "POST":
            return Inbound(status=405, error="微信回调只接受 GET/POST")
        if len(body) > MAX_BODY:
            return Inbound(status=413, error="消息体过大")
        if self.token and not self._signature_ok(q):
            return Inbound(status=403, error="签名不匹配")

        try:
            root = ET.fromstring(body)                    # 微信的 XML
        except ET.ParseError:
            return Inbound(status=400, error="不是合法 XML")

        def val(tag: str) -> str:
            node = root.find(tag)
            return (node.text or "").strip() if node is not None else ""

        msg_type = val("MsgType")
        from_user = val("FromUserName")
        if not from_user:
            return Inbound(reply=("text/plain", b""))
        if msg_type == "text":
            text = val("Content")
        elif msg_type == "voice":
            text = val("Recognition") or "[语音]"
        elif msg_type in ("image", "video", "shortvideo"):
            text = "[图片]" if msg_type == "image" else "[视频]"
        elif msg_type == "event":
            ev_name = val("Event")
            if ev_name == "subscribe":
                text = ""                                 # 关注事件：不主动搭话
            else:
                text = ""
        else:
            text = ""

        # 加密模式的 XML 会带 Encrypt 节点、没有 Content：明确告诉使用者怎么改
        if not text and root.find("Encrypt") is not None:
            return Inbound(status=400, error="公众号开了安全模式（AES），请改成明文模式")

        if not text:
            return Inbound(reply=("text/plain", b""))

        ev = {
            "post_type": "message",
            "message_type": "private",                    # 公众号是 1:1
            "self_id": self.self_id,
            "user_id": from_user,
            "group_id": "",
            "message_id": val("MsgId") or f"wx-{int(time.time())}",
            "message": [{"type": "text", "data": {"text": text}}],
            "sender": {"user_id": from_user, "nickname": "", "card": "",
                       "role": "member"},
        }
        # 微信要求 5 秒内响应；回空串 = 「这次不被动回复」，随后用客服消息发
        return Inbound(event=ev, reply=("text/plain", b""))

    # ---------- 出站 ----------
    def _access_token(self) -> str:
        if not (self.app_id and self.app_secret):
            raise AdapterError("微信没配 AppID / AppSecret")

        def fetch():
            data = request_json(
                f"{API}/cgi-bin/token?grant_type=client_credential"
                f"&appid={self.app_id}&secret={self.app_secret}",
                method="GET", timeout=15)
            if int(data.get("errcode") or 0) != 0:
                raise AdapterError(f"微信取 token 失败：{data.get('errmsg') or data}")
            return str(data.get("access_token") or ""), float(data.get("expires_in") or 7200)

        return self._token.get(fetch)

    def send_text(self, chat_id: str, text: str, *,
                  reply_to: Optional[str] = None,
                  at_uid: Optional[str] = None) -> Any:
        kind, _, target = str(chat_id or "").partition(":")
        openid = target.strip() if kind in ("private", "group") else str(chat_id or "").strip()
        if not openid:
            raise AdapterError(f"微信 chat_id 形态不对：{chat_id!r}")
        token = self._access_token()
        try:
            data = request_json(
                f"{API}/cgi-bin/message/custom/send?access_token={token}",
                payload={"touser": openid, "msgtype": "text",
                         "text": {"content": str(text)}},
                timeout=20)
        except HttpError as e:
            raise AdapterError(f"微信发送失败：{e}") from e
        code = int(data.get("errcode") or 0)
        if code != 0:
            raise AdapterError(f"微信发送失败：{data.get('errmsg') or data}"
                               "（未认证订阅号没有客服消息权限）")
        return {"ok": True, "message_id": "", "platform": self.platform}

    # ---------- 自检 ----------
    def health(self) -> dict:
        if not self.token:
            return {"ok": False, "platform": self.platform,
                    "error": "还没填服务器 Token（验签用）"}
        if not (self.app_id and self.app_secret):
            return {"ok": False, "platform": self.platform,
                    "error": "还没填 AppID / AppSecret（出站要用）"}
        try:
            self._access_token()
            return {"ok": True, "platform": self.platform, "ingress": self.INGRESS,
                    **self._token.peek()}
        except Exception as e:                                    # noqa: BLE001
            return {"ok": False, "platform": self.platform,
                    "error": f"{type(e).__name__}: {e}"}

    def describe(self) -> dict:
        d = super().describe()
        d.update({"app_id": self.app_id, "has_secret": bool(self.app_secret),
                  "has_token": bool(self.token)})
        return d

    def reconfigure(self, config: dict) -> bool:
        super().reconfigure(config)
        old = (self.app_id, self.app_secret)
        self.app_id = str(self.config.get("wechat_app_id") or "").strip()
        self.app_secret = str(self.config.get("wechat_app_secret") or "").strip()
        self.token = str(self.config.get("wechat_token") or "").strip()
        if old != (self.app_id, self.app_secret):
            self._token.clear()
        return True
