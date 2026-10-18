"""适配器层：把「平台怎么收发消息」和「机器人怎么思考」分开。

框架视角下，一个 bot 由三块拼成：

    ┌──────────────┐   Event    ┌──────────────┐   reply()   ┌──────────────┐
    │   Adapter    │ ─────────► │   Runtime    │ ──────────► │   Adapter    │
    │ (QQ/TG/…)    │            │ 插件/LLM/知识库 │             │  (发回去)     │
    └──────────────┘            └──────────────┘             └──────────────┘

`Adapter` 只负责传输：发文本、发图、发语音、拉群成员、取消息、审批请求。
拆分、限流、历史、人格、知识库、插件分发 —— 全是 Runtime 的事，适配器不碰。

新增一个平台 = 新建一个文件，继承 `BaseAdapter`，标上 `platform = "xxx"`，
框架启动时会自动注册；面板/配置里把 `adapter` 改成这个值即可。

为什么方法全是**同步**的：老代码（OneBotHTTP）是 urllib 阻塞调用，框架用
`asyncio.to_thread` 包起来跑。适配器作者如果更喜欢 async，也可以写成
`async def` —— `maybe_await()` 两种都认，不用为了适配框架改签名。
"""
from __future__ import annotations

import inspect
from typing import Any, Dict, Optional

__all__ = ["AdapterError", "BaseAdapter", "REGISTRY", "register", "build",
           "available", "maybe_await"]


class AdapterError(RuntimeError):
    """适配器层面的错误（连不上 / 平台不支持这个能力 / 参数非法）。"""


#: platform 名 → 适配器类。子模块 import 时自动填。
REGISTRY: Dict[str, type["BaseAdapter"]] = {}


def register(cls: type["BaseAdapter"]) -> type["BaseAdapter"]:
    """类装饰器：把适配器登记进 REGISTRY。"""
    platform = str(getattr(cls, "platform", "") or "").strip().lower()
    if not platform:
        raise AdapterError(f"{cls.__name__} 没有声明 platform")
    REGISTRY[platform] = cls
    return cls


def available() -> list:
    """所有已注册的适配器（面板用来出下拉）。"""
    out = []
    for pid, cls in sorted(REGISTRY.items()):
        out.append({"id": pid, "name": getattr(cls, "title", pid),
                    "description": (cls.__doc__ or "").strip().splitlines()[0]
                    if cls.__doc__ else ""})
    return out


async def maybe_await(value: Any) -> Any:
    """同步返回值 / awaitable 都能过。适配器用哪种写法都行。"""
    if inspect.isawaitable(value):
        return await value
    return value


class BaseAdapter:
    """传输层基类。只实现能支持的动词，其余保持默认（抛 AdapterError）。

    子类至少要实现 `send_text`，否则收到消息也回不了。
    """

    #: 配置里 `adapter` 字段的取值，也是插件里判断平台的依据
    platform: str = "base"
    #: 面板显示名
    title: str = "Base"
    #: 能力声明（面板展示 + 插件可用它做降级判断）
    CAPABILITIES: Dict[str, bool] = {
        "text": True, "image": False, "voice": False,
        "group_member_list": False, "message_lookup": False,
        "request_approval": False, "recall": False,
    }

    def __init__(self, config: Optional[dict] = None, *,
                 owner_id: str = "", self_id: str = "") -> None:
        self.config = dict(config or {})
        self.owner_id = str(owner_id or self.config.get("owner_id") or "")
        self.self_id = str(self_id or self.config.get("self_id") or "")

    # ---------- 会话标识 ----------
    def chat_id_of(self, is_group: bool, group_id: str, user_id: str) -> str:
        """会话唯一 ID。默认 `group:<id>` / `private:<id>`（OneBot 风格）。

        换成 Telegram 之类需要别的形态时覆盖它 —— 但**框架只要求返回值稳定**，
        而且能由你自己解析回来（你还要靠它发消息）。
        """
        return f"group:{group_id}" if is_group else f"private:{user_id}"

    # ---------- 必须实现 ----------
    def send_text(self, chat_id: str, text: str, *,
                  reply_to: Optional[str] = None,
                  at_uid: Optional[str] = None) -> Any:
        raise AdapterError(f"{self.platform} 不支持发文本")

    # ---------- 可选能力（不支持就抛，框架/插件按 CAPABILITIES 降级） ----------
    def send_image(self, chat_id: str, path: str, caption: Optional[str] = None, *,
                   reply_to: Optional[str] = None,
                   at_uid: Optional[str] = None) -> Any:
        raise AdapterError(f"{self.platform} 不支持发图片")

    def send_voice(self, chat_id: str, path: str, *,
                   reply_to: Optional[str] = None) -> Any:
        raise AdapterError(f"{self.platform} 不支持发语音")

    def get_group_member_list(self, group_id: str) -> list:
        raise AdapterError(f"{self.platform} 拿不到群成员")

    def get_msg(self, message_id: str) -> dict:
        raise AdapterError(f"{self.platform} 查不了历史消息")

    def get_image(self, file: str) -> dict:
        raise AdapterError(f"{self.platform} 取不了图片")

    def set_friend_add_request(self, flag: str, approve: bool = False,
                               remark: str = "") -> Any:
        raise AdapterError(f"{self.platform} 不支持好友请求审批")

    def set_group_add_request(self, flag: str, sub_type: str = "add",
                              approve: bool = False, reason: str = "") -> Any:
        raise AdapterError(f"{self.platform} 不支持入群请求审批")

    # ---------- 生命周期 / 自检 ----------
    def health(self) -> dict:
        """给面板「运维」页用。连不上别抛，返回 ok=False + 原因。"""
        return {"ok": True, "platform": self.platform}

    def describe(self) -> dict:
        return {"platform": self.platform, "title": self.title,
                "owner_id": self.owner_id, "self_id": self.self_id,
                "capabilities": dict(self.CAPABILITIES)}

    def reconfigure(self, config: dict) -> bool:
        """配置热更新。返回 True 表示连的是同一个目标、已就地生效。"""
        self.config = {**self.config, **(config or {})}
        self.owner_id = str(self.config.get("owner_id") or self.owner_id or "")
        self.self_id = str(self.config.get("self_id") or self.self_id or "")
        return True

    def close(self) -> None:
        pass

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} platform={self.platform}>"


def build(config: Optional[dict] = None, **kw) -> BaseAdapter:
    """按配置造适配器。`adapter` 缺省 = onebot11（向后兼容老配置）。"""
    cfg = dict(config or {})
    pid = str(cfg.get("adapter") or "onebot11").strip().lower()
    cls = REGISTRY.get(pid)
    if cls is None:
        known = ", ".join(sorted(REGISTRY)) or "（无）"
        raise AdapterError(f"没有这个适配器：{pid!r}（可用：{known}）")
    return cls(cfg, **kw)

