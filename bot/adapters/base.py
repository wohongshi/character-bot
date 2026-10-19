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

I/O 动词写成**同步**或 `async def` 都行：运行时统一走 `_adapter_call()` ——
同步实现丢进 `asyncio.to_thread`（老代码 OneBotHTTP 是 urllib 阻塞调用，
不能占住事件循环），协程实现直接 await。

例外：`describe()` / `health()` / `reconfigure()` 必须是**同步**的 ——
它们是元数据，不该做 I/O；写成 async 的话面板只会显示一条「必须是同步方法」。
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

__all__ = ["AdapterError", "BaseAdapter", "Inbound", "REGISTRY", "register",
           "build", "available", "system_plugins", "maybe_await"]


class AdapterError(RuntimeError):
    """适配器层面的错误（连不上 / 平台不支持这个能力 / 参数非法）。"""


#: platform 名 → 适配器类。子模块 import 时自动填。
REGISTRY: Dict[str, type["BaseAdapter"]] = {}


def register(cls: type["BaseAdapter"]) -> type["BaseAdapter"]:
    """类装饰器：把适配器登记进 REGISTRY。

    同名 platform 直接报错，不静默覆盖：否则 `build()` 选到哪个实现取决于
    import 顺序，出问题时毫无线索（配置写的是 webhook，跑起来可能是别的）。
    """
    platform = str(getattr(cls, "platform", "") or "").strip().lower()
    if not platform:
        raise AdapterError(f"{cls.__name__} 没有声明 platform")
    prev = REGISTRY.get(platform)
    if prev is not None and prev is not cls:
        raise AdapterError(
            f"platform {platform!r} 已被 {prev.__name__} 占用，{cls.__name__} 无法注册")
    REGISTRY[platform] = cls
    return cls


def available() -> list:
    """所有已注册的适配器（面板用来出下拉）。"""
    out = []
    for pid, cls in sorted(REGISTRY.items()):
        # `"   "` 这种 docstring：strip() 之后是空列表，[0] 会 IndexError，
        # 整个面板的适配器列表就没了。
        desc = str(getattr(cls, "DESCRIPTION", "") or "").strip()
        if not desc:
            doc = (cls.__doc__ or "").strip()
            desc = doc.splitlines()[0] if doc else ""
        out.append({"id": pid, "name": getattr(cls, "title", pid),
                    "description": desc, "system": True,
                    "ingress": getattr(cls, "INGRESS", None),
                    "default_off": bool(getattr(cls, "DEFAULT_OFF", True)),
                    "capabilities": dict(getattr(cls, "CAPABILITIES", {}) or {})})
    return out


