"""Agent 工具的注册表 —— 工具即插件。

设计口径
--------
- **一张表，谁都能读**：`TOOLS[name]` → `{name, description, parameters, handler, plugin}`。
  `agent.run()` 只认这张表，所以「插件关掉 = 工具从模型的可调用列表里消失」，
  而不是「还在列表里、点了报错」。
- **两个写入方**：
  1. `FeatureAPI.add_tool()` —— 插件装载时（热重载会清空重建）；
  2. `bootstrap()` —— 没有 FeatureManager 的场合（单测、命令行）用同一批插件文件补上。
- **名字是全局命名空间**：重名直接拒（后注册的赢不了），避免两个插件抢同一个工具。

为什么 `bootstrap()` 不放在模块导入时：插件文件会 `from bot.agent import ...`，
在这里导入它们会形成循环。改由 `bot.agent` 在**自己定义完之后**调一次。
"""
from __future__ import annotations

import importlib.util as _ilu
import logging
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List

log = logging.getLogger("klee.bot.toolbox")

# 默认工具集（顺序即面板里的展示顺序）。配置没写 agent_tools 时用它。
DEFAULT_TOOLS: List[str] = [
    "web_search", "web_fetch", "kb_search", "now", "calc",
    "list_skills", "read_skill", "use_skill", "remember", "recall",
    # 日常小工具：换算 / 倒数日 / 待办 —— 装上就能用，不必先去面板勾选
    "convert_unit", "days_until", "todo",
]

# 带工具的插件（相对 bot/features）。bootstrap 用；也是「工具插件」的权威清单。
# 这份清单同时决定「新装插件默认开」——漏一个，那次升级就会静默瞎掉一个工具。
TOOL_PLUGINS: List[str] = ["web_tools", "utility", "notes", "knowledge", "skillbox",
                          "units", "countdowns", "todo"]

TOOLS: Dict[str, dict] = {}
_LOCK = threading.RLock()
_BOOTED = False
# bootstrap 登记的工具挂这个「临时 owner」：真正的 FeatureManager 装载完之后
# 它们要被清掉。否则用户关掉某个工具插件，模型那边照样能调 ——
# 「插件关掉 = 工具消失」这条就不成立了（bootstrap 注册时 owner=None，谁都不清它）。
_BOOT_OWNER = object()


class ToolError(ValueError):
    """工具声明不合法（名字空、没 handler、重名）。"""


def register(plugin: str, name: str, handler: Callable, *, description: str = "",
             parameters: dict | None = None, system: bool = False,
             owner: Any = None, characters=()) -> None:
    """登记一个工具。同名覆盖（热重载时后装的为准）。

    `owner` 是「谁装的」（一般是 FeatureManager 实例）。有了它，
    第二个 manager 装载自己的插件目录时不会把别人的工具表清空
    —— 单测里同时存在多个 manager 时踩过这个坑。
    """
    n = str(name or "").strip()
    if not n:
        raise ToolError("工具名不能为空")
    if not callable(handler):
        raise ToolError(f"工具 {n} 没有可调用的 handler")
    spec = {
        "name": n,
        "description": str(description or "").strip(),
        "parameters": parameters or {"type": "object", "properties": {}},
        "handler": handler,
        "plugin": str(plugin or ""),
        "system": bool(system),
        "owner": owner,
        # 角色限定插件登记的工具只对该角色可见（空 = 通用）。
        # 现在没有角色限定插件登记工具，但会话绑定之后这件事随时会成立：
        # 漏了它，胡桃的工具会出现在可莉的群里。
        "characters": tuple(str(x).strip().lower() for x in (characters or ()) if str(x).strip()),
    }
    with _LOCK:
        TOOLS[n] = spec


def unregister_plugin(plugin: str, owner: Any = None) -> int:
    """摘掉某个插件的全部工具（插件被关掉 / 被热重载时）。

    `owner` 给了就只摘这个 owner 装的（别把别的 manager 的一起端了）。
    """
    with _LOCK:
        gone = [k for k, v in TOOLS.items()
                if v.get("plugin") == plugin and (owner is None or v.get("owner") is owner)]
        for k in gone:
            TOOLS.pop(k, None)
    return len(gone)


def reset(owner: Any = None) -> None:
    """清空工具表。

    `owner=None` 清全部（bootstrap 用）；给了 owner 只清这个 manager 装的。
    整体装载时调的是 `reset(self)` —— 之前的版本无条件 `TOOLS.clear()`，
    结果同一进程里只要有第二个 manager 装载，第一个的工具就全没了。
    """
    with _LOCK:
        if owner is None:
            TOOLS.clear()
            return
        for k in [k for k, v in TOOLS.items() if v.get("owner") is owner]:
            TOOLS.pop(k, None)


