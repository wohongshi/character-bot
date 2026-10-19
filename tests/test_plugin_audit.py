#!/usr/bin/env python3
"""插件体检（第三轮）：把「装了但没生效、而且没人告诉你」翻出来。

最典型的一种：命令名/别名全局唯一，撞名时框架只写一行日志、把**整条命令**
丢掉 —— 插件卡片看着正常，命令就是不在。这个项目因此丢过 `.字数` 和 `.draw`。

判据都是「造一个真问题 → 必须报出来；没问题 → 必须一条都不报」。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


def section(t):
    print(f"\n== {t}")


class Cmd:
    def __init__(self, name, plugin, aliases=()):
        self.name, self.plugin, self.aliases = name, plugin, tuple(aliases)


def fake_mgr(plugins, commands):
    return SimpleNamespace(plugins=plugins, commands=commands,
                           nlu_rules=[], sorted_commands=lambda: list(commands.values()))


def audit_with(plugins, commands, files: dict, enabled=None, chars=("klee", "atri")):
    """在临时目录里铺插件文件 + 假 manager，跑一次体检。"""
    import server
    tmp = Path(tempfile.mkdtemp())
    for name, src in files.items():
        (tmp / f"{name}.py").write_text(src, encoding="utf-8")
    (tmp / "config.json").write_text(
        json.dumps({"enabled": list(enabled if enabled is not None else files)}), encoding="utf-8")
    real = (server.FEATURES_DIR, server.BOT, server._char_store)
    try:
        server.FEATURES_DIR = tmp
        server.BOT = SimpleNamespace(runtime=SimpleNamespace(mgr=fake_mgr(plugins, commands)))
        server._char_store = lambda: SimpleNamespace(defs={c: {} for c in chars}, get=lambda c: {})
        return server._plugin_audit()
    finally:
        server.FEATURES_DIR, server.BOT, server._char_store = real


PLAIN = '''NAME = "普通"
DESCRIPTION = "ok"


def register(api):
    api.on_command("hello", _h, cooldown=0, help=".hello")
'''

CONFLICT = '''NAME = "撞名"
DESCRIPTION = "和 hello 抢名字"


def register(api):
    api.on_command("hello", _h, cooldown=0, help=".hello")
'''


def main():
    section("1. 健康时一条都不报")
    d = audit_with({"a": {"enabled": True}},
                   {"hello": Cmd("hello", "a")},
                   {"a": PLAIN}, enabled=["a"])
    check("1.1 没有问题", d["ok"] and d["issues"] == [], str(d["issues"]))
    check("1.2 统计里有插件数和命令数", d["counts"]["plugins"] == 1 and d["counts"]["commands"] == 1,
          str(d["counts"]))

    section("2. 命令被抢（整条被丢掉）")
    d = audit_with({"a": {"enabled": True}, "b": {"enabled": True}},
                   {"hello": Cmd("hello", "a")},      # b 的 hello 没注册上
                   {"a": PLAIN, "b": CONFLICT}, enabled=["a", "b"])
    kinds = {i["kind"] for i in d["issues"]}
    hit = next((i for i in d["issues"] if i["kind"] == "command_taken"), None)
    check("2.1 报出来了", "command_taken" in kinds, str(d["issues"]))
    check("2.2 点名是哪个插件抢的", hit and "a" in hit["message"], hit and hit["message"])
    check("2.3 是「必修」不是「建议」", hit and hit["level"] == "error", hit and hit["level"])
    check("2.4 给出了改法", hit and "改个名字" in hit["fix"], hit and hit["fix"])

    section("3. 声明了但没注册（不是撞名）")
    import server as _s
    src = '''NAME = "坏"
DESCRIPTION = "注册中途抛错"


def register(api):
    api.on_command("ghost_cmd", _h)
'''
    d = audit_with({"c": {"enabled": True}, "a": {"enabled": True}}, {"hello": Cmd("hello", "a")},
                   {"a": PLAIN, "c": src}, enabled=["a", "c"])
    kinds = {i["kind"] for i in d["issues"]}
    check("3.1 报 command_missing", "command_missing" in kinds, str(d["issues"]))
    check("3.2 是「建议」级别", all(i["level"] != "error" for i in d["issues"] if i["kind"] == "command_missing"))

    section("4. 关掉的插件不报（用户主动关的不算问题）")
    # 真框架里「没在 enabled 名单」的插件，info["enabled"] 就是 False
    d = audit_with({"a": {"enabled": True}, "b": {"enabled": False}}, {"hello": Cmd("hello", "a")},
                   {"a": PLAIN, "b": CONFLICT}, enabled=["a"])
    check("4.1 没有 command_taken", not any(i["kind"] == "command_taken" for i in d["issues"]),
          str(d["issues"]))

    section("5. 配置开着但运行时没有（重载失败）")
    d = audit_with({"a": {"enabled": True}}, {"hello": Cmd("hello", "a")},
                   {"a": PLAIN, "zzz": "NAME='x'\n"}, enabled=["a", "zzz"])
    hit = next((i for i in d["issues"] if i["kind"] == "ghost"), None)
    check("5.1 报 ghost", hit is not None, str(d["issues"]))
    check("5.2 点名 zzz", hit and hit["plugin"] == "zzz", hit and hit["plugin"])

    section("6. 插件自己报的错")
    d = audit_with({"a": {"enabled": True}}, {"hello": Cmd("hello", "a")},
                   {"a": PLAIN}, enabled=["a"])
    # 同名但 info 带 error
    d2 = audit_with({"a": dict(error="ImportError: boom")}, {"hello": Cmd("hello", "a")},
                    {"a": PLAIN}, enabled=["a"])
    check("6.1 报 plugin_error", any(i["kind"] == "plugin_error" for i in d2["issues"]),
          str(d2["issues"]))
    check("6.2 原文带上", any("boom" in i["message"] for i in d2["issues"]), str(d2["issues"]))

    section("7. 角色限定写了不存在的角色")
    d = audit_with({"a": dict(characters=["klee", "nobody"], enabled=True)},
                   {"hello": Cmd("hello", "a")}, {"a": PLAIN}, enabled=["a"])
    hit = next((i for i in d["issues"] if i["kind"] == "unknown_character"), None)
    check("7.1 报 unknown_character", hit is not None, str(d["issues"]))
    check("7.2 点名 nobody", hit and "nobody" in hit["message"], hit and hit["message"])

    section("8. 配置项写坏")
    d = audit_with({"a": dict(enabled=True, schema={"x": {"type": "integer"}})},
                   {"hello": Cmd("hello", "a")}, {"a": PLAIN}, enabled=["a"])
    check("8.1 报 schema", any(i["kind"] == "schema" for i in d["issues"]), str(d["issues"]))

    section("9. 默认工具没有任何插件注册")
    from bot import toolbox as tb
    real_tools = list(tb.DEFAULT_TOOLS)
    try:
        tb.DEFAULT_TOOLS = list(real_tools) + ["不存在的工具"]
        d = audit_with({"a": {"enabled": True}}, {"hello": Cmd("hello", "a")}, {"a": PLAIN}, enabled=["a"])
        hit = next((i for i in d["issues"] if i["kind"] == "tool_unregistered"), None)
        check("9.1 报 tool_unregistered", hit is not None, str(d["issues"]))
        check("9.2 点名那个工具", hit and "不存在的工具" in hit["message"], hit and hit["message"])
    finally:
        tb.DEFAULT_TOOLS = real_tools

    section("10. 活服务（真实配置应该是干净的）")
    import urllib.error
    import urllib.request
    try:
        req = urllib.request.Request("http://127.0.0.1:8798/api/plugins/audit",
                                     headers={"X-Klee-Studio": "1"})
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read())
        check("10.1 接口可用", d.get("ok") is True)
        check("10.2 有统计", d["counts"].get("plugins", 0) > 100, str(d["counts"]))
        check("10.3 真实配置没有必修项", d["counts"].get("error") == 0,
              str([i for i in d["issues"] if i["level"] == "error"])[:300])
    except (urllib.error.URLError, OSError) as e:
        print(f"  · 服务没起来，跳过（{type(e).__name__}）")

    section("11. 面板接线")
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    check("11.1 有体检按钮", "plugAudit()" in html and "🩺 体检" in html)
    check("11.2 结果区在网格上方", html.index('id="auditBox"') < html.index('id="plugGrid"'))
    check("11.3 每个问题能点开对应插件", "openPluginDrawer('${esc(i.plugin)}')" in html
          or 'openPluginDrawer(\'${esc(i.plugin)}\')' in html)
    check("11.4 区分必修/建议", "必修" in html and "建议" in html)

    print(f"\nPASS {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败：" + "、".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
