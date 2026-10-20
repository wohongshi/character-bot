"""OneBot 适配器插件（Features）框架。

设计参考了 AstrBot 的 star 插件（命令 / 过滤器 / 权限，装饰器注册 + 权限类型）与
NoneBot2 的 Matcher（priority 顺序 / block 阻断 / 临时冷却 / 会话维度），
按本适配器的体量做了精简：

- **插件** = ``features_dir``（默认 ``<profile>/onebot_features``）下的 ``*.py`` 文件，
  暴露一个同步的 ``register(api)`` 函数。首次运行会从 ``feature_templates/`` 铺一套示例。
- **三种挂载点**：
  * ``api.on_command(name, handler, aliases=…, scope=…, permission=…, cooldown=…, help=…)``
  * ``api.on_message(handler, priority=…)``  —— 发往 agent 之前；可改写文本；返回 ``api.BLOCK``
    可拦下这条消息（不发给 agent）。
  * ``api.on_reply(handler)``                —— 出站文本真正发出之前；返回新文本即改写。
- **命令前缀**默认 ``.``（也认全角 ``。`` 和 ``．``，``!`` 同理认 ``！``；
  前缀可配多字符，如 ``./／`` 让斜杠也当命令前缀）：
  命令本身就是"定向"，群里使用命令**不需要 @**。斜杠 ``/`` 默认留给网关的斜杠命令层；
  适配器会把「没命中命令的斜杠消息」剥杠放行（配 ``./／`` 时，斜杠直接当命令前缀用）。
- **权限**：``all`` / ``owner``（主人）/ ``admin``（群主或群管理，或主人）。
  权限不足：静默吞掉（不回复、不喂给模型），只记日志 + 计数。
- **冷却**：按 (命令, 用户) 计秒；冷却期内静默吞掉。
- **命令是"消费"语义**：命中并执行后消息不会再发给 agent（要回复就 ``ctx.reply``，
  直接返回字符串也行）；没命中任何命令才继续走正常流程。
- **热重载**：适配器每 15 秒扫一次目录指纹（``*.py`` / ``config.json`` / ``.reload``），
  有变化就整体重载；管理面板的「重载」按钮就是 ``touch .reload``。
- **错误隔离**：任何插件抛异常只落日志、记到该插件的 ``errors``，绝不影响消息主流程。
"""

from __future__ import annotations

import re

import asyncio
import copy
import importlib.util as _ilu
import inspect
import json
import logging
import math
import os
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .ext import ExtPlugin, load_spec

from . import cmdpolicy as _cmdpolicy      # 权限/作用域覆盖（热路径与失败兜底共用）
from . import chat_char                    # 会话绑定的「此刻是谁」（只读 ContextVar）
from . import toolbox
logger = logging.getLogger(__name__)

# 按需装载失败后的退避（秒）：失败一次就每条消息重试会刷日志、也拖慢消息
RETRY_COOLDOWN = 300.0


# ---- 常量 / 哨兵 ----
class _Block:
    """on_message 钩子的阻断哨兵：``return api.BLOCK`` 表示此消息不再发往 agent。"""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover
        return "<FeatureBlock>"


BLOCK = _Block()

_PREFIX_ALIASES = {".": "。", "．": None, "!": "！", "！": None}


def _plugin_enabled(name: str, enabled_cfg: Any, default_on: bool = True) -> bool:
    """按 config.json 的 `enabled` 决定一个插件开不开。

    约定（三处共用，别再各写一遍）：

      · `enabled` 缺省（None）→ 看插件自己的 DEFAULT_OFF；
      · 字符串 → 当成「只启用这一个」的白名单（旧代码曾把它逐字符拆开）；
      · 其它不可迭代值（int 等）→ 回落默认开关。
    """
    if enabled_cfg is None:
        return default_on
    if isinstance(enabled_cfg, str):
        return name == enabled_cfg
    try:
        return name in {str(x) for x in enabled_cfg}
    except TypeError:
        return default_on


def strip_prefix(text: str, chars) -> str:
    """去掉开头的命令前缀（`./／` 或面板配置的那些）。

    规则/自然语言里存的是「`.骰子 2d6`」这种带前缀的整串，运行时和模拟器
    都必须**按同一套字符**剥 —— 一边无条件 `lstrip("./／")`、一边只看配置，
    会造出「试跑说没这个命令、实际能跑」的假阴性。
    """
    s = str(text or "").strip()
    # **并上默认三件套**：规则里存的可能是默认前缀写的示例（`.骰子 2d6`），
    # 而面板把前缀改成了 `!` —— 只按配置剥的话这条规则就永远跑不了。
    # 反过来，多剥几个前缀字符不会误伤（命令名不可能以这些符号开头）。
    cs = set(str(c) for c in (chars or "") if str(c)) | {".", "/", "／"}
    while s and s[0] in cs:
        s = s[1:].strip()
    return s


def _prefix_chars(prefix: str) -> set:
    """把配置的前缀串展开成可识别的字符集（含全角变体）。"""
    chars = set()
    for ch in (prefix or ".").strip() or ".":
        chars.add(ch)
        alt = _PREFIX_ALIASES.get(ch, None)
        if ch == "．":
            continue
        if alt:
            chars.add(alt)
        if ch == ".":
            chars.add("．")
    return chars


# ---------------------------------------------------------------- 插件元数据 / 配置 schema
# NoneBot 的 `__plugin_meta__` + AstrBot 的 `_conf_schema.json`，合成一套轻量约定：
#   __plugin_meta__ = {"name": ..., "usage": ..., "author": ..., "homepage": ...}
#   CONFIG_SCHEMA   = {"key": {"type": "int", "description": "...", "default": 3,
#                              "min": 0, "max": 10, "enum": [...]}}
# 两种都可以用模块级常量简写：NAME / DESCRIPTION / USAGE / AUTHOR / VERSION / HOMEPAGE。
SCHEMA_TYPES = {"bool", "int", "float", "str", "text", "list", "json", "select"}
_TYPE_ALIASES = {"string": "str", "number": "float", "integer": "int", "boolean": "bool",
                 "object": "json", "dict": "json", "array": "list", "textarea": "text"}


def _infer_type(value) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, (list, tuple)):
        return "list"
    if isinstance(value, dict):
        return "json"
    return "str"


def normalize_schema(raw) -> dict:
    """把插件声明的 CONFIG_SCHEMA 收敛成统一结构（坏声明忽略，不炸加载）。"""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, spec in raw.items():
        key = str(key).strip()
        if not key:
            continue
        if not isinstance(spec, dict):
            spec = {"default": spec}
        typ = str(spec.get("type") or _infer_type(spec.get("default"))).strip().lower()
        typ = _TYPE_ALIASES.get(typ, typ)
        if typ not in SCHEMA_TYPES:
            typ = "str"
        field = {"type": typ,
                 "description": str(spec.get("description") or spec.get("desc") or ""),
                 "default": spec.get("default")}
        if typ in ("int", "float"):
            for k in ("min", "max"):
                if isinstance(spec.get(k), (int, float)):
                    field[k] = spec[k]
        if typ == "select":
            opts = spec.get("enum") or spec.get("options") or spec.get("choices") or []
            field["options"] = [str(x) for x in opts] if isinstance(opts, (list, tuple)) else []
        if typ in ("str", "text"):
            if isinstance(spec.get("max_len"), int):
                field["max_len"] = spec["max_len"]
        out[key] = field
    return out


def _as_bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "on", "是", "开"):
        return True
    if s in ("0", "false", "no", "off", "否", "关", ""):
        return False
    raise ValueError(f"不是布尔值：{v!r}")


def coerce_setting(field: dict, value):
    """按 schema 把面板提交的值转成正确类型；不合法抛 ValueError。"""
    typ = field.get("type") or "str"
    if typ == "bool":
        return _as_bool(value)
    if typ == "int":
        if isinstance(value, bool):
            raise ValueError("要整数")
        try:
            num = float(str(value).strip())
            if not math.isfinite(num):
                raise ValueError("要有限数字")
            out = int(num)
        except (OverflowError, ValueError) as e:
            raise ValueError(f"不是有效整数：{value!r}") from e
        if "min" in field:
            out = max(int(field["min"]), out)
        if "max" in field:
            out = min(int(field["max"]), out)
        return out
    if typ == "float":
        if isinstance(value, bool):
            raise ValueError("要数字")
        try:
            out = float(str(value).strip())
        except (OverflowError, ValueError) as e:
            raise ValueError(f"不是有效数字：{value!r}") from e
        if not math.isfinite(out):
            raise ValueError("要有限数字")
        if "min" in field:
            out = max(float(field["min"]), out)
        if "max" in field:
            out = min(float(field["max"]), out)
        return out
    if typ == "list":
        if isinstance(value, (list, tuple)):
            return [str(x).strip() for x in value if str(x).strip()]
        return [x.strip() for x in re.split(r"[\n,，]+", str(value or "")) if x.strip()]
    if typ == "json":
        if isinstance(value, (dict, list)):
            return value
        # 已经是 JSON 标量（bool / 数字）就原样返回：str(False) 是 "False"，
        # json.loads 不认；`value or "null"` 更糟，会把 0 / False 变成 null。
        if isinstance(value, (bool, int, float)):
            return value
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return json.loads(str(value))
    if typ == "select":
        out = str(value)
        allowed = [str(x) for x in field.get("options") or []]
        if allowed and out not in allowed:
            raise ValueError(f"只能是 {'/'.join(allowed)} 之一")
        return out
    out = "" if value is None else str(value)
    return out[: int(field.get("max_len") or 4000)]


def coerce_settings(schema: dict, values: dict) -> tuple[dict, list[str]]:
    """返回 (干净的设置, 错误列表)。schema 为空时原样接受（兼容老插件）。"""
    if not isinstance(values, dict):
        return {}, ["values 必须是对象"]
    if not schema:
        return {k: v for k, v in values.items() if k != "_doc"}, []
    clean, errors = {}, []
    for key, value in values.items():
        if key == "_doc":
            continue
        field = schema.get(key)
        if not field:
            errors.append(f"未知配置项 {key}（已忽略）")
            continue
        try:
            clean[key] = coerce_setting(field, value)
        except (ValueError, TypeError) as e:
            errors.append(f"{key}: {e}")
    return clean, errors