@dataclass
class Inbound:
    """一次入站 HTTP 请求的处理结果。

    适配器不直接写 socket（那是 server.py 的事），只回答三件事：

      · `reply`   —— 立刻回给平台的响应体（`(content_type, bytes)`），
                    比如飞书的 challenge、微信的 echostr；
      · `event`   —— 要交给运行时分发的事件（OneBot 11 的 `post_type` 结构）；
      · `error`/`status` —— 校验不过时回什么状态码。

    为什么要有 `reply` 和 `event` 同时存在：微信/飞书这类平台既要求
    「立刻回 200」又要求「随后再主动发消息」，两者必须能分开。
    """
    event: Optional[dict] = None
    reply: Optional[tuple[str, bytes]] = None
    status: int = 200
    error: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


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

    # ---------- 系统插件元信息（面板「插件」页按这个渲染卡片） ----------
    #: 一句话说明，面板卡片上显示
    DESCRIPTION: str = ""
    #: 英文名与说明（面板语言切到 English 时用；留空就显示中文原文）
    TITLE_EN: str = ""
    DESCRIPTION_EN: str = ""
    VERSION: str = "1.0"
    #: 系统插件默认全部关闭：新装一份仓库，不配任何凭据时它只是「网页聊天」
    DEFAULT_OFF: bool = True
    #: 入站路径（server.py 会把该路径的请求交给本适配器）；None = 只做出站
    INGRESS: Optional[str] = None
    #: 面板设置表单：键名必须是 bot_config.json 里的真实键（扁平存放）
    CONFIG_SCHEMA: Dict[str, dict] = {}

    #: 这个插件自带的「小页面」。返回 HTML 片段（面板塞进 iframe 里渲染）。
    #: 空字符串 = 没有页面。用它把平台自己的东西（扫码、诊断、控制台）
    #: 从「运维」页收回到插件自己身上 —— 运维不该知道任何平台的细节。
    def page(self) -> str:
        return ""

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

    def recall(self, message_id: str) -> Any:
        raise AdapterError(f"{self.platform} 不支持撤回消息")

    def set_friend_add_request(self, flag: str, approve: bool = False,
                               remark: str = "") -> Any:
        raise AdapterError(f"{self.platform} 不支持好友请求审批")

    def set_group_add_request(self, flag: str, sub_type: str = "add",
                              approve: bool = False, reason: str = "") -> Any:
        raise AdapterError(f"{self.platform} 不支持入群请求审批")

    # ---------- 入站 ----------
    def parse_inbound(self, method: str, path: str, query: dict,
                      headers: dict, body: bytes) -> Inbound:
        """把平台回调解析成框架事件。

        默认实现 = 不认识这个请求（返回 `Inbound(status=404)`），
        因为 OneBot 11 的事件体**本来就是**框架事件结构，不需要转换。
        """
        return Inbound(status=404, error=f"{self.platform} 不处理入站请求")

    # ---------- 生命周期 / 自检 ----------
    def health(self) -> dict:
        """给面板「运维」页用。连不上别抛，返回 ok=False + 原因。"""
        return {"ok": True, "platform": self.platform}

    def describe(self) -> dict:
        return {"platform": self.platform, "title": self.title,
                "description": self.DESCRIPTION, "version": self.VERSION,
                "ingress": self.INGRESS, "system": True,
                "owner_id": self.owner_id, "self_id": self.self_id,
                "capabilities": dict(self.CAPABILITIES)}

    def system_info(self) -> dict:
        """系统插件在「插件」页的卡片数据（和普通插件的 meta 同构）。"""
        return {"name": self.platform, "title": self.title,
                "description": self.DESCRIPTION, "version": self.VERSION,
                "system": True, "ingress": self.INGRESS,
                "capabilities": dict(self.CAPABILITIES),
                "schema": dict(self.CONFIG_SCHEMA),
                "locked": False, "characters": [], "commands": 0,
                "command_list": [], "usage": "", "author": "", "homepage": ""}

    def reconfigure(self, config: dict) -> bool:
        """配置热更新。

        返回 True 表示就地生效；返回 False 表示「连接目标变了，请重建实例」。
        基类不碰任何连接，所以无条件 True —— 会换目标的适配器要覆盖它。
        """
        incoming = dict(config or {})
        self.config = {**self.config, **incoming}
        # 只在这次配置里**带了**这个键时才改：显式传空串是「清掉」的意思，
        # 用 `or self.owner_id` 会把它顶回旧值，等于永远清不掉。
        if "owner_id" in incoming:
            self.owner_id = str(incoming.get("owner_id") or "")
        if "self_id" in incoming:
            self.self_id = str(incoming.get("self_id") or "")
        return True

    def close(self) -> None:
        pass

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} platform={self.platform}>"


def system_plugins() -> list:
    """所有适配器（= 系统插件）的元信息，按 id 排序。"""
    out = []
    for pid, cls in sorted(REGISTRY.items()):
        inst = cls.__new__(cls)          # 不执行 __init__：只读元信息，别去连网
        info = BaseAdapter.system_info(inst)
        desc = str(getattr(cls, "DESCRIPTION", "") or "").strip()
        if not desc:                      # 没写 DESCRIPTION 就退回 docstring 第一行
            doc = (cls.__doc__ or "").strip()
            desc = doc.splitlines()[0] if doc else ""
        info.update({"name": pid, "title": getattr(cls, "title", pid),
                     "description": desc,
                     "version": getattr(cls, "VERSION", "1.0"),
                     "ingress": getattr(cls, "INGRESS", None),
                     "default_off": bool(getattr(cls, "DEFAULT_OFF", True)),
                     "schema": dict(getattr(cls, "CONFIG_SCHEMA", {}) or {}),
                     "capabilities": dict(getattr(cls, "CAPABILITIES", {}) or {}),
                     "has_page": cls.page is not BaseAdapter.page,
                     "title_en": str(getattr(cls, "TITLE_EN", "") or ""),
                     "description_en": str(getattr(cls, "DESCRIPTION_EN", "") or "")})
        out.append(info)
    return out


def build(config: Optional[dict] = None, platform: Optional[str] = None,
          **kw) -> BaseAdapter:
    """按配置造适配器。

    `platform` 显式指定就造那一个（多平台并存时逐个构建）；
    不给则读配置里的 `adapter`，再缺省 onebot11（向后兼容老配置）。
    """
    cfg = dict(config or {})
    pid = str(platform or cfg.get("adapter") or "onebot11").strip().lower()
    cls = REGISTRY.get(pid)
    if cls is None:
        known = ", ".join(sorted(REGISTRY)) or "（无）"
        raise AdapterError(f"没有这个适配器：{pid!r}（可用：{known}）")
    return cls(cfg, **kw)

