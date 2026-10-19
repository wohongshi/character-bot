"""插件包（.zip）：导出、卸载。

导入那一半在 `server.py`（`_plugin_zip_files` + 两阶段写入）—— 那边已经跑了很久，
规则也定死了：**zip 顶层只能是 `<小写名>.py`**，`plugin.json` / `README.md` 会被忽略。
所以这里的导出必须产出同一种形状，导出的包才能被同一个入口装回去（也能直接发给别人）。

为什么不复用导入的校验函数
--------------------------
那份代码长在 server 里，依赖 `FEATURES_DIR` / 全局状态；打包这边要能被单测
直接喂临时目录。两边共享的是**格式约定**，不是代码：这里的 `NAME_RE` 与
server 的 `PLUGIN_NAME_RE` 一致，导出时也只写顶层文件。

卸载不删文件，移到 `state/trash/`：插件是用户自己写的东西，误点一次不该消失。
"""

from __future__ import annotations

import ast
import io
import json
import re
import shutil
import time
import zipfile
from pathlib import Path

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")     # 与 server.PLUGIN_NAME_RE 同口径
# 导入入口把 **所有** 非目录条目都算进 20 个上限里（连 plugin.json / README.md 也算，
# 它在跳过这两个之前就计数了）。而我们每个包都会写这两份说明，所以自己只能放 18 个 .py。
MAX_FILES = 18
MAX_BYTES = 2 * 1024 * 1024

_META_ATTRS = ("NAME", "NAME_EN", "DESCRIPTION", "DESCRIPTION_EN",
               "VERSION", "CHARACTERS", "SYSTEM")


class PackError(ValueError):
    """打包/卸载被拒（消息直接给人看）。"""


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def module_meta(src: str) -> dict:
    """读插件源码里的模块级常量 + 注册了哪些命令（**不执行插件**）。

    导入一个陌生 .py 就等于让它跑一遍，那是最不该做的事；
    这里只解析 AST，取字面量和 `api.on_command("…")` 的字符串参数。
    """
    out: dict = {"commands": [], "aliases": []}
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        raise PackError(f"语法不对：第 {e.lineno} 行 {e.msg}") from None
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id in _META_ATTRS:
            try:
                out[node.targets[0].id] = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                pass
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr in ("on_command", "on_message")):
            continue
        if fn.attr == "on_command" and node.args and isinstance(node.args[0], ast.Constant):
            cmd = str(node.args[0].value or "").strip()
            if cmd:
                out["commands"].append(cmd)
        for kw in node.keywords:
            if kw.arg == "aliases" and isinstance(kw.value, (ast.List, ast.Tuple)):
                for el in kw.value.elts:
                    if isinstance(el, ast.Constant) and str(el.value or "").strip():
                        out["aliases"].append(str(el.value).strip())
    return out


def manifest_for(name: str, src: str) -> dict:
    m = module_meta(src)
    chars = m.get("CHARACTERS")
    return {"name": name,
            "title": str(m.get("NAME") or name),
            "title_en": str(m.get("NAME_EN") or ""),
            "description": str(m.get("DESCRIPTION") or ""),
            "description_en": str(m.get("DESCRIPTION_EN") or ""),
            "version": str(m.get("VERSION") or ""),
            "characters": [str(x) for x in chars] if isinstance(chars, (list, tuple)) else [],
            "system": bool(m.get("SYSTEM")),
            "commands": m["commands"],
            "aliases": m["aliases"],
            "file": f"{name}.py",
            "exported_at": int(time.time())}


def _readme(man: dict, sources: list[str]) -> str:
    lines = [f"# {man.get('title') or man['name']}", ""]
    if man.get("description"):
        lines += [man["description"], ""]
    lines += [f"- 插件名：`{man['name']}`",
              f"- 文件：{'、'.join('`' + s + '`' for s in sources)}"]
    if man.get("version"):
        lines.append(f"- 版本：{man['version']}")
    if man.get("characters"):
        lines.append("- 只在这些角色下启用：" + "、".join(man["characters"]))
    if man.get("commands"):
        lines.append("- 命令：" + "、".join("`." + c + "`" for c in man["commands"]))
    lines += ["", "## 怎么装", "",
              "面板 → 插件 → 📦 安装插件包，选这个 zip；或者解压后把 `.py` 放进 `bot/features/`。",
              "", "> 这个 zip 里的 `plugin.json` 与 `README.md` 只是说明，安装时会被忽略。"]
    return "\n".join(lines) + "\n"