def merge_plugin_settings(config: dict, name: str, values: dict, schema: dict) -> tuple[dict, list[str]]:
    """把一次表单提交合并进 config.json 的结构（不改原对象）。"""
    clean, errors = coerce_settings(schema, values)
    cfg = copy.deepcopy(config) if isinstance(config, dict) else {}
    settings = cfg.get("settings")
    settings = settings if isinstance(settings, dict) else {}
    cur = settings.get(name) if isinstance(settings.get(name), dict) else {}
    cur.update(clean)
    settings[name] = cur
    cfg["settings"] = settings
    return cfg, errors


@dataclass
class _SendCtx:
    """出站钩子的轻量上下文（hoisted：原先每次回复都现场 dataclass() 一遍，热路径开销）。"""

    chat_id: str
    is_group: bool


@dataclass
class FeatureContext:
    """一次消息事件里、插件可见的全部上下文。"""

    adapter: Any
    text: str                       # 展平后的文本（含「[图片]」之类占位符）
    is_group: bool
    group_id: str
    user_id: str
    user_name: str
    message_id: str
    mentioned: bool = False
    role: str = ""                  # 群成员的 OneBot 身份：owner/admin/member（私聊为空）
    # 命令分发时填充：
    command: str = ""               # 命中的命令名（原始拼写）
    args: List[str] = field(default_factory=list)
    # 这个会话此刻生效的角色（运行时入口处填）。角色限定插件/命令按它门控；
    # 空 = 没上下文（模拟器、单测），按全局角色处理。
    character: str = ""

    @property
    def arg_text(self) -> str:
        return " ".join(self.args)

    @property
    def is_owner(self) -> bool:
        return bool(getattr(self.adapter, "owner_id", "")) and self.user_id == getattr(
            self.adapter, "owner_id", "")

    @property
    def is_admin(self) -> bool:
        """群主/群管理 或 主人（群里才有效）。"""
        return self.is_owner or (self.is_group and self.role in ("owner", "admin"))

    @property
    def chat_id(self) -> str:
        """会话 ID。优先问适配器（换平台时形态可能不同），老实现退回 _chat_id。"""
        fn = getattr(self.adapter, "chat_id_of", None)
        if callable(fn):
            try:
                return str(fn(self.is_group, self.group_id, self.user_id))
            except Exception:                                      # noqa: BLE001
                pass
        return str(self.adapter._chat_id(self.is_group, self.group_id, self.user_id))

    @property
    def platform(self) -> str:
        """当前适配器的平台名（插件据此做分支，比如 onebot11 / webhook）。"""
        return str(getattr(self.adapter, "platform", "") or "onebot11")

    def supports(self, capability: str) -> bool:
        """这个平台支持某个能力吗（image / voice / group_member_list …）。

        插件应该问它，而不是 `hasattr(adapter, "send_image")` —— 后者在有
        BaseAdapter 兜底方法时永远为真。
        """
        caps = getattr(self.adapter, "CAPABILITIES", None)
        if isinstance(caps, dict):
            return bool(caps.get(capability))
        return hasattr(self.adapter, capability)

    async def reply(self, text: str) -> None:
        """直接往当前会话发一条消息（走适配器 send：限速/引用/富文本都适用）。"""
        if text:
            # 命令输出是一个整体（菜单/结算/卡片文案），不参与「分句连发」拆条
            await self.adapter.send(self.chat_id, text, sentence_split=False)


@dataclass
class EventContext:
    """非消息事件（notice / request）的上下文。

    消息事件用 `FeatureContext`（有 text / args / reply）；这两类没有「回复」
    语义，插件一般只做记录、审计、计数。要主动发消息用 `api.send(chat_id, …)`。

    注意：钩子是**旁听**。内置行为（给主人发提醒、写 requests.jsonl）照常执行，
    插件改不了「好友/入群请求绝不自动通过」这条铁律。
    """

    adapter: Any
    kind: str                       # "notice" | "request"
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def platform(self) -> str:
        return str(getattr(self.adapter, "platform", "") or "onebot11")

    @property
    def notice_type(self) -> str:
        return str(self.raw.get("notice_type") or self.raw.get("sub_type") or "")

    @property
    def request_type(self) -> str:
        return str(self.raw.get("request_type") or self.raw.get("sub_type") or "")

    @property
    def sub_type(self) -> str:
        return str(self.raw.get("sub_type") or "")

    @property
    def user_id(self) -> str:
        return str(self.raw.get("user_id") or self.raw.get("operator_id") or "")

    @property
    def group_id(self) -> str:
        return str(self.raw.get("group_id") or "")

    @property
    def is_group(self) -> bool:
        return bool(self.raw.get("group_id"))

    @property
    def chat_id(self) -> str:
        fn = getattr(self.adapter, "chat_id_of", None)
        if callable(fn):
            try:
                return str(fn(self.is_group, self.group_id, self.user_id))
            except Exception:                                  # noqa: BLE001
                pass
        return f"group:{self.group_id}" if self.is_group else f"private:{self.user_id}"

    @property
    def is_owner(self) -> bool:
        owner = str(getattr(self.adapter, "owner_id", "") or "")
        return bool(owner) and self.user_id == owner

    def supports(self, capability: str) -> bool:
        """这个平台支持某能力吗（text / image / request_approval …）。"""
        caps = getattr(self.adapter, "CAPABILITIES", None)
        if isinstance(caps, dict):
            return bool(caps.get(capability))
        return hasattr(self.adapter, capability)


@dataclass
class _Command:
    name: str
    handler: Callable
    aliases: Tuple[str, ...]
    scope: str
    permission: str
    cooldown: float
    help: str
    plugin: str
    priority: int = 100
    # 角色限定插件登记的命令：只用这几个角色能用（空 = 所有人）。
    # 由 register_command 从插件信息里填，插件作者不用管。
    characters: Tuple[str, ...] = ()


@dataclass
class _Hook:
    handler: Callable
    plugin: str
    priority: int = 50
    characters: Tuple[str, ...] = ()


@dataclass
class _NluRule:
    """自然语言触发规则：整句 fullmatch 命中 → 消息被替换成 ``.{cmd}`` 再走命令。"""
    pattern: "re.Pattern[str]"
    cmd: str
    plugin: str
    priority: int = 100
    seq: int = 0
    characters: Tuple[str, ...] = ()


