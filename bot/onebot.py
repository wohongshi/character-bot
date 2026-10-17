"""OneBot 11 HTTP 客户端（NapCat HTTP 模式）—— 纯标准库。

设计：NapCat 开一个 HTTP 服务（默认 127.0.0.1:3101）+ 把事件 HTTP POST 到
studio 的 /onebot/event；studio 用这里的 call() 回消息/查成员。
"""
from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request


class OneBotError(RuntimeError):
    """OneBot 调用失败（网络 / retcode != 0）。"""


class OneBotHTTP:
    def __init__(self, base: str = "http://127.0.0.1:3101", token: str = ""):
        self.base = (base or "http://127.0.0.1:3101").rstrip("/")
        self.token = token or ""

    # ---------- 底层 ----------
    def call(self, action: str, timeout: float = 20, **params):
        body = json.dumps(params, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(f"{self.base}/{action}", data=body, headers=headers,
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310
                raw = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:200]
            except (OSError, AttributeError, http.client.HTTPException):
                pass
            raise OneBotError(f"{action} HTTP {e.code}：{detail or e.reason}") from e
        except (OSError, http.client.HTTPException) as e:
            raise OneBotError(f"{action} 网络失败：{type(e).__name__}: {e}") from e
        if not raw.strip():
            raise OneBotError(f"{action} 返回空响应（NapCat 可能没处理这个 action）")
        try:
            data = json.loads(raw)
        except ValueError as e:
            raise OneBotError(f"{action} 返回非 JSON：{raw[:120]}") from e
        if not isinstance(data, dict):
            raise OneBotError(f"{action} 返回格式异常：{str(data)[:120]}")
        if data.get("status") == "failed" or data.get("retcode", 0) != 0:
            raise OneBotError(f"{action} 返回错误：{data}")
        return data.get("data")

    # ---------- 请求处理（好友/入群，必须主人手动触发） ----------
    def set_friend_add_request(self, flag: str, approve: bool = False, remark: str = ""):
        return self.call("set_friend_add_request", flag=str(flag),
                         approve=bool(approve), remark=str(remark or ""))

    def set_group_add_request(self, flag: str, sub_type: str, approve: bool = False,
                              reason: str = ""):
        return self.call("set_group_add_request", flag=str(flag),
                         sub_type=str(sub_type or "add"), approve=bool(approve),
                         reason=str(reason or ""))

    # ---------- 发消息 ----------
    @staticmethod
    def _split_chat_id(chat_id: str) -> tuple[str, str]:
        raw = str(chat_id or "").strip()
        kind, sep, target = raw.partition(":")
        kind = kind.strip().lower()
        if not sep:
            kind, target = "private", kind          # 兼容裸数字（当私聊）
        target = target.strip()
        if (kind not in ("group", "private") or not target.isascii()
                or not target.isdigit() or int(target) <= 0):
            raise OneBotError(f"非法 chat_id：{chat_id!r}（期望 'group:<id>' / 'private:<id>'）")
        return kind, target

    def send(self, chat_id: str, message, *, reply_to: str | None = None,
             auto_escape: bool = False) -> dict:
        kind, target = self._split_chat_id(chat_id)
        segs = message if isinstance(message, list) else [{"type": "text", "data": {"text": str(message)}}]
        if reply_to:
            segs = [{"type": "reply", "data": {"id": str(reply_to)}}] + list(segs)
        if kind == "group":
            return self.call("send_group_msg", group_id=int(target or 0), message=segs,
                             auto_escape=auto_escape) or {}
        return self.call("send_private_msg", user_id=int(target or 0), message=segs,
                         auto_escape=auto_escape) or {}

    def send_text(self, chat_id: str, text: str, *, reply_to: str | None = None,
                  at_uid: str | None = None) -> dict:
        """发文本；可选引用（reply 段）与 @（at 段，群聊用）。"""
        segs = []
        if reply_to:
            segs.append({"type": "reply", "data": {"id": str(reply_to)}})
        if at_uid:
            segs.append({"type": "at", "data": {"qq": str(at_uid)}})
            segs.append({"type": "text", "data": {"text": " "}})
        segs.append({"type": "text", "data": {"text": text}})
        return self.send(chat_id, segs)

    def send_image(self, chat_id: str, path: str, caption: str | None = None, *,
                   reply_to: str | None = None, at_uid: str | None = None) -> dict:
        """本地图片：base64 上传（不依赖 NapCat 的本地文件开关）。"""
        import base64
        from pathlib import Path
        p = Path(path)
        try:
            size = p.stat().st_size
        except OSError as e:
            raise OneBotError(f"读取图片失败：{p}: {type(e).__name__}: {e}") from e
        if size > 20 * 1024 * 1024:
            raise OneBotError(f"图片过大（{size} 字节），拒绝 base64 上传：{p}")
        try:
            raw = p.read_bytes()
        except OSError as e:
            raise OneBotError(f"读取图片失败：{p}: {type(e).__name__}: {e}") from e
        if len(raw) > 20 * 1024 * 1024:
            raise OneBotError(f"图片过大（{len(raw)} 字节），拒绝 base64 上传：{p}")
        b64 = base64.b64encode(raw).decode("ascii")
        segs = []
        if reply_to:
            segs.append({"type": "reply", "data": {"id": str(reply_to)}})
        if at_uid:
            segs.append({"type": "at", "data": {"qq": str(at_uid)}})
            segs.append({"type": "text", "data": {"text": " "}})
        segs.append({"type": "image", "data": {"file": f"base64://{b64}", "sub_type": 0}})
        if caption:
            segs.append({"type": "text", "data": {"text": str(caption)}})
        return self.send(chat_id, segs)

    MAX_VOICE_BYTES = 5 * 1024 * 1024

    def send_voice(self, chat_id: str, path: str, *, reply_to: str | None = None) -> dict:
        """发语音（record 段，base64 上传）。"""
        from pathlib import Path
        p = Path(path)
        try:
            if p.stat().st_size > self.MAX_VOICE_BYTES:
                raise OneBotError(f"语音过大（{p.stat().st_size} 字节，上限 "
                                  f"{self.MAX_VOICE_BYTES}）：{p}")
        except OSError as e:
            raise OneBotError(f"读取语音失败：{p}: {type(e).__name__}: {e}") from e
        try:
            size = p.stat().st_size
        except OSError as e:
            raise OneBotError(f"读取语音失败：{p}: {type(e).__name__}: {e}") from e
        if size > 10 * 1024 * 1024:
            raise OneBotError(f"语音过大（{size} 字节）：{p}")
        try:
            raw = p.read_bytes()
        except OSError as e:
            raise OneBotError(f"读取语音失败：{p}: {type(e).__name__}: {e}") from e
        if len(raw) > 10 * 1024 * 1024:
            raise OneBotError(f"语音过大（{len(raw)} 字节）：{p}")
        import base64
        segs = []
        if reply_to:
            segs.append({"type": "reply", "data": {"id": str(reply_to)}})
        segs.append({"type": "record", "data": {"file": "base64://" + base64.b64encode(raw).decode("ascii")}})
        return self.send(chat_id, segs)

    # ---------- 查询 ----------
    def get_group_member_list(self, group_id: str) -> list:
        try:
            gid = int(str(group_id).strip())
        except (TypeError, ValueError) as e:
            raise OneBotError(f"非法 group_id：{group_id!r}") from e
        data = self.call("get_group_member_list", group_id=gid, timeout=15)
        return data if isinstance(data, list) else []

    def get_msg(self, message_id: str) -> dict:
        try:
            mid = int(str(message_id).strip())
        except (TypeError, ValueError) as e:
            raise OneBotError(f"非法 message_id：{message_id!r}") from e
        data = self.call("get_msg", message_id=mid, timeout=10)
        return data if isinstance(data, dict) else {}

    def get_image(self, file: str) -> dict:
        """取图片信息（NapCat 回 {file, url, file_size, file_unique}）。

        `enableLocalFile2Url` 开着时这里会带 http url；关着时通常是本地路径/base64。
        """
        data = self.call("get_image", file=str(file or ""), timeout=15)
        return data if isinstance(data, dict) else {}

    def get_login_info(self) -> dict:
        data = self.call("get_login_info", timeout=8)
        return data if isinstance(data, dict) else {}

    def get_status(self) -> dict:
        data = self.call("get_status", timeout=8)
        return data if isinstance(data, dict) else {}
