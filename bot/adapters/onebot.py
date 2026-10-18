"""OneBot 11 适配器：NapCat / Lagrange / go-cqhttp 都走这套 HTTP 接口。

这是本项目原本唯一的实现，现在只做一层「把 OneBotHTTP 的方法名对齐到框架协议」
的包装 —— 传输逻辑（base64 上传、段构造、错误码解析）全部留在 `bot/onebot.py`，
这样老代码和测试都不用动。

配置项（state/bot_config.json）：

    "adapter":       "onebot11"          # 选中本适配器
    "napcat_http":   "http://127.0.0.1:3101"
    "napcat_token":  "…"
"""
from __future__ import annotations

from typing import Any, Optional

from bot.onebot import OneBotError, OneBotHTTP

from .base import AdapterError, BaseAdapter, register


@register
class OneBotAdapter(BaseAdapter):
    """OneBot 11（HTTP）：QQ 侧最常用的一套协议。"""

    platform = "onebot11"
    title = "OneBot 11（QQ）"
    CAPABILITIES = {
        "text": True, "image": True, "voice": True,
        "group_member_list": True, "message_lookup": True,
        "request_approval": True, "recall": True,
    }

    def __init__(self, config: Optional[dict] = None, **kw) -> None:
        super().__init__(config, **kw)
        self.base = str(self.config.get("napcat_http") or "http://127.0.0.1:3101")
        self.token = str(self.config.get("napcat_token") or "")
        self.raw = OneBotHTTP(self.base, self.token)

    # ---------- 传输 ----------
    def _wrap(self, fn, *a, **kw):
        """把 OneBotError 统一成 AdapterError，上层只认一种异常。"""
        try:
            return fn(*a, **kw)
        except OneBotError as e:
            raise AdapterError(str(e)) from e

    def send_text(self, chat_id: str, text: str, *,
                  reply_to: Optional[str] = None,
                  at_uid: Optional[str] = None) -> Any:
        return self._wrap(self.raw.send_text, chat_id, text,
                          reply_to=reply_to, at_uid=at_uid)

    def send_image(self, chat_id: str, path: str, caption: Optional[str] = None, *,
                   reply_to: Optional[str] = None,
                   at_uid: Optional[str] = None) -> Any:
        return self._wrap(self.raw.send_image, chat_id, str(path), caption,
                          reply_to=reply_to, at_uid=at_uid)

    def send_voice(self, chat_id: str, path: str, *,
                   reply_to: Optional[str] = None) -> Any:
        return self._wrap(self.raw.send_voice, chat_id, str(path), reply_to=reply_to)

    def send_raw(self, chat_id: str, segments: Any, **kw) -> Any:
        """发原始消息段（插件里要拼 at/reply/image 混合段时用）。"""
        return self._wrap(self.raw.send, chat_id, segments, **kw)

    # ---------- 查询 ----------
    def get_group_member_list(self, group_id: str) -> list:
        return self._wrap(self.raw.get_group_member_list, str(group_id))

    def get_msg(self, message_id: str) -> dict:
        return self._wrap(self.raw.get_msg, str(message_id))

    def get_image(self, file: str) -> dict:
        return self._wrap(self.raw.get_image, str(file))

    def get_login_info(self) -> dict:
        return self._wrap(self.raw.get_login_info)

    # ---------- 审批 ----------
    def set_friend_add_request(self, flag: str, approve: bool = False,
                               remark: str = "") -> Any:
        return self._wrap(self.raw.set_friend_add_request, flag, approve, remark)

    def set_group_add_request(self, flag: str, sub_type: str = "add",
                              approve: bool = False, reason: str = "") -> Any:
        return self._wrap(self.raw.set_group_add_request, flag, sub_type, approve, reason)

    # ---------- 自检 / 配置 ----------
    def health(self) -> dict:
        try:
            st = self.raw.get_status()
            ok = bool(st)
            return {"ok": ok, "platform": self.platform, "base": self.base,
                    "online": (st or {}).get("online"),
                    "detail": "" if ok else "get_status 返回空"}
        except Exception as e:                                    # noqa: BLE001
            return {"ok": False, "platform": self.platform, "base": self.base,
                    "error": f"{type(e).__name__}: {e}"}

    def describe(self) -> dict:
        d = super().describe()
        d.update({"base": self.base, "has_token": bool(self.token)})
        return d

    def reconfigure(self, config: dict) -> bool:
        super().reconfigure(config)
        base = str(self.config.get("napcat_http") or self.base).rstrip("/")
        token = str(self.config.get("napcat_token") or "")
        if base != self.base or token != self.token:
            self.base, self.token = base, token
            self.raw = OneBotHTTP(base, token)
        return True