class FeatureAPI:
    """交给每个插件模块的注册入口（``register(api)`` 的那个 api）。"""

    def __init__(self, manager: "FeatureManager", plugin: str, settings: dict):
        self._manager = manager
        self._plugin = plugin
        self._settings = settings if isinstance(settings, dict) else {}
        self._state: Dict[str, Any] = {}          # 插件自己的内存态（重载即清）
        self._system = False                      # 模块级 SYSTEM 在这里落地
        self.BLOCK = BLOCK

    # —— 注册 ——
    def on_command(self, name: str, handler: Callable, *, aliases=(), scope: str = "both",
                   permission: str = "all", cooldown: float = 0.0, help: str = "",
                   priority: int = 100) -> None:
        self._manager.register_command(
            _Command(name=str(name), handler=handler, aliases=tuple(str(a) for a in aliases),
                     scope=str(scope).lower(), permission=str(permission).lower(),
                     cooldown=float(cooldown or 0), help=str(help), plugin=self._plugin,
                     priority=int(priority)))

    def on_message(self, handler: Callable, *, priority: int = 50) -> None:
        self._manager.register_message_hook(_Hook(handler=handler, plugin=self._plugin,
                                                  priority=int(priority)))

    def on_reply(self, handler: Callable, *, priority: int = 50) -> None:
        self._manager.register_reply_hook(_Hook(handler=handler, plugin=self._plugin,
                                                priority=int(priority)))

    def on_notice(self, handler: Callable, *, priority: int = 50) -> None:
        """订阅 notice 事件（戳一戳 / 入群 / 退群 / 撤回 …）。

        签名：`async def h(ctx: EventContext)`。返回值不参与决策，只用于日志。
        """
        self._manager.register_notice_hook(_Hook(handler=handler, plugin=self._plugin,
                                                 priority=int(priority)))

    def on_request(self, handler: Callable, *, priority: int = 50) -> None:
        """订阅 request 事件（好友申请 / 入群申请）。

        ⚠️ 只能旁听：审批必须由主人在面板点，钩子无法自动通过。
        """
        self._manager.register_request_hook(_Hook(handler=handler, plugin=self._plugin,
                                                  priority=int(priority)))

    def add_tool(self, name: str, *, description: str = "", parameters: dict | None = None,
                 handler: Callable | None = None, system: bool = False) -> None:
        """把一个 **agent 工具** 挂到这个插件名下。

        插件关掉 → 工具从模型的可调用列表里消失（不是「还在、点了报错」）。
        `system=True` 会让它在面板归到「系统」分类（框架能力，比如联网）。
        """
        toolbox.register(self._plugin, name, handler, description=description,
                         parameters=parameters, system=bool(system or self._system),
                         owner=self._manager, characters=self._own_chars())

    # —— 会话角色绑定（bot/chat_char.py）：插件不该自己去读那个文件 ——
    def chat_character(self, chat_id: str) -> str:
        """这个会话绑定的角色；没绑 → ""（= 跟随全局）。"""
        return chat_char.get(self._state_root(), chat_id)

    def list_characters(self) -> List[dict]:
        """全部角色（id / name / en）。适配器没有角色库时返回空表。"""
        store = getattr(getattr(self._manager, "adapter", None), "characters", None)
        fn = getattr(store, "list", None)
        if not callable(fn):
            return []
        try:
            items = fn()
        except Exception:                                       # noqa: BLE001
            return []
        return [{"id": str(c.get("id") or ""), "name": str(c.get("name") or ""),
                 "en": str(c.get("en") or "")} for c in items if isinstance(c, dict)]

    def bind_chat_character(self, chat_id: str, cid: str, *, by: str = "") -> str:
        """把会话钉到某个角色；cid 空 = 取消绑定。返回绑定后的角色 id（取消 → ""）。"""
        ids = [c["id"] for c in self.list_characters()]
        if not cid:
            chat_char.clear(self._state_root(), chat_id)
            return ""
        if not ids:
            raise ValueError("这个运行环境没有角色库")
        rec = chat_char.set_binding(self._state_root(), chat_id, cid, by=by, valid_ids=ids)
        cid2 = str(rec.get("id") or "")
        # 绑上就把它装上：下一条消息就要用，别等第一次用到才装（会慢一拍）
        self._manager.ensure_character(cid2)
        return cid2

    def _state_root(self) -> Path:
        d = getattr(getattr(self._manager, "adapter", None), "state_dir", None)
        return Path(str(d)) if d else Path(os.environ.get("KLEE_STATE_DIR") or "state")

    def add_page(self, html, *, title: str = "") -> None:
        """挂一个**插件自带的小页面**（面板抽屉里用 iframe 打开）。

        `html` 是字符串或 `callable() -> str`。页面跑在同源 iframe 里，
        可以直接 fetch 面板的 `/api/*`（脚本里已经注入了 `ks.api/ks.post`）。
        用它把「这个插件自己的事」放回插件身上，别堆到运维页。
        """
        self._manager.pages[self._plugin] = {"title": str(title or self._plugin),
                                             "html": html}

    def on_nlu(self, pattern: str, cmd: str, *, priority: int = 100) -> None:
        """注册自然语言触发：整句命中后消息被替换成 `.{cmd}`（`{1}` 引用正则分组）。

        只在「对可莉说」的消息上尝试（被 @ / 被回复 / 私聊），群里聊天不会误触。
        """
        self._manager.register_nlu(pattern, cmd, self._plugin, priority=priority)

    # —— 给插件用的小工具 ——
    @property
    def character_id(self) -> str:
        """这个插件眼里的「我」是谁。

        角色限定插件 → 它自己声明的角色（在哪个群被调到都是它自己）；
        通用插件 → 这个会话此刻生效的角色（会话绑定优先，见 bot/chat_char.py）。
        """
        own = self._own_chars()
        if own:
            cur = self._manager._char_now()
            return cur if cur in own else own[0]
        return self._manager._char_now() or "klee"

    def _own_chars(self) -> Tuple[str, ...]:
        info = self._manager.plugins.get(self._plugin) or {}
        chars = info.get("characters") if isinstance(info, dict) else ()
        return tuple(chars) if isinstance(chars, (list, tuple)) else ()

    @property
    def character(self) -> dict:
        cid = self.character_id
        info = getattr(self._manager, "active_character_info", {}) or {}
        if str(info.get("id") or "") == cid:
            return info
        store = getattr(getattr(self._manager, "adapter", None), "characters", None)
        getter = getattr(store, "get", None)
        if callable(getter):
            try:
                got = getter(cid)
                if isinstance(got, dict):
                    return got
            except Exception:                                  # noqa: BLE001
                pass
        return info

    @property
    def settings(self) -> dict:
        return self._settings

    @property
    def state(self) -> dict:
        return self._state

    @property
    def adapter(self):
        """适配器实例（**主动**发消息用：定时提醒、口播播报这类「没有来消息也要说话」的场景）。

        每次从管理器现取，别缓存：网关重连时会新建适配器实例，缓存住的就是死引用。
        踩过的坑：旧版 FeatureAPI 压根没有这个属性，而 `remind.py` 一直写 `api.adapter.send(...)`
        —— 真框架上 AttributeError 被自己的 `except Exception: continue` 吞掉，**到点提醒从未发出去过**，
        测试里的假 api 却恰好带了 `.adapter`，所以一直没暴露（2026-10-13 白班实测发现）。
        """
        return getattr(self._manager, "adapter", None)

    async def send(self, chat_id: str, text: str):
        """主动给某个会话发一条文本（返回适配器的 SendResult）。

        ⚠️ 掉线时适配器**不抛异常**，而是返回 ``SendResult(success=False)`` ——
        调用方必须检查返回值，不然消息就这么丢了（提醒/播报尤其要重投）。
        """
        adapter = self.adapter
        if adapter is None:
            raise RuntimeError("适配器不可用（还没装载 / 已卸载）")
        # 主动播报不拆条：提醒/纪念日是一句话的整段语义
        return await adapter.send(chat_id, text, sentence_split=False)

    def commands_help(self, character: str = "") -> List[str]:
        """命令表（`.help` 用）。`character` 给了就只列那个角色能用的。

        同名多条（通用插件的别名 vs 某个角色的专属命令）按角色挑一条，
        别把两条都列出来（`._抱抱` 出现两遍看着像 bug）。
        """
        mgr = self._manager
        with mgr._lock:
            keys = list(mgr._cmd_all.keys())
        picked, seen = [], set()
        for key in keys:
            cmd = mgr._pick_cmd(key, character)
            if cmd is None or id(cmd) in seen:
                continue
            seen.add(id(cmd))
            if cmd.permission == "owner":
                continue
            if not mgr._char_ok(getattr(cmd, "characters", ()), character):
                continue
            picked.append(cmd)
        picked.sort(key=lambda c: (c.priority, c.name))
        out = []
        for cmd in picked:
            if cmd.help:
                out.append(cmd.help)
            else:
                prefix = str(getattr(self._manager.adapter, "feature_prefix", ".") or ".")
                out.append(f"{prefix}{cmd.name}")
        return out

    def log(self, msg: str, *args: Any) -> None:
        try:
            logger.info("[feature:%s] %s", self._plugin, msg % args if args else msg)
        except Exception:                                    # noqa: BLE001
            logger.info("[feature:%s] %s", self._plugin, msg)


