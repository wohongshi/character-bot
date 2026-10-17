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

logger = logging.getLogger(__name__)


# ---- 常量 / 哨兵 ----
class _Block:
    """on_message 钩子的阻断哨兵：``return api.BLOCK`` 表示此消息不再发往 agent。"""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover
        return "<FeatureBlock>"


BLOCK = _Block()

_PREFIX_ALIASES = {".": "。", "．": None, "!": "！", "！": None}


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
        return json.loads(str(value or "null"))
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
        return self.adapter._chat_id(self.is_group, self.group_id, self.user_id)

    async def reply(self, text: str) -> None:
        """直接往当前会话发一条消息（走适配器 send：限速/引用/富文本都适用）。"""
        if text:
            # 命令输出是一个整体（菜单/结算/卡片文案），不参与「分句连发」拆条
            await self.adapter.send(self.chat_id, text, sentence_split=False)


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


@dataclass
class _Hook:
    handler: Callable
    plugin: str
    priority: int = 50


@dataclass
class _NluRule:
    """自然语言触发规则：整句 fullmatch 命中 → 消息被替换成 ``.{cmd}`` 再走命令。"""
    pattern: "re.Pattern[str]"
    cmd: str
    plugin: str
    priority: int = 100
    seq: int = 0


class FeatureAPI:
    """交给每个插件模块的注册入口（``register(api)`` 的那个 api）。"""

    def __init__(self, manager: "FeatureManager", plugin: str, settings: dict):
        self._manager = manager
        self._plugin = plugin
        self._settings = settings if isinstance(settings, dict) else {}
        self._state: Dict[str, Any] = {}          # 插件自己的内存态（重载即清）
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

    def on_nlu(self, pattern: str, cmd: str, *, priority: int = 100) -> None:
        """注册自然语言触发：整句命中后消息被替换成 `.{cmd}`（`{1}` 引用正则分组）。

        只在「对可莉说」的消息上尝试（被 @ / 被回复 / 私聊），群里聊天不会误触。
        """
        self._manager.register_nlu(pattern, cmd, self._plugin, priority=priority)

    # —— 给插件用的小工具 ——
    @property
    def character_id(self) -> str:
        return str(getattr(self._manager, "active_character", "") or "klee")

    @property
    def character(self) -> dict:
        return getattr(self._manager, "active_character_info", {}) or {}

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

    def commands_help(self) -> List[str]:
        seen, out = set(), []
        for cmd in self._manager.sorted_commands():
            if cmd.name in seen or cmd.permission == "owner":
                continue
            seen.add(cmd.name)
            if cmd.help:
                out.append(cmd.help)
            else:
                out.append(f".{cmd.name}")
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
        self.commands: Dict[str, _Command] = {}     # 小写名/别名 → _Command
        self.hooks_in: List[_Hook] = []
        self.hooks_out: List[_Hook] = []
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
        self.scaffold()
        # 复位注册表（hits/errors 跨重载保留）。
        # `plugin == "__builtin__"` 的是运行时自带的命令（如 .agent），不来自插件文件，
        # 热重载时必须原样保留，否则重载一次就「命令消失」。
        builtin = {k: c for k, c in self.commands.items() if c.plugin == "__builtin__"}
        self.commands = dict(builtin)
        self.hooks_in = []
        self.hooks_out = []
        self.nlu_rules = []
        self._nlu_seq = 0
        self.plugins = {}
        self.load_error = ""

        cfg, corrupt = self._read_config()
        if not isinstance(cfg, dict):          # 合法 JSON 但不是对象 → 当损坏处理（fail-closed）
            cfg, corrupt = {}, True
        enabled_cfg = cfg.get("enabled")
        if corrupt:
            enabled_cfg = []                      # fail-closed：坏配置不放开默认插件
        settings = cfg.get("settings") if isinstance(cfg.get("settings"), dict) else {}

        self._register_builtin()
        self._purge_modules()

        files = sorted(p for p in self.dir.glob("*.py") if not p.name.startswith("_"))
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

        self.hooks_in.sort(key=lambda h: h.priority)
        self.hooks_out.sort(key=lambda h: h.priority)
        self.loaded = True
        self.last_reload_ts = time.time()
        self._fp_seen = self._fingerprint()
        logger.info("OneBot: 插件装载完成 —— %d 个（启用 %d），命令 %d 个，NLU %d 条，钩子 入%d/出%d",
                    len(self.plugins),
                    sum(1 for p in self.plugins.values() if p["enabled"]),
                    len({id(c) for c in self.commands.values()}),
                    len(self.nlu_rules),
                    len(self.hooks_in), len(self.hooks_out))

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

    def _load_one(self, path: Path, enabled_cfg: Any, settings: dict) -> None:
        name = path.stem
        info = {"description": "", "title": name, "version": "", "enabled": False,
                "error": "", "file": path.name,
                "usage": "", "author": "", "homepage": "", "schema": {},
                "characters": [], "locked": False}
        try:
            mod_name = f"onebot_feature_{name}_{int(time.time() * 1000)}"
            spec = _ilu.spec_from_file_location(mod_name, path)
            if spec is None or spec.loader is None:
                raise RuntimeError("无法为插件文件建立 import spec")
            module = _ilu.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as e:                                # noqa: BLE001
            info["error"] = f"{type(e).__name__}: {e}"
            self.plugins[name] = info
            logger.warning("OneBot: 插件 %s 加载失败 — %s", name, info["error"])
            return

        # 元信息：优先模块变量，其次 docstring 首行
        desc = str(getattr(module, "DESCRIPTION", "") or "").strip()
        if not desc and module.__doc__:
            desc = module.__doc__.strip().splitlines()[0]
        meta = getattr(module, "__plugin_meta__", None)
        meta = meta if isinstance(meta, dict) else {}
        info["description"] = str(meta.get("description") or desc)
        info["title"] = str(meta.get("name") or getattr(module, "NAME", "") or name)
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

        if info["locked"]:
            info["enabled"] = self.active_character in info["characters"]
            info["lock_reason"] = ("当前角色可用" if info["enabled"]
                                   else "仅 " + "/".join(info["characters"]) + " 可用")
        elif enabled_cfg is None:
            info["enabled"] = not bool(getattr(module, "DEFAULT_OFF", False))
        else:
            # 字符串 = 单插件简写（旧代码把它当可迭代逐字符拆开 → 白名单静默全失效，OCR 四轮 #2）；
            # 其它不可迭代值（int 等）→ 回落默认开关。
            if isinstance(enabled_cfg, str):
                allowed = {enabled_cfg}
            else:
                try:
                    allowed = {str(x) for x in enabled_cfg}
                except TypeError:
                    allowed = None
            info["enabled"] = (not bool(getattr(module, "DEFAULT_OFF", False))
                               if allowed is None else name in allowed)

        self.plugins[name] = info
        if not info["enabled"]:
            return

        register = getattr(module, "register", None)
        if not callable(register):
            info["error"] = "没有 register(api) 函数"
            info["enabled"] = False
            logger.warning("OneBot: 插件 %s 缺少 register(api)，已跳过", name)
            return
        api = FeatureAPI(self, name, settings.get(name) or {})
        # 注册到一半抛异常时，把半截命令/钩子/NLU 撤掉，别让「已停用」的插件还在接消息
        snapshot = (dict(self.commands), list(self.hooks_in), list(self.hooks_out),
                    list(self.nlu_rules), self._nlu_seq)
        try:
            register(api)
        except Exception as e:                                # noqa: BLE001
            (self.commands, self.hooks_in, self.hooks_out,
             self.nlu_rules, self._nlu_seq) = snapshot
            info["error"] = f"register(): {type(e).__name__}: {e}"
            info["enabled"] = False
            logger.warning("OneBot: 插件 %s 注册失败（已回滚半截注册）— %s", name, info["error"])

    # ---------- 注册 ----------
    def register_command(self, cmd: _Command) -> None:
        keys = [cmd.name.lower()] + [a.lower() for a in cmd.aliases]
        if any(k in self.commands for k in keys):
            logger.warning("OneBot: 命令 %s（来自 %s）与已有命令重名，忽略", cmd.name, cmd.plugin)
            return
        for k in keys:
            self.commands[k] = cmd

    def register_message_hook(self, hook: _Hook) -> None:
        self.hooks_in.append(hook)

    def register_reply_hook(self, hook: _Hook) -> None:
        self.hooks_out.append(hook)

    def register_nlu(self, pattern: str, cmd: str, plugin: str, priority: int = 100) -> None:
        """注册一条「自然语言 → 命令」规则（坏正则只告警不炸加载）。"""
        try:
            rx = re.compile(str(pattern))
        except re.error as e:
            logger.warning("OneBot: NLU 规则编译失败（%s · %s）— %s", plugin, str(pattern)[:40], e)
            return
        self._nlu_seq += 1
        self.nlu_rules.append(_NluRule(pattern=rx, cmd=str(cmd), plugin=plugin,
                                       priority=int(priority), seq=self._nlu_seq))

    def set_character(self, cid: str, info: dict | None = None) -> None:
        """切换当前角色并重新装载（角色限定插件的启用状态由 active_character 决定）。"""
        with self._lock:
            self.active_character = str(cid or "klee")
            self.active_character_info = dict(info or {})
        self.load()
        logger.info("插件层已按角色 %s 重载：%d 个启用",
                    self.active_character,
                    sum(1 for p in self.plugins.values() if p.get("enabled")))

    def nlu_match(self, text: str) -> Optional[str]:
        """整句匹配自然语言规则 → 返回替换后的命令行（不含前缀）；没命中返回 None。"""
        t = (text or "").strip()
        if not t:
            return None
        for r in sorted(self.nlu_rules, key=lambda r: (r.priority, r.seq)):
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

    def _register_builtin(self) -> None:
        """框架内置的 .features 管理命令（主人私聊）。"""

        async def _features_cmd(ctx: FeatureContext) -> None:
            if ctx.args and ctx.args[0].lower() in ("reload", "重载"):
                self.load()
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

    async def handle_command(self, ctx: FeatureContext) -> bool:
        """命中并消费返回 True；没命中返回 False（继续正常流程）。"""
        parsed = self._split_command(ctx.text)
        if not parsed:
            return False
        token, args = parsed
        cmd = self.commands.get(token.lower())
        if cmd is None:
            return False

        # 作用域 / 权限 / 冷却 —— 任何一项不过都静默吞掉（别喂给 LLM）
        if cmd.scope == "group" and not ctx.is_group:
            return True
        if cmd.scope == "dm" and ctx.is_group:
            return True
        ok = (cmd.permission == "all"
              or (cmd.permission == "owner" and ctx.is_owner)
              or (cmd.permission == "admin" and (ctx.is_admin or ctx.is_owner)))
        if not ok:
            logger.info("OneBot: 命令 .%s 权限不足（%s role=%s）", cmd.name, ctx.user_id, ctx.role or "-")
            self._bump(f"{cmd.plugin}:denied")
            return True
        if cmd.cooldown > 0:
            key = (cmd.name.lower(), ctx.user_id)
            now = time.monotonic()
            if now < self.cooldowns.get(key, 0.0):
                logger.info("OneBot: 命令 .%s 冷却中（%s）", cmd.name, ctx.user_id)
                return True
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
        for hook in self.hooks_in:
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

    async def run_reply_hooks(self, chat_id: str, is_group: bool, text: str) -> str:
        """出站钩子链；返回改写后的文本。"""

        sc = _SendCtx(chat_id=chat_id, is_group=is_group)
        for hook in self.hooks_out:
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
            for p in sorted(self.dir.iterdir()):
                if p.suffix == ".py" or p.name in ("config.json", ".reload"):
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
            commands = sorted({c.name for c in self.sorted_commands()})
            cmd_count: Dict[str, int] = {}
            cmd_list: Dict[str, list] = {}
            for c in {id(x): x for x in self.commands.values()}.values():
                if c.plugin == "__builtin__":
                    continue
                cmd_count[c.plugin] = cmd_count.get(c.plugin, 0) + 1
                cmd_list.setdefault(c.plugin, []).append({
                    "name": c.name, "aliases": list(c.aliases), "help": c.help,
                    "scope": c.scope, "permission": c.permission, "cooldown": c.cooldown,
                })
            nlu_len = len(self.nlu_rules)
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
                 "file": p.get("file", "")}
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