def names() -> List[str]:
    with _LOCK:
        return list(TOOLS.keys())


def get(name: str) -> dict | None:
    with _LOCK:
        return TOOLS.get(str(name or ""))


def has(name: str) -> bool:
    with _LOCK:
        return str(name or "") in TOOLS


def resolve(configured: Any) -> List[str]:
    """把配置里的工具名收敛成合法列表。

    - 没配置（None）→ 默认集里**当前真的注册过**的那些
    - 配置了（哪怕是空串/空列表）→ 只保留合法且已注册的；全非法就是空，**不回退全集**
      （拼错一个名字就把 web_fetch/use_skill 全打开，属于 fail-open）
    """
    if configured is None:
        base = list(DEFAULT_TOOLS)
    else:
        if isinstance(configured, str):
            import re
            base = [x for x in re.split(r"[,，\s]+", configured) if x]
        else:
            try:
                base = [str(x).strip() for x in configured]
            except TypeError:
                base = []
    from . import chat_char
    who = chat_char.current()          # "" = 没有会话上下文（面板 / 单测）→ 不过滤
    out: List[str] = []
    with _LOCK:
        for n in base:
            n = str(n).strip()
            if not n or n in out:
                continue
            t = TOOLS.get(n)
            if t is None:
                continue
            own = t.get("characters") or ()
            if own and who and who not in own:
                continue
            out.append(n)
    return out


def specs(configured: Any) -> List[dict]:
    """OpenAI tools 参数里的 `tools=[...]`。"""
    out = []
    for n in resolve(configured):
        t = get(n) or {}
        out.append({
            "type": "function",
            "function": {
                "name": n,
                "description": t.get("description") or n,
                "parameters": t.get("parameters") or {"type": "object", "properties": {}},
            },
        })
    return out


def catalog() -> List[dict]:
    """面板用：全部已注册工具（不含 handler）。"""
    with _LOCK:
        items = list(TOOLS.values())
    order = {n: i for i, n in enumerate(DEFAULT_TOOLS)}
    items.sort(key=lambda t: (order.get(t["name"], 999), t["name"]))
    return [{"name": t["name"], "description": t["description"],
             "plugin": t["plugin"], "system": t["system"]} for t in items]


# ---------------------------------------------------------------- 无框架启动
class _MiniAPI:
    """bootstrap 用的极简 api：只接 add_tool，其余注册动作当没发生。"""

    def __init__(self, plugin: str):
        self._plugin = plugin

    def add_tool(self, name, *, description="", parameters=None, handler=None, system=False):
        register(self._plugin, name, handler, description=description,
                 parameters=parameters, system=system, owner=_BOOT_OWNER)

    def __getattr__(self, item):                    # on_command / on_message …
        return lambda *a, **kw: None


def bootstrap(features_dir: str | Path | None = None, *, force: bool = False) -> int:
    """没有 FeatureManager 时，用插件文件把工具表填上（单测 / 命令行）。

    返回登记到的工具数。已经 bootstrap 过就直接返回（除非 force）。
    """
    global _BOOTED
    with _LOCK:
        if _BOOTED and not force:
            return len(TOOLS)
        _BOOTED = True
    d = Path(features_dir) if features_dir else Path(__file__).with_name("features")
    # 和 FeatureManager 一样把插件目录放进 sys.path：插件之间会 `from _plugstore import …`，
    # 少了这一步，bootstrap 出来的工具会以 ImportError 静默消失。
    added = str(d) not in sys.path
    if added:
        sys.path.append(str(d))
    n = 0
    try:
        for stem in TOOL_PLUGINS:
            path = d / f"{stem}.py"
            if not path.exists():
                log.warning("toolbox: 工具插件缺失 %s", path)
                continue
            mod_name = f"klee_toolplugin_{stem}"
            try:
                spec = _ilu.spec_from_file_location(mod_name, path)
                if spec is None or spec.loader is None:
                    raise RuntimeError("建不了 import spec")
                mod = _ilu.module_from_spec(spec)
                sys.modules[mod_name] = mod
                spec.loader.exec_module(mod)
                reg = getattr(mod, "register", None)
                if not callable(reg):
                    raise RuntimeError("没有 register(api)")
                before = len(TOOLS)
                reg(_MiniAPI(stem))
                n += len(TOOLS) - before
            except Exception as e:                              # noqa: BLE001
                log.warning("toolbox: 工具插件 %s 加载失败 — %s: %s", stem, type(e).__name__, e)
    finally:
        # sys.path 的恢复放 finally：插件抛 BaseException（Ctrl-C / SystemExit）时
        # 也要把这条临时路径摘掉，否则它会留在进程里影响后续导入。
        if added:
            try:
                sys.path.remove(str(d))
            except ValueError:
                pass
    return n