class FeatureManager:
    """插件管理器：装载 / 热重载 / 命令分发 / 钩子链。"""

    def __init__(self, adapter: Any):
        self.adapter = adapter
        raw_dir = str(getattr(adapter, "features_dir", "") or "").strip()
        # 绝不回退到进程 CWD：那会把任意目录里的 *.py 当插件 exec_module。
        self.dir = Path(raw_dir) if raw_dir else Path(__file__).with_name("features")
        self.prefix_chars = _prefix_chars(getattr(adapter, "feature_prefix", "."))
        self.commands: Dict[str, _Command] = {}     # 小写名/别名 → 首选 _Command（面板枚举用）
        # 同一个名字下可能有多个角色各自的命令（见 register_command）；这里是全部。
        self._cmd_all: Dict[str, List[_Command]] = {}
        #: 外部插件（其他语言写的）：name → ExtPlugin。重载时全部关掉重来。
        self.ext_plugins: Dict[str, Any] = {}
        #: 插件自带的小页面：name → {"title": str, "html": str|callable}
        self.pages: Dict[str, dict] = {}
        self.hooks_in: List[_Hook] = []
        self.hooks_out: List[_Hook] = []
        self.hooks_notice: List[_Hook] = []     # 非消息事件（旁听）
        self.hooks_request: List[_Hook] = []
        self.nlu_rules: List[_NluRule] = []      # 自然语言 → 命令 规则（nlu.py 注册）
        self._nlu_seq = 0
        self.plugins: Dict[str, dict] = {}          # name → {description, version, enabled, error, file}
        self.hits: Dict[str, int] = {}              # plugin → 命中次数（跨重载累计）
        self.errors: Dict[str, int] = {}            # plugin → 异常次数
        self.cooldowns: Dict[Tuple[str, str], float] = {}
        self.loaded = False
        self.last_reload_ts = 0.0
        self.load_error = ""
        self._fp_seen: Optional[tuple] = None
        self._lock = threading.RLock()      # 装载/快照/注册互斥（HTTP 线程会读快照）
        self.active_character = str(getattr(adapter, "active_character", "") or "klee")
        self.active_character_info = dict(getattr(adapter, "active_character_info", {}) or {})
        # ---- 按角色按需装载 ----
        # 会话绑定之后，每个群都可能是不同角色；六个角色的插件全注册等于
        # 「谁写错一个插件都能影响所有人」，而且 100 多个模块白占内存。
        # 现在只装载**在用**的角色（全局当前 + 已绑定 + 面板选中的），
        # 其余插件留在 `_dormant` 里等第一次用到时再装。
        # 默认值；真正的读取在 `_load_locked()` 开头（面板改完要能立刻生效）
        self.lazy_characters = True
        self.max_loaded_characters = 4
        self._loaded_chars: set[str] = set()
        self._dormant: Dict[str, Path] = {}         # 未装载的角色插件：name → 文件
        self._char_touch: Dict[str, float] = {}      # LRU：角色 → 上次用到的时间
        self._retry_at: Dict[str, float] = {}         # 装载失败的角色 → 什么时候再试
        self._enabled_cfg: Any = []
        self._settings: dict = {}

    # ---------- 装载 ----------
    def scaffold(self) -> None:
        """首次运行：把 feature_templates/ 铺到 features_dir。"""
        if self.dir.exists():
            return
        tpl = Path(__file__).with_name("feature_templates")
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            if tpl.is_dir():
                for p in sorted(tpl.glob("*.py")):
                    # `_x.py` 是插件共用的支撑模块（如 _cardimg.py），装载器不当插件，
                    # 但必须一起铺过去 —— 否则抽卡/运势 import 不到它，一上来就是加载错误。
                    shutil.copy2(p, self.dir / p.name)
            cfg = self.dir / "config.json"
            if not cfg.exists():
                cfg.write_text(json.dumps({"enabled": None, "settings": {}},
                                          ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info("OneBot: 已铺设插件示例到 %s（%d 个）",
                        self.dir, len(list(self.dir.glob("*.py"))))
        except OSError as e:
            logger.warning("OneBot: 铺设插件目录失败 — %s", e)

    def _read_config(self) -> tuple[dict, bool]:
        """返回 (配置, 是否损坏)。缺文件 = 首次运行（启用默认开）；坏文件 = 全部不启用。"""
        cfg = self.dir / "config.json"
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}, False
        except (OSError, ValueError) as e:
            logger.error("OneBot: config.json 读取失败（%s）—— 本次不启用任何插件，避免误开", e)
            return {}, True
        if not isinstance(data, dict):
            logger.error("OneBot: config.json 不是对象 —— 按损坏处理，不启用任何插件")
            return {}, True
        return data, False

    def load(self) -> None:
        """整体装载（同步；重载也走这里）。"""
        with self._lock:
            self._load_locked()

    def _load_locked(self) -> None:
        # 指纹在装载**之前**取：装载过程中又改文件的话，下一轮扫描还能发现变化；
        # 取在末尾会把「没被装载进去的那版」当成已处理，改动永远丢失。
        fp_at_start = self._fingerprint()
        # 每次都按当前配置重算：`apply_config` 只改 adapter.config，不重建 manager，
        # 读在 __init__ 里的话面板上那个开关就成了「重启才生效」的假开关。
        cfg_all = getattr(self.adapter, "config", None)
        cfg_all = cfg_all if isinstance(cfg_all, dict) else {}
        self.lazy_characters = bool(cfg_all.get("lazy_plugins", True))
        try:
            self.max_loaded_characters = max(1, int(cfg_all.get("max_loaded_characters") or 4))
        except (TypeError, ValueError):
            self.max_loaded_characters = 4
        self.scaffold()
        # 复位注册表（hits/errors 跨重载保留）。
        # `plugin == "__builtin__"` 的是运行时自带的命令（如 .agent），不来自插件文件，
        # 热重载时必须原样保留，否则重载一次就「命令消失」。
        builtin = {k: c for k, c in self.commands.items() if c.plugin == "__builtin__"}
        self.commands = dict(builtin)
        self._cmd_all = {k: [c] for k, c in builtin.items()}
        self.hooks_in = []
        self.hooks_out = []
        self.hooks_notice = []
        self.hooks_request = []
        self.nlu_rules = []
        self._nlu_seq = 0
        self.plugins = {}
        self.pages = {}
        # 命令的作用域/权限覆盖（面板里调，见 bot/cmdpolicy.py）。
        # 读不到就是空表 = 完全按插件声明，行为不变。
        try:
            self.overrides = _cmdpolicy.load(self.dir)
        except Exception as e:                                     # noqa: BLE001
            logger.warning("OneBot: 读命令权限覆盖失败（按默认处理）：%s", e)
            self.overrides = {"commands": {}, "plugins": {}}
        # 工具表跟着一起清：插件装不上 / 被关掉，它的工具就不该还挂在模型面前
        toolbox.reset(self)
        self.load_error = ""

        cfg, corrupt = self._read_config()
        if not isinstance(cfg, dict):          # 合法 JSON 但不是对象 → 当损坏处理（fail-closed）
            cfg, corrupt = {}, True
        enabled_cfg = cfg.get("enabled")
        if corrupt:
            enabled_cfg = []                      # fail-closed：坏配置不放开默认插件
        settings = cfg.get("settings") if isinstance(cfg.get("settings"), dict) else {}
        self._enabled_cfg, self._settings = enabled_cfg, settings
        self._dormant = {}
        self._loaded_chars = self._want_characters()

        self._register_builtin()
        self._purge_modules()
        self._ext_close_all()          # 重载前先收掉旧的外部进程，别留下孤儿

        files = sorted(p for p in self.dir.glob("*.py") if not p.name.startswith("_"))
        tools_default_on = set(toolbox.TOOL_PLUGINS)
        if not corrupt:
            self._adopt_new_plugins(files, cfg, tools_default_on)
        sys_path_added = False
        try:
            if str(self.dir) not in sys.path:
                sys.path.append(str(self.dir))   # append：插件目录只作兜底，别盖住 stdlib（OCR 修）
                sys_path_added = True
            for path in files:
                self._load_one(path, enabled_cfg, settings)
        finally:
            if sys_path_added:
                try:
                    sys.path.remove(str(self.dir))
                except ValueError:
                    pass

        if not corrupt:
            self._load_ext(enabled_cfg)

        # 关掉的插件：把 bootstrap 可能已经登记过的工具摘掉。
        # 为什么需要：`bot.agent` 导入时会 `toolbox.bootstrap()` 把工具插件全注册一遍
        # （那时还没有 manager），owner=None，`reset(self)` 清不到它。
        # 只按「本目录里确实存在、且这次没启用」的插件名摘 —— 不能整表清空：
        # 单测用的是临时插件目录，工具表就靠 bootstrap 那一份。
        for _name, _info in self.plugins.items():
            if _info.get("registered"):
                continue
            if not _info.get("enabled"):
                # 只摘 bootstrap 留下的那份（owner 是哨兵），别把同一进程里
                # 另一个 FeatureManager 注册的同名工具一起端掉（owner=None 会全摘）。
                toolbox.unregister_plugin(_name, toolbox._BOOT_OWNER)

        self.hooks_in.sort(key=lambda h: h.priority)
        self.hooks_out.sort(key=lambda h: h.priority)
        self.hooks_notice.sort(key=lambda h: h.priority)
        self.hooks_request.sort(key=lambda h: h.priority)
        self.loaded = True
        self.last_reload_ts = time.time()
        self._fp_seen = fp_at_start
        logger.info("OneBot: 插件装载完成 —— %d 个（启用 %d），命令 %d 个，NLU %d 条，钩子 入%d/出%d",
                    len(self.plugins),
                    sum(1 for p in self.plugins.values() if p["enabled"]),
                    len({id(c) for c in self.commands.values()}),
                    len(self.nlu_rules),
                    len(self.hooks_in), len(self.hooks_out))

    # ---------- 新插件默认开 ----------
    def _adopt_new_plugins(self, files, cfg: dict, default_on: set) -> None:
        """把**第一次出现**的插件按自带默认开关落进 config.json。

        为什么要这份 known 记录：`enabled` 是白名单，「用户主动关掉」和
        「这插件刚装上、还没进名单」在里面长得一模一样。有了 known 才能区分：
        没见过的 → 按默认（工具插件默认开）；见过但不在名单 → 用户关的，不动。

        工具插件尤其不能默认关：升级一次 agent 就瞎了，而且面板上看不出原因。
        """
        known_path = self.dir / "known.json"        # 和 config.json 放一起
        try:
            known = set(json.loads(known_path.read_text(encoding="utf-8")).get("names") or [])
        except (OSError, ValueError, TypeError):
            known = set()
        names = [p.stem for p in files]
        fresh = [n for n in names if n not in known]
        if not fresh:
            return
        enabled = cfg.get("enabled")
        if not isinstance(enabled, list):
            enabled = []
            cfg["enabled"] = enabled
        added = []
        for n in fresh:
            if n in default_on and n not in enabled:
                enabled.append(n)
                added.append(n)
        try:
            known_path.parent.mkdir(parents=True, exist_ok=True)
            known_path.write_text(json.dumps({"names": sorted(known | set(names))},
                                             ensure_ascii=False, indent=0), encoding="utf-8")
        except OSError as e:
            logger.warning("OneBot: 写 known.json 失败 — %s", e)
        if added:
            try:
                cfg_path = self.dir / "config.json"
                cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                                    encoding="utf-8")
            except OSError as e:
                logger.warning("OneBot: 存新插件默认开关失败 — %s", e)
            logger.info("OneBot: 新插件按默认开启 —— %s", ", ".join(added))

    # ---------- 外部插件（Node / Rust / C / C++ / Java / PHP …） ----------
    def _ext_close_all(self) -> None:
        for p in list(self.ext_plugins.values()):
            try:
                p.close()
            except Exception:                                      # noqa: BLE001
                pass
        self.ext_plugins = {}

    def _load_ext(self, enabled_cfg: Any) -> None:
        """扫 `features/ext/*.ext.json`，把别的语言写的插件接进来。

        规格文件小到可以手写：

            {"id":"uuid","title":"UUID","language":"node","entry":"tools/uuid.mjs",
             "timeout":4,"description":"生成 UUID / 格式化 JSON"}
        """
        ext_dir = self.dir / "ext"
        if not ext_dir.is_dir():
            return
        for spec_path in sorted(ext_dir.glob("*.ext.json")):
            name = spec_path.name[: -len(".ext.json")]
            info = {"description": "", "title": name, "version": "", "enabled": False,
                    "error": "", "file": spec_path.name, "usage": "", "author": "",
                    "homepage": "", "schema": {}, "characters": [], "locked": False,
                    "external": True, "language": "", "entry": "",
                    "commands": 0, "command_list": []}
            try:
                spec = load_spec(spec_path)
            except Exception as e:                                 # noqa: BLE001
                info["error"] = f"规格文件读不动：{type(e).__name__}: {e}"
                self.plugins[name] = info
                continue
            spec["id"] = str(spec.get("id") or name).lower()
            info["title"] = str(spec.get("title") or name)
            info["description"] = str(spec.get("description") or "")
            info["version"] = str(spec.get("version") or "1.0")
            info["language"] = str(spec.get("language") or "binary")
            info["entry"] = str(spec.get("entry") or "")
            if not _plugin_enabled(name, enabled_cfg, True):
                info["enabled"] = False
                self.plugins[name] = info
                continue
            try:
                plug = ExtPlugin(spec, ext_dir)
                meta, booted = self._ext_meta(plug, spec_path)   # 优先读缓存，别每次开机都拉进程
            except Exception as e:                                 # noqa: BLE001
                info["error"] = f"{type(e).__name__}: {e}"
                self.plugins[name] = info
                logger.warning("外部插件 %s 启动失败：%s", name, info["error"])
                continue
            cmds = meta.get("commands") if isinstance(meta.get("commands"), list) else []
            registered = []
            for c in cmds:
                if not isinstance(c, dict):
                    continue
                cname = str(c.get("name") or "").strip()
                if not cname:
                    continue
                aliases = tuple(str(a) for a in (c.get("aliases") or []) if str(a).strip())

                async def _handler(ctx, _p=plug, _c=cname, _n=name):
                    ctxd = {"user_id": ctx.user_id, "user_name": ctx.user_name,
                            "chat_id": ctx.chat_id, "is_group": ctx.is_group,
                            # 角色名挂在运行时（ctx.adapter 是路由视图，会转发过去）；
                            # FeatureContext 本身没有 character_id —— 这里写成
                            # ctx.character_id 会 AttributeError（踩过一次）。
                            "character": str(getattr(getattr(ctx, "adapter", None),
                                                     "active_character", "") or ""),
                            "platform": ctx.platform,
                            "raw": str(getattr(ctx, "text", "") or ""),
                            "message_id": ctx.message_id}
                    # 传给插件的是「参数部分」，不是整条消息：
                    # `.json {"a":1}` 里插件该拿到 {"a":1}，带上命令前缀会解析失败。
                    arg_text = str(getattr(ctx, "arg_text", "") or "")
                    try:
                        res = await asyncio.to_thread(
                            _p.call, _c, list(ctx.args or []), arg_text, ctxd)
                    except Exception as e:                         # noqa: BLE001
                        await ctx.reply(f"⚠ {_n} 插件出错：{str(e)[:180]}")
                        return
                    reply = res.get("reply")
                    if reply:
                        await ctx.reply(str(reply)[:1500])

                self.register_command(_Command(
                    name=cname, handler=_handler, aliases=aliases, scope="both",
                    permission="all", cooldown=float(c.get("cooldown") or 2),
                    help=str(c.get("help") or ""), plugin=name, priority=100))
                registered.append({"name": cname, "aliases": list(aliases),
                                   "help": str(c.get("help") or "")})
            self.ext_plugins[name] = plug
            if booted:
                log_note = "（首次握手）"
            else:
                log_note = "（读缓存，进程首次调用才起）"
            info.update({"enabled": True, "commands": len(registered),
                         "command_list": registered, "ext_note": log_note})
            self.plugins[name] = info
            logger.info("外部插件 %s（%s）就绪：%d 个命令%s",
                        name, info["language"], len(registered), log_note)

    def _ext_meta(self, plug: ExtPlugin, spec_path: Path) -> tuple[dict, bool]:
        """取外部插件的命令表：优先读缓存，缓存失效才真的起进程握手。

        为什么：起 5 个进程（Node 146ms + Java 364ms + 三个原生）要 ~530ms，
        还常驻 ~26MB —— 而它们的命令表几乎不变。缓存按「规格文件 mtime + 入口
        文件大小」做键，改了代码就自动失效，下次开机再握手一次。
        返回 (meta, 是否真的起了进程)。
        """
        cache_dir = Path(getattr(self.adapter, "state_dir", self.dir)) / "extmeta"
        cache = cache_dir / f"{plug.name}.json"
        try:
            size = 0
            if plug.entry:                      # run+args 型（Java）没有入口文件
                entry = plug.base / plug.entry
                size = entry.stat().st_size if entry.exists() else 0
            key = f"{spec_path.stat().st_mtime_ns}-{size}"
        except OSError:
            key = ""
        try:
            saved = json.loads(cache.read_text(encoding="utf-8"))
            if key and saved.get("key") == key and isinstance(saved.get("meta"), dict):
                return dict(saved["meta"]), False
        except (OSError, ValueError):
            pass
        meta = plug.start()
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_name(cache.name + ".tmp")
            tmp.write_text(json.dumps({"key": key, "meta": meta}, ensure_ascii=False),
                           encoding="utf-8")
            os.replace(tmp, cache)
        except OSError:
            pass
        return dict(meta), True

    def _purge_modules(self) -> None:
        """清掉 features_dir 下遗留的模块缓存（含插件互相 import 的辅助模块）。"""
        try:
            root = str(self.dir.resolve())
        except OSError:
            root = str(self.dir)
        for name, mod in list(sys.modules.items()):
            f = getattr(mod, "__file__", None)
            if not f:
                continue
            try:
                p = str(Path(f).resolve())
            except OSError:
                continue
            if p.startswith(root + os.sep) or p == root:
                sys.modules.pop(name, None)

    def _drop_modules(self, name: str) -> None:
        """摘掉某个插件在 sys.modules 里的模块（卸载/重装都不能留旧对象）。

        `_load_one` 每次用 `onebot_feature_<name>_<ms>` 这个唯一名 exec：
        不摘的话，每「卸了再装」一次就多一份模块对象（连同它 import 的东西），
        长跑下来是实打实的内存增长。
        """
        for mod_name, mod in list(sys.modules.items()):
            if not mod_name.startswith("onebot_feature_"):
                continue
            # 只认文件名叫 <name>.py 的：`ledger` 和 `ledger_red` 的模块名前缀一样，
            # 按前缀删会把另一个插件的模块也摘掉。
            if Path(getattr(mod, "__file__", "") or "").name == f"{name}.py":
                sys.modules.pop(mod_name, None)

    # ---------- 按角色按需装载 ----------
    def _want_characters(self) -> set[str]:
        """这次装载应该**真的注册**哪些角色。

        来源三处：全局当前角色、适配器给的「在用角色」（运行时 = 已绑定的会话）、
        以及面板正在看的角色。适配器没提供时至少装上全局那个。
        """
        want = {str(self.active_character or "").strip().lower()} - {""}
        extra = getattr(self.adapter, "loaded_characters", None)
        if isinstance(extra, (set, frozenset, list, tuple)):
            want |= {str(x).strip().lower() for x in extra if str(x).strip()}
        return want

    def ensure_character(self, cid: str) -> bool:
        """确保这个角色的插件已注册。已注册 → False（便宜，可每条消息调）。"""
        cid = str(cid or "").strip().lower()
        if not cid:
            return False
        if not self.lazy_characters:
            with self._lock:
                self._char_touch[cid] = time.time()
            return False
        with self._lock:
            self._char_touch[cid] = time.time()      # 面板会并发遍历它，必须持锁写
            if cid in self._loaded_chars:
                return False
            if time.time() < float(self._retry_at.get(cid) or 0):
                return False                         # 刚失败过，退避一会儿再试
            self._loaded_chars.add(cid)
            todo = []
            for name, path in list(self._dormant.items()):
                info = self.plugins.get(name) or {}
                chars = {str(x).strip().lower() for x in (info.get("characters") or ())}
                if chars & self._loaded_chars:
                    todo.append((name, path))
            for name, path in todo:
                try:
                    self._load_one(path, self._enabled_cfg, self._settings)
                except Exception as e:                            # noqa: BLE001
                    logger.warning("OneBot: 按需装载插件 %s 失败 — %s", name, e)
                    self._dormant[name] = path
                    continue
                info = self.plugins.get(name) or {}
                if info.get("error") or not info.get("registered"):
                    # 失败：放回待命 + 把角色从「已加载」里摘掉，否则下次 ensure
                    # 会在 `cid in _loaded_chars` 那行直接返回，这个角色整轮进程
                    # 都再也装不上它（日志说「留待重试」但永远不重试）。
                    logger.warning("OneBot: 按需装载插件 %s 失败（%s），%d 秒后再试",
                                   name, str(info.get("error"))[:80] or "未注册",
                                   int(RETRY_COOLDOWN))
                    self._dormant[name] = path
                    self._loaded_chars.discard(cid)
                    self._retry_at[cid] = time.time() + RETRY_COOLDOWN
                else:
                    self._dormant.pop(name, None)
            return bool(todo)

    def release_character(self, cid: str) -> list[str]:
        """卸掉这个角色的插件（只卸「没有别的已加载角色在用」的那些）。"""
        cid = str(cid or "").strip().lower()
        if not self.lazy_characters or cid not in self._loaded_chars:
            return []
        with self._lock:
            self._loaded_chars.discard(cid)
            freed = []
            for name, info in list(self.plugins.items()):
                chars = {str(x).strip().lower() for x in (info.get("characters") or ())}
                if not chars or not info.get("registered"):
                    continue
                if chars & self._loaded_chars:      # 还有别的在用角色
                    continue
                self._unregister_plugin(name)
                info["registered"] = False
                info["enabled"] = False
                path = self.dir / str(info.get("file") or f"{name}.py")
                self._dormant[name] = path
                freed.append(name)
            logger.info("OneBot: 角色 %s 的插件已卸载（%d 个）", cid, len(freed))
            return freed

    def _unregister_plugin(self, name: str) -> None:
        """把一个插件的注册痕迹全摘掉：命令 / 钩子 / NLU / 工具 / 页面。

        只摘这个插件自己的：同名的另一条命令（另一个角色的）要留着 ——
        所以按**条目**过滤 `_cmd_all`，而不是按 key 删。
        """
        self._cmd_all = {k: [c for c in v if c.plugin != name]
                         for k, v in self._cmd_all.items()}
        self._rebuild_cmd_tables()
        for attr in ("hooks_in", "hooks_out", "hooks_notice", "hooks_request"):
            setattr(self, attr, [h for h in getattr(self, attr, []) if h.plugin != name])
        self.nlu_rules = [r for r in self.nlu_rules if r.plugin != name]
        toolbox.unregister_plugin(name, self)
        self.pages.pop(name, None)
        self._drop_modules(name)

    def _rebuild_cmd_tables(self) -> None:
        """从 `_cmd_all` 重建 `commands`（别名 → 首选）并丢掉空键。"""
        self._cmd_all = {k: v for k, v in self._cmd_all.items() if v}
        self.commands = {k: v[0] for k, v in self._cmd_all.items() if v}

    def update_loaded(self, characters) -> dict:
        """把「在用角色」名单同步给管理器：该装的装上，超出上限的 LRU 卸掉。

        名单 = 全局当前 + 所有绑定 + 面板选中的。`protected` 里的一律不卸。
        """
        want = {str(x).strip().lower() for x in (characters or ()) if str(x).strip()}
        want.add(str(self.active_character or "").strip().lower())
        for cid in sorted(want):
            self.ensure_character(cid)
        freed: list[str] = []
        if self.lazy_characters:
            with self._lock:
                # 「必须留着」= 全局 + 所有绑定的（卸了就等于把绑定弄坏）。
                # 上限只管**额外**装进来的角色（面板预览、以前用过一次的），
                # 所以绑定数本身就超过上限时全部保留，只记一条日志。
                extra = [c for c in self._loaded_chars if c not in want]
                extra.sort(key=lambda c: self._char_touch.get(c, 0.0))   # 最久没用先卸
                while extra and len(self._loaded_chars) > self.max_loaded_characters:
                    cid = extra.pop(0)
                    freed.extend(self.release_character(cid))
                if len(self._loaded_chars) > self.max_loaded_characters:
                    logger.info("在用的角色 %d 个，超过按需装载上限 %d（绑定的不卸）",
                                len(self._loaded_chars), self.max_loaded_characters)
        with self._lock:
            loaded = sorted(self._loaded_chars)
            n_dormant = len(self._dormant)
        return {"loaded": loaded, "required": sorted(want),
                "dormant": n_dormant, "released": freed}

    def loaded_characters(self) -> dict:
        """面板用：哪些角色已加载、有多少插件待命、上限多少。

        整段持锁：HTTP 线程读的时候，事件循环那边可能正往 plugins 里插东西
        （`ensure_character` → `_load_one`），边插边遍历会 RuntimeError。
        """
        with self._lock:
            per: Dict[str, int] = {}
            for info in list(self.plugins.values()):
                if self.lazy_characters and not info.get("registered"):
                    continue
                for c in (info.get("characters") or ()):
                    per[str(c)] = per.get(str(c), 0) + 1
            loaded = (sorted(self._loaded_chars) if self.lazy_characters
                      else sorted(per.keys()))
            out = {"lazy": bool(self.lazy_characters), "loaded": loaded,
                   "per": per, "dormant": len(self._dormant),
                   "max": int(self.max_loaded_characters),
                   "touch": {k: round(v, 1) for k, v in self._char_touch.items()}}
        return out

    def _load_one(self, path: Path, enabled_cfg: Any, settings: dict) -> None:
        name = path.stem
        info = {"description": "", "title": name, "version": "", "enabled": False,
                "error": "", "file": path.name,
                "usage": "", "author": "", "homepage": "", "schema": {},
                "characters": [], "locked": False, "system": False, "tools": [],
                "title_en": "", "description_en": ""}
        try:
            mod_name = f"onebot_feature_{name}_{int(time.time() * 1000)}"
            spec = _ilu.spec_from_file_location(mod_name, path)
            if spec is None or spec.loader is None:
                raise RuntimeError("无法为插件文件建立 import spec")
            module = _ilu.module_from_spec(spec)
            # 登记进 sys.modules：插件里 `import` 兄弟模块/自引用时才找得到，
            # 卸载时 `_drop_modules` 也才有东西可摘（不登记的话那个函数是空转）。
            sys.modules[mod_name] = module
            try:
                spec.loader.exec_module(module)
            except BaseException:
                sys.modules.pop(mod_name, None)      # 半截模块别留在表里
                raise
        except Exception as e:                                # noqa: BLE001
            info["error"] = f"{type(e).__name__}: {e}"
            self.plugins[name] = info
            logger.warning("OneBot: 插件 %s 加载失败 — %s", name, info["error"])
            return

        # 元信息：优先模块变量，其次 docstring 首行
        desc = str(getattr(module, "DESCRIPTION", "") or "").strip()
        if not desc:
            # `"   "` 这种 docstring：strip() 之后 splitlines() 是空列表，
            # [0] 直接 IndexError —— 而且这行在 try/except 之外，一个插件的
            # 空白 docstring 会把整次装载带崩。
            doc = (module.__doc__ or "").strip()
            if doc:
                desc = doc.splitlines()[0]
        meta = getattr(module, "__plugin_meta__", None)
        meta = meta if isinstance(meta, dict) else {}
        info["description"] = str(meta.get("description") or desc)
        info["title"] = str(meta.get("name") or getattr(module, "NAME", "") or name)
        # 英文名（面板切英文时用）；不写就回落中文，不强制每个插件都有
        info["title_en"] = str(meta.get("name_en") or getattr(module, "NAME_EN", "") or "")
        info["description_en"] = str(
            meta.get("description_en") or getattr(module, "DESCRIPTION_EN", "") or "")
        info["version"] = str(meta.get("version") or getattr(module, "VERSION", "") or "")
        info["usage"] = str(meta.get("usage") or getattr(module, "USAGE", "") or "")
        info["author"] = str(meta.get("author") or getattr(module, "AUTHOR", "") or "")
        info["homepage"] = str(meta.get("homepage") or getattr(module, "HOMEPAGE", "") or "")
        try:
            info["schema"] = normalize_schema(getattr(module, "CONFIG_SCHEMA", None))
        except Exception as e:                                # noqa: BLE001
            info["schema"] = {}
            logger.warning("OneBot: 插件 %s 的 CONFIG_SCHEMA 声明有误 — %s", name, e)
        chars = getattr(module, "CHARACTERS", ()) or ()
        if isinstance(chars, str):
            chars = (chars,)
        try:
            info["characters"] = [str(x).strip().lower() for x in chars if str(x).strip()]
        except TypeError:
            info["characters"] = []
        info["locked"] = bool(info["characters"])
        # 模块级 SYSTEM = True：这个插件提供的是框架能力（适配器、联网），
        # 面板归到「系统」分类，且不能被角色限定覆盖
        info["system"] = bool(getattr(module, "SYSTEM", False))

        if info["locked"]:
            # `enabled` 在面板上的含义**不变**：当前全局角色能不能用。
            # 注册与否看「这个角色是否在用」：按需装载时，没在用的角色插件
            # 只登记元信息（面板还看得见），不执行、不注册命令 —— 一个写坏的
            # 角色插件就不会影响别的群。
            info["enabled"] = self._char_now(self.active_character) in info["characters"]
            info["lock_reason"] = ("当前角色可用" if info["enabled"]
                                   else "仅 " + "/".join(info["characters"]) + " 可用")
            want = {str(x).strip().lower() for x in info["characters"]}
            if self.lazy_characters and not (want & self._loaded_chars):
                info["registered"] = False
                info["dormant"] = True
                self.plugins[name] = info
                self._dormant[name] = path
                return
            info["registered"] = True
        else:
            info["enabled"] = _plugin_enabled(
                name, enabled_cfg, not bool(getattr(module, "DEFAULT_OFF", False)))

        self.plugins[name] = info
        if not info["enabled"] and not info.get("registered"):
            return

        register = getattr(module, "register", None)
        if not callable(register):
            info["error"] = "没有 register(api) 函数"
            info["enabled"] = False
            logger.warning("OneBot: 插件 %s 缺少 register(api)，已跳过", name)
            return
        toolbox.unregister_plugin(name, self)   # 重载：先摘旧工具，register 里再登记新的
        self.pages.pop(name, None)           # 旧页面也一起摘（插件改名/删页面时不残留）
        api = FeatureAPI(self, name, settings.get(name) or {})
        # 模块级 SYSTEM = True → 这个插件登记的工具也算系统能力（联网、出网这类）
        api._system = bool(getattr(module, "SYSTEM", False))
        # 注册到一半抛异常时，把半截命令/钩子/NLU 撤掉，别让「已停用」的插件还在接消息
        snapshot = (dict(self.commands), {k: list(v) for k, v in self._cmd_all.items()},
                    list(self.hooks_in), list(self.hooks_out),
                    list(self.hooks_notice), list(self.hooks_request),
                    list(self.nlu_rules), self._nlu_seq)
        try:
            register(api)
        except Exception as e:                                # noqa: BLE001
            (self.commands, self._cmd_all, self.hooks_in, self.hooks_out,
             self.hooks_notice, self.hooks_request,
             self.nlu_rules, self._nlu_seq) = snapshot
            toolbox.unregister_plugin(name, self)
            self.pages.pop(name, None)
            info["error"] = f"register(): {type(e).__name__}: {e}"
            info["enabled"] = False
            logger.warning("OneBot: 插件 %s 注册失败（已回滚半截注册）— %s", name, info["error"])

    # ---------- 注册 ----------
    def _char_now(self, character: str = "") -> str:
        """此刻生效的角色：显式传入 → 当前上下文 → 全局当前角色。"""
        want = str(character or "").strip()
        if not want:
            want = chat_char.current() or str(getattr(self, "active_character", "") or "")
        return want.strip().lower()

    def _char_ok(self, characters, character: str = "") -> bool:
        """空 = 通用（谁都能用）；否则必须命中此刻的角色。"""
        chars = tuple(characters or ())
        if not chars:
            return True
        return self._char_now(character) in chars

    def _chars_of(self, plugin: str) -> Tuple[str, ...]:
        """插件声明的角色限定（没有 → 空元组 = 通用）。"""
        info = self.plugins.get(plugin)
        if not isinstance(info, dict):
            return ()
        chars = info.get("characters")
        return tuple(chars) if isinstance(chars, (list, tuple)) else ()

    def register_command(self, cmd: _Command) -> None:
        if not cmd.characters:
            cmd.characters = self._chars_of(cmd.plugin)
        keys = [cmd.name.lower()] + [a.lower() for a in cmd.aliases]
        # 现在所有角色限定插件都会注册，跨角色的同名命令（如通用插件的别名
        # 「抱抱」和亚托莉的「抱抱」）必须能共存 —— 否则一个角色的命令会把
        # 另一个角色的整条命令挤掉（老行为：整条丢弃 + 静默）。
        # 规则：两份的命令**角色集合不相交**就都留下，按会话角色挑；
        # 相交（都是通用的 / 同角色重名）才按老规矩丢弃并告警。
        if any(k in self.commands for k in keys):
            clash = [self._cmd_all.get(k) or [self.commands.get(k)] for k in keys if k in self.commands]
            # 只要**有一方**是角色限定、且两边的角色集合不相交，就都留下：
            # 通用 + 限定（.抱抱 这种别名撞车）也算 —— 限定那条只在自己的
            # 角色里被挑中，通用那条兜住其它角色。两条都是通用才叫真重名。
            coexist = all(
                (other.characters or cmd.characters)
                and not (set(other.characters) & set(cmd.characters))
                for group in clash for other in group if other is not None
            )
            if coexist:
                # 撞上的那几个键变成「多条」，**没撞的别名照常占位** ——
                # 否则一个别名撞了名，这条命令的其它别名会跟着一起消失
                # （`.抱抱` 撞了，`.拥抱` 明明没人用却敲不出来）。
                for k in keys:
                    if k not in self.commands:
                        self.commands[k] = cmd
                    self._cmd_all.setdefault(k, []).append(cmd)
                return
        if any(k in self.commands for k in keys):
            logger.warning("OneBot: 命令 %s（来自 %s）与已有命令重名，忽略", cmd.name, cmd.plugin)
            return
        for k in keys:
            self.commands[k] = cmd
            self._cmd_all.setdefault(k, []).append(cmd)

    def register_message_hook(self, hook: _Hook) -> None:
        if not hook.characters:
            hook.characters = self._chars_of(hook.plugin)
        self.hooks_in.append(hook)

    def register_reply_hook(self, hook: _Hook) -> None:
        if not hook.characters:
            hook.characters = self._chars_of(hook.plugin)
        self.hooks_out.append(hook)

    def register_notice_hook(self, hook: _Hook) -> None:
        if not hook.characters:
            hook.characters = self._chars_of(hook.plugin)
        self.hooks_notice.append(hook)

    def register_request_hook(self, hook: _Hook) -> None:
        if not hook.characters:
            hook.characters = self._chars_of(hook.plugin)
        self.hooks_request.append(hook)

    def register_nlu(self, pattern: str, cmd: str, plugin: str, priority: int = 100) -> None:
        """注册一条「自然语言 → 命令」规则（坏正则只告警不炸加载）。"""
        try:
            rx = re.compile(str(pattern))
        except re.error as e:
            logger.warning("OneBot: NLU 规则编译失败（%s · %s）— %s", plugin, str(pattern)[:40], e)
            return
        self._nlu_seq += 1
        self.nlu_rules.append(_NluRule(pattern=rx, cmd=str(cmd), plugin=plugin,
                                       priority=int(priority), seq=self._nlu_seq,
                                       characters=self._chars_of(plugin)))

    def set_character(self, cid: str, info: dict | None = None) -> None:
        """切换当前角色并重新装载（角色限定插件的启用状态由 active_character 决定）。"""
        with self._lock:
            self.active_character = str(cid or "klee")
            self.active_character_info = dict(info or {})
        self.load()
        logger.info("插件层已按角色 %s 重载：%d 个启用",
                    self.active_character,
                    sum(1 for p in self.plugins.values() if p.get("enabled")))

    def nlu_match(self, text: str, character: str = "") -> Optional[str]:
        """整句匹配自然语言规则 → 返回替换后的命令行（不含前缀）；没命中返回 None。"""
        t = (text or "").strip()
        if not t:
            return None
        with self._lock:
            rules = sorted(self.nlu_rules, key=lambda r: (r.priority, r.seq))
        for r in rules:
            if not self._char_ok(getattr(r, "characters", ()), character):
                continue
            m = r.pattern.fullmatch(t)
            if not m:
                continue
            args = [str(g or "").strip() for g in m.groups()]
            try:
                out = r.cmd.format(*([""] + args))     # {1}＝第 1 个捕获组（0 号位垫底）
            except (IndexError, KeyError, ValueError):
                out = re.sub(r"\{\w+\}", "", r.cmd)     # 模板占位符多于分组时的兜底
            out = " ".join(out.split())
            if out:
                logger.info("OneBot: NLU 命中（%s）「%s」→ .%s", r.plugin, t[:30], out[:40])
                return out
        return None

    def sorted_commands(self) -> List[_Command]:
        uniq = {id(c): c for c in self.commands.values()}
        return sorted(uniq.values(), key=lambda c: (c.priority, c.name))

    def all_commands(self) -> List[_Command]:
        """所有注册上的命令（含同名不同角色的），体检/快照用。"""
        with self._lock:
            items = [c for group in self._cmd_all.values() for c in group]
        return {id(c): c for c in items}.values()

    def cmd_for(self, name: str, character: str = "") -> Optional[_Command]:
        """按名字取「这个角色此刻真的能用」的那条命令；用不了 → None。

        公开版本（`_pick_cmd` + 角色门控）：技能通路 / 试跑 / 任何绕过
        `explain_command` 的入口都必须走它，否则「分发时按角色门控」就有洞
        —— 曾出现「亚托莉的群通过 use_skill 跑起可莉的插件」。
        """
        cmd = self._pick_cmd(name, character)
        if cmd is None:
            return None
        return cmd if self._char_ok(getattr(cmd, "characters", ()), character) else None

    def _pick_cmd(self, name: str, character: str = "") -> Optional[_Command]:
        """同名多条命令时，挑这个角色该用的那条。

        优先「角色专属且命中」→ 其次「通用」→ 最后退回第一条（由调用方按
        角色门控决定它可不可用）。
        """
        with self._lock:
            group = list(self._cmd_all.get(str(name or "").lower()) or [])
        if not group:
            return None
        who = self._char_now(character)
        for c in group:
            if c.characters and who in c.characters:
                return c
        for c in group:
            if not c.characters:
                return c
        return group[0]

    def _register_builtin(self) -> None:
        """框架内置的 .features 管理命令（主人私聊）。"""

        async def _features_cmd(ctx: FeatureContext) -> None:
            if ctx.args and ctx.args[0].lower() in ("reload", "重载"):
                # 装载要 import/exec 上百个文件，放线程里跑，别卡住事件循环
                # （卡住期间连心跳都发不出去）。
                await asyncio.to_thread(self.load)
                await ctx.reply("🧩 插件已重载")
                return
            lines = ["🧩 可莉的插件："]
            for n, p in sorted(self.plugins.items()):
                mark = "✅" if p["enabled"] else "⏸️"
                warn = " ⚠️" if p.get("error") else ""
                lines.append(f" {mark} {n}{warn} —— {p.get('description') or '（无描述）'}")
            lines.append("（.features reload 可以重载）")
            await ctx.reply("\n".join(lines))

        self.register_command(_Command(name="features", handler=_features_cmd,
                                       aliases=("插件",), scope="dm", permission="owner",
                                       cooldown=0, help=".features —— 插件列表 / reload",
                                       plugin="_builtin", priority=0))

    # ---------- 分发 ----------
    def _split_command(self, text: str) -> Optional[Tuple[str, List[str]]]:
        t = (text or "").lstrip()
        if not t or t[0] not in self.prefix_chars:
            return None
        body = t[1:].strip()
        if not body:
            return None
        parts = body.split()
        return parts[0], parts[1:]

    def explain_command(self, ctx: FeatureContext) -> dict:
        """命令这一层的判定 —— **不执行 handler、不改冷却**（纯查表）。

        分发（`handle_command`）和面板的「模拟器 / 命令中心」都走这里，
        这样「试跑说会执行」和「聊天里真的执行」不可能对不上（以前两边
        各写一份判断，改一边就漂）。

        返回 `{token, args, cmd, action, reason}`，action 三选一：
          - `none`：根本不是命令（或没有这个命令）→ 继续后面的流程；
          - `skip`：是命令但不该执行（作用域/权限/冷却不过）→ 消息被吃掉；
          - `run` ：会执行。
        """
        parsed = self._split_command(ctx.text)
        if not parsed:
            # 形状要和「有命令」的那几个分支一致：调用方会直接读 token/args/cmd
            return {"action": "none", "token": "", "args": [], "cmd": None,
                    "kind": "prefix", "reason": "不是命令（没有前缀）"}
        token, args = parsed
        with self._lock:                    # 热重载会在另一个线程重置字典
            cmd = self._pick_cmd(token, getattr(ctx, "character", ""))
        if cmd is None:
            return {"action": "none", "token": token, "args": args, "cmd": None,
                    "kind": "unknown", "reason": f"没有命令「{token}」"}
        # 角色限定命令：本会话不是它的角色 → 当作「没有这条命令」。
        # 为什么是 none 而不是 skip：skip 会把消息**静默吃掉**；none 则继续
        # 当成普通聊天（和「这个角色根本没有这条命令」完全一致，也是今天的行为）。
        if not self._char_ok(getattr(cmd, "characters", ()), getattr(ctx, "character", "")):
            return {"action": "none", "token": token, "args": args, "cmd": cmd,
                    "kind": "character",
                    "reason": (f"「{cmd.name}」是 {'/'.join(cmd.characters)} 的专属命令，"
                               f"本会话的角色是 {self._char_now(getattr(ctx, 'character', ''))}")}
        scope, perm = cmd.scope, cmd.permission
        source = "插件默认"
        try:
            eff = _cmdpolicy.effective(cmd, getattr(self, "overrides", {}) or {})
            scope, perm = eff["scope"], eff["permission"]
            if eff["override"]:
                source = "面板覆盖"
        except Exception as e:                                     # noqa: BLE001
            # 覆盖表坏了就退回插件默认 —— 但必须留痕，否则「明明设了却不生效」
            # 无从排查（正是这个功能要消灭的失败方式）。
            logger.warning("OneBot: 命令权限覆盖读取失败（按插件默认）：%s", e)
        out = {"token": token, "args": args, "cmd": cmd, "scope": scope,
               "permission": perm, "rule_source": source}
        # 判定本体在 cmdpolicy 里（allowed/scope_ok），这里不再内联一份 ——
        # 以后加权限级别只改一个地方。
        if not _cmdpolicy.scope_ok(scope, is_group=ctx.is_group):
            if scope == "group":
                return {**out, "action": "skip", "kind": "scope",
                        "reason": "这条命令只在群里可用"}
            return {**out, "action": "skip", "kind": "scope",
                    "reason": "这条命令只在私聊可用"}
        if not _cmdpolicy.allowed(perm, is_owner=ctx.is_owner, is_admin=ctx.is_admin):
            return {**out, "action": "skip", "kind": "permission",
                    "reason": ("只有主人能用" if perm == "owner" else "只有群主/群管理能用")}
        if cmd.cooldown > 0:
            key = (cmd.name.lower(), ctx.user_id)
            if time.monotonic() < self.cooldowns.get(key, 0.0):
                return {**out, "action": "skip", "kind": "cooldown",
                        "reason": f"冷却中（{cmd.cooldown:g} 秒一次）", "cooldown": cmd.cooldown}
            out["cooldown"] = cmd.cooldown
        return {**out, "action": "run", "kind": "ok"}

    async def handle_command(self, ctx: FeatureContext) -> bool:
        """命中并消费返回 True；没命中返回 False（继续正常流程）。"""
        verdict = self.explain_command(ctx)
        action = verdict["action"]
        if action == "none":
            return False
        cmd = verdict["cmd"]
        if action == "skip":
            # 作用域 / 权限 / 冷却 —— 任何一项不过都静默吞掉（别喂给 LLM）。
            # 用 verdict["kind"] 分类，不靠中文 reason 前缀（改个措辞就会静默失效）。
            kind = verdict.get("kind")
            if kind in ("scope", "permission"):
                logger.info("OneBot: 命令 .%s 被拦下（%s %s role=%s）",
                            cmd.name, verdict["reason"], ctx.user_id, ctx.role or "-")
                self._bump(f"{cmd.plugin}:denied")
            else:
                logger.info("OneBot: 命令 .%s %s（%s）", cmd.name, verdict["reason"], ctx.user_id)
            return True
        args = verdict["args"]
        if cmd.cooldown > 0:
            key = (cmd.name.lower(), ctx.user_id)
            now = time.monotonic()
            if len(self.cooldowns) > 2000:      # 长跑防膨胀：过期项清一波（OCR low）
                self.cooldowns = {k: v for k, v in self.cooldowns.items() if v > now}
            self.cooldowns[key] = now + cmd.cooldown

        ctx.command = cmd.name
        ctx.args = args
        try:
            res = cmd.handler(ctx)
            if inspect.isawaitable(res):
                res = await res
            if isinstance(res, str) and res.strip():
                await ctx.reply(res)
            self._bump(cmd.plugin)
            logger.info("OneBot: 命令 .%s → 插件 %s（%s %s）", cmd.name, cmd.plugin,
                        ctx.user_id, " ".join(args)[:40])
        except Exception:                                     # noqa: BLE001
            self.errors[cmd.plugin] = self.errors.get(cmd.plugin, 0) + 1
            logger.exception("OneBot: 命令 .%s（插件 %s）执行异常", cmd.name, cmd.plugin)
        return True

    async def run_message_hooks(self, ctx: FeatureContext) -> bool:
        """入站钩子链；返回 False = 消息被拦下（不再发往 agent）。"""
        with self._lock:                    # 快照：热重载会在别的线程重建这个列表
            hooks = list(self.hooks_in)
        for hook in hooks:
            if not self._char_ok(getattr(hook, "characters", ()),
                                 getattr(ctx, "character", "")):
                continue
            try:
                res = hook.handler(ctx)
                if inspect.isawaitable(res):
                    res = await res
            except Exception:                                 # noqa: BLE001
                self.errors[hook.plugin] = self.errors.get(hook.plugin, 0) + 1
                logger.exception("OneBot: 入站钩子异常（插件 %s）", hook.plugin)
                continue
            if res is BLOCK:
                self._bump(hook.plugin)
                logger.info("OneBot: 入站钩子（%s）拦截了一条消息", hook.plugin)
                return False
            if isinstance(res, str):
                ctx.text = res
        return True

    async def run_event_hooks(self, kind: str, ev: dict, adapter=None) -> list:
        """跑 notice / request 钩子。返回 [(插件名, 返回值)]。

        `adapter` 传「这条事件是从哪个平台来的」视图；不给就用管理器自己的
        适配器（= 运行时）。多平台并存时插件靠它区分来源。

        异常只计数不抛：一个插件写错不该让「提醒主人」也失效。
        """
        with self._lock:
            hooks = list(self.hooks_notice if kind == "notice" else self.hooks_request)
        if not hooks:
            return []
        ctx = EventContext(adapter=adapter or getattr(self, "adapter", None), kind=kind,
                           raw=dict(ev or {}))
        out = []
        for hook in hooks:
            if not self._char_ok(getattr(hook, "characters", ()), ""):
                continue
            try:
                res = hook.handler(ctx)
                if inspect.isawaitable(res):
                    res = await res
            except Exception:                                 # noqa: BLE001
                self.errors[hook.plugin] = self.errors.get(hook.plugin, 0) + 1
                logger.exception("OneBot: %s 钩子异常（插件 %s）", kind, hook.plugin)
                continue
            self._bump(hook.plugin)
            out.append((hook.plugin, res))
        return out

    async def run_reply_hooks(self, chat_id: str, is_group: bool, text: str,
                              character: str = "") -> str:
        """出站钩子链；返回改写后的文本。

        `character`：这条消息是哪个角色在说（角色限定插件只改自己角色的台词）。
        不给就用 ContextVar / 全局角色。
        """

        sc = _SendCtx(chat_id=chat_id, is_group=is_group)
        with self._lock:
            hooks = list(self.hooks_out)
        for hook in hooks:
            if not self._char_ok(getattr(hook, "characters", ()), character):
                continue
            try:
                res = hook.handler(sc, text)
                if inspect.isawaitable(res):
                    res = await res
            except Exception:                                 # noqa: BLE001
                self.errors[hook.plugin] = self.errors.get(hook.plugin, 0) + 1
                logger.exception("OneBot: 出站钩子异常（插件 %s）", hook.plugin)
                continue
            if isinstance(res, str) and res != text:
                text = res
                self._bump(hook.plugin)
        return text

    def _bump(self, plugin: str) -> None:
        if plugin == "_builtin":
            return
        self.hits[plugin] = self.hits.get(plugin, 0) + 1

    # ---------- 热重载 ----------
    def _fingerprint(self) -> tuple:
        items = []
        try:
            candidates = list(self.dir.iterdir())
            ext_dir = self.dir / "ext"
            if ext_dir.is_dir():
                candidates += list(ext_dir.glob("*.ext.json"))
            for p in sorted(candidates):
                if p.suffix == ".py" or p.name in ("config.json", ".reload") \
                        or p.name.endswith(".ext.json"):
                    try:
                        st = p.stat()
                    except OSError:
                        continue
                    items.append((p.name, st.st_mtime_ns, st.st_size))
        except OSError:
            return ()
        return tuple(items)

    async def maybe_reload(self) -> bool:
        """目录有变化就重载；由适配器的看护任务定期调用。"""
        fp = self._fingerprint()
        if fp == self._fp_seen:
            return False
        await asyncio.sleep(2.0)          # 半写入保护：等文件落盘
        await asyncio.to_thread(self.load)
        logger.info("OneBot: 插件热重载完成（触发于目录变化）")
        return True

    # ---------- 快照（给 /stats 和状态文件） ----------
    def snapshot(self) -> dict:
        with self._lock:
            items = list(self.plugins.items())
            hits, errors = dict(self.hits), dict(self.errors)
            every = list(self.all_commands())
            commands = sorted({c.name for c in every})
            cmd_count: Dict[str, int] = {}
            cmd_list: Dict[str, list] = {}
            for c in every:
                if c.plugin == "__builtin__":
                    continue
                cmd_count[c.plugin] = cmd_count.get(c.plugin, 0) + 1
                cmd_list.setdefault(c.plugin, []).append({
                    "name": c.name, "aliases": list(c.aliases), "help": c.help,
                    "scope": c.scope, "permission": c.permission, "cooldown": c.cooldown,
                })
            nlu_len = len(self.nlu_rules)
            pages = set(self.pages)
        tool_rows = toolbox.catalog()
        return {
            "dir": str(self.dir),
            "loaded": self.loaded,
            "last_reload": self.last_reload_ts,
            "plugins": [
                {"name": n, "title": p.get("title", n), "description": p.get("description", ""),
                 "characters": list(p.get("characters") or []), "locked": bool(p.get("locked")),
                 "lock_reason": p.get("lock_reason", ""),
                 "version": p.get("version", ""), "usage": p.get("usage", ""),
                 "author": p.get("author", ""), "homepage": p.get("homepage", ""),
                 "schema": p.get("schema", {}),
                 "enabled": bool(p.get("enabled")), "error": p.get("error", ""),
                 "hits": int(hits.get(n, 0)), "errors": int(errors.get(n, 0)),
                 "commands": int(cmd_count.get(n, 0)),
                 "command_list": sorted(cmd_list.get(n, []), key=lambda x: x["name"]),
                 "file": p.get("file", ""),
                 # 外部插件（其他语言）：面板上要显示语言徽章与入口
                 "system": bool(p.get("system")),
                 "title_en": p.get("title_en", ""),
                 "description_en": p.get("description_en", ""),
                 "has_page": n in pages,
                 "tools": [t["name"] for t in tool_rows if t["plugin"] == n],
                 "external": bool(p.get("external")),
                 "language": p.get("language", "") or "",
                 "entry": p.get("entry", "") or "",
                 "ext_note": p.get("ext_note", "") or ""}
                for n, p in sorted(items)
            ],
            "commands": commands,
            "nlu_rules": nlu_len,
            "active_character": self.active_character,
        }

    def stats_line(self) -> str:
        """一行式统计（/stats 用）。`:denied` 哨兵键不计入命中总数（OCR 四轮 #3）。"""
        total = sum(int(v) for k, v in self.hits.items()
                    if k != "_builtin" and not k.endswith(":denied"))
        parts = [f"{n}={self.hits.get(n, 0)}" for n, p in sorted(self.plugins.items())
                 if p["enabled"]]
        return f"插件 {len(self.plugins)} 个·命中 {total}" + (f"（{'  '.join(parts)}）" if parts else "")