def pack(features_dir: Path | str, names, *, filename: str = "") -> tuple[bytes, str]:
    """把一到多个插件打成 zip（形状与导入入口一致：顶层只有 .py）。

    返回 `(bytes, 建议文件名)`。
    """
    root = Path(features_dir)
    if isinstance(names, str):
        names = [names]
    clean: list[str] = []
    for n in names:
        n = str(n or "").strip()
        if not NAME_RE.match(n):
            raise PackError(f"插件名不合法：{n!r}")
        p = root / f"{n}.py"
        if not p.exists():
            raise PackError(f"没有这个插件：{n}")
        clean.append(n)
    if not clean:
        raise PackError("没有选中插件")
    if len(clean) > MAX_FILES:
        raise PackError(f"一次最多打包 {MAX_FILES} 个插件")

    sources = {n: _read(root / f"{n}.py") for n in clean}
    metas = [manifest_for(n, sources[n]) for n in clean]
    total = sum(len(v.encode("utf-8")) for v in sources.values())
    if total > MAX_BYTES:
        raise PackError(f"源码合计 {total // 1024} KB，超过上限 {MAX_BYTES // 1024} KB")

    man = (metas[0] if len(metas) == 1
           else {"name": "bundle", "title": f"{len(metas)} 个插件",
                 "plugins": [m["name"] for m in metas], "exported_at": int(time.time())})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n in clean:
            z.writestr(f"{n}.py", sources[n])
        z.writestr("plugin.json", json.dumps(man, ensure_ascii=False, indent=1))
        z.writestr("README.md", _readme(man, [f"{n}.py" for n in clean]))
    out_name = filename or (f"{clean[0]}.zip" if len(clean) == 1 else "klee-plugins.zip")
    return buf.getvalue(), out_name


BACKUP_MAX_BYTES = 8 * 1024 * 1024
BACKUP_MAX_FILES = 400


def pack_backup(features_dir: Path | str) -> tuple[bytes, str]:
    """把所有插件源码打成一个**备份包**（不是可安装的插件包）。

    和 `pack()` 的区别要说清楚：导入入口只接受 ≤20 个 .py，150 多个插件的
    整包装不回去 —— 所以这个包是「代码存档」，README 里写明怎么拷回去，
    不假装它是一键安装包。
    """
    root = Path(features_dir)
    # 只跳过 `_` 开头的基础设施模块（_plugstore / _cardimg 之类，不是插件）。
    # 不按别的名字过滤：备份少文件而用户看不出来，是最糟的失败方式。
    files = sorted(f for f in root.glob("*.py") if not f.name.startswith("_"))
    if not files:
        raise PackError("没有可备份的插件")
    if len(files) > BACKUP_MAX_FILES:
        raise PackError(f"插件太多了（{len(files)} 个）")
    blobs = {f.name: _read(f) for f in files}
    total = sum(len(v.encode("utf-8")) for v in blobs.values())
    if total > BACKUP_MAX_BYTES:
        raise PackError(f"源码合计 {total // 1024} KB，超过上限 {BACKUP_MAX_BYTES // 1024} KB")
    index = []
    for name, src in blobs.items():
        try:
            m = module_meta(src)
        except PackError:
            m = {"commands": []}
        index.append({"file": name, "title": str(m.get("NAME") or name[:-3]),
                      "version": str(m.get("VERSION") or ""),
                      "characters": [str(x) for x in (m.get("CHARACTERS") or [])],
                      "commands": m.get("commands") or []})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, src in blobs.items():
            z.writestr(name, src)
        z.writestr("plugin.json", json.dumps(
            {"name": "backup", "count": len(blobs), "plugins": index,
             "exported_at": int(time.time())}, ensure_ascii=False, indent=1))
        z.writestr("README.md",
                   "# 插件源码备份\n\n"
                   f"共 {len(blobs)} 个插件，导出时间 "
                   f"{time.strftime('%Y-%m-%d %H:%M', time.localtime())}。\n\n"
                   "## 怎么恢复\n\n"
                   "把里面的 `.py` 直接拷回 `bot/features/`，然后面板 → 运维 → 重载插件。\n"
                   "`plugin.json` 是清单（每个插件有哪些命令），不用放回去。\n\n"
                   "> 这个包**不是**一键安装包：导入入口一次最多接收 20 个 .py，"
                   "所以它只做存档。\n")
    return buf.getvalue(), "klee-plugins-" + time.strftime("%Y%m%d") + ".zip"


def uninstall(features_dir: Path | str, name: str, trash_dir: Path | str) -> dict:
    """移到回收站（不直接删：那是用户自己写的东西）。"""
    name = str(name or "").strip()
    if not NAME_RE.match(name):
        raise PackError(f"插件名不合法：{name!r}")
    src = Path(features_dir) / f"{name}.py"
    if not src.exists():
        raise PackError(f"没有这个插件：{name}")
    trash = Path(trash_dir)
    trash.mkdir(parents=True, exist_ok=True)
    dst = trash / f"{name}.{int(time.time())}.py"
    shutil.move(str(src), str(dst))
    return {"ok": True, "name": name, "moved_to": str(dst)}
