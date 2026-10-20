"""命令的作用域与权限覆盖：不改插件代码，也能收紧「谁能用」。

为什么要它
----------
框架本来就有 `scope`（all/group/dm）和 `permission`（all/admin/owner），
但**150 多个插件里没有一个用到** —— 也就是说群里任何人敲 `.待办清 确认`
都能清空整个群的待办，`.记忆` 这类也是谁都能用。改插件源码不现实：
同一个插件装在不同群里，想要的权限也不一样。

所以覆盖放在**框架层**、单独一个文件（`bot/features/overrides.json`）：

    {"commands": {"待办清": {"scope": "dm", "permission": "owner"}},
     "plugins":  {"gacha":  {"permission": "all"}}}

- 命令级优先于插件级；
- 没写就等于用插件声明的默认值（**默认行为一点不变**）；
- 只认白名单里的值，写错的不生效（宁可按默认，也不要因为一个错字把所有命令锁死）。

为什么单独一个文件而不是塞进 `config.json`：那个文件会被插件启停、设置保存
反复整份重写，多一个键就容易在某个分支里被丢掉。覆盖规则必须稳定可读。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

# 插件里声明的默认值是 "both"（= 都行）；覆盖表里允许写 both，等价于 all。
SCOPES = ("all", "both", "group", "dm")
PERMISSIONS = ("all", "admin", "owner")

_LOCK = threading.RLock()
FILENAME = "overrides.json"


class PolicyError(ValueError):
    """覆盖规则不合法（消息直接给人看）。"""


def path_for(features_dir: Path | str) -> Path:
    return Path(features_dir) / FILENAME


def load(features_dir: Path | str) -> dict:
    """读覆盖表；坏文件按空处理（规则丢一次，比整个插件目录起不来强）。"""
    p = path_for(features_dir)
    try:
        d = json.loads(p.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        return {"commands": {}, "plugins": {}}
    if not isinstance(d, dict):
        return {"commands": {}, "plugins": {}}
    out = {"commands": {}, "plugins": {}}
    for kind in ("commands", "plugins"):
        src = d.get(kind)
        if not isinstance(src, dict):
            continue
        for name, ov in src.items():
            clean = _clean(ov)
            if clean:
                out[kind][str(name)] = clean
    return out


def _clean(ov: dict) -> dict:
    """只留下合法的键值；一条都不合法就返回空（等于没写）。"""
    if not isinstance(ov, dict):
        return {}
    out = {}
    scope = str(ov.get("scope") or "").strip().lower()
    if scope in SCOPES:
        out["scope"] = scope
    perm = str(ov.get("permission") or "").strip().lower()
    if perm in PERMISSIONS:
        out["permission"] = perm
    return out


def save(features_dir: Path | str, data: dict) -> dict:
    p = path_for(features_dir)
    with _LOCK:                          # RLock：save 会被 set_override 在外层再拿一次
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)
    return load(features_dir)


def set_override(features_dir: Path | str, *, command: str = "", plugin: str = "",
                 scope: str | None = None, permission: str | None = None) -> dict:
    """设一条覆盖（**按字段**）。

    `None` = 这一项不动；`""` = 清掉这一项；两项都清 → 整条删掉（回到插件默认）。
    这个区分很要紧：面板是先设权限、再改作用域的，如果「没给」和「清空」
    长得一样，第二步就会把第一步悄悄冲掉。
    """
    command = str(command or "").strip()
    plugin = str(plugin or "").strip()
    if not command and not plugin:
        raise PolicyError("要么给命令名，要么给插件名")
    scope = None if scope is None else str(scope).strip().lower()
    permission = None if permission is None else str(permission).strip().lower()
    if scope and scope not in SCOPES:
        raise PolicyError(f"作用域只能是 {'/'.join(SCOPES)}")
    if permission and permission not in PERMISSIONS:
        raise PolicyError(f"权限只能是 {'/'.join(PERMISSIONS)}")
    # 读-改-写整体串行：面板是 ThreadingHTTPServer，两个标签页同时保存时
    # 后写的会覆盖先写的（丢的是权限规则，属于用户可见的配置丢失）。
    with _LOCK:
        data = load(features_dir)
        # 命令 + 插件都给 = 指定「这一条」（同名命令不会串）；
        # 只给命令 = 老写法，作用于所有同名命令（向后兼容）。
        kind = "commands" if command else "plugins"
        key = f"{plugin}:{command}" if (command and plugin) else (command or plugin)
        # **按字段合并**，不是整条替换：面板每次只提交改动的那个字段
        # （改权限时只发 permission），整条替换会把上一步设的作用域冲掉 ——
        # 更糟的是「先设仅主人、再改作用域」会把权限悄悄恢复成所有人可用。
        row = dict((data.get(kind) or {}).get(key) or {})
        for k, v in (("scope", scope), ("permission", permission)):
            if v is None:
                continue                     # 没提这一项 → 不动
            if v:
                row[k] = v
            else:
                row.pop(k, None)             # 显式给空串 → 清掉
        clean = _clean(row)
        if clean:
            data.setdefault(kind, {})[key] = clean
        else:
            (data.get(kind) or {}).pop(key, None)
            if command:
                # 兼容老数据：升级前写的是纯命令名，只清新键的话旧键继续生效，
                # 面板会显示「已回到插件默认」但其实没回
                (data.get(kind) or {}).pop(command, None)
        return save(features_dir, data)


def effective(cmd: object, data: dict) -> dict:
    """命令的**实际**作用域与权限（插件级 < 命令级 < 插件声明）。"""
    name = str(getattr(cmd, "name", "") or "")
    plugin = str(getattr(cmd, "plugin", "") or "")
    cmds = data.get("commands") or {}
    # 「插件:命令」优先，但**逐字段**合并：老版本面板写的是纯命令名，
    # 如果新键整行遮住旧键，只改了作用域的那次保存会把旧的「仅主人」丢掉
    # （等于悄悄放宽权限）—— 这正是按字段合并要防的那类静默回归。
    legacy = cmds.get(name) or {}
    qual = cmds.get(f"{plugin}:{name}") or {}
    cmd_ov = ({**legacy, **qual} if (legacy or qual) else {})
    plug_ov = (data.get("plugins") or {}).get(plugin) or {}
    scope = str(cmd_ov.get("scope") or plug_ov.get("scope") or "") \
        or str(getattr(cmd, "scope", "all") or "all")
    perm = str(cmd_ov.get("permission") or plug_ov.get("permission") or "") \
        or str(getattr(cmd, "permission", "all") or "all")
    return {"scope": scope, "permission": perm,
            "override": "command" if cmd_ov else ("plugin" if plug_ov else "")}


def allowed(perm: str, *, is_owner: bool, is_admin: bool) -> bool:
    """这个身份能不能用这条命令。"""
    if perm == "owner":
        return bool(is_owner)
    if perm == "admin":
        return bool(is_owner or is_admin)
    return True


def scope_ok(scope: str, *, is_group: bool) -> bool:
    if scope in ("all", "both", ""):
        return True
    if scope == "group":
        return bool(is_group)
    if scope == "dm":
        return not is_group
    return True
