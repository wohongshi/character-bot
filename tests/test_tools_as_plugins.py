#!/usr/bin/env python3
"""工具即插件 / 插件自带页面 / 平台化后的分类。

这轮改动的三条契约，逐条验行为（不是「代码里写了就算」）：

1. **工具是插件**：关掉插件 → 工具从 `toolbox` 消失 → 模型的可调用列表里没有它。
2. **插件可以自带小页面**：`api.add_page()` / `BaseAdapter.page()` 都能出 HTML，
   `/api/plugin/page` 拿得到；没有页面的插件老实报错。
3. **分类**：系统（适配器/联网）· 限定（角色专属）· 通用（共享插件）。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

STUDIO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STUDIO))

from bot import toolbox                                     # noqa: E402
# 没有 FeatureManager 的场合（本测试）靠 bootstrap 把工具表填上；
# 真实启动时是插件装载时 register(api) → api.add_tool() 填的。
toolbox.bootstrap()
from bot.framework import FeatureManager                     # noqa: E402
from bot.adapters import web as webmod                        # noqa: E402

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  [{extra}]" if extra and not ok else ""))


FEAT = STUDIO / "bot" / "features"

# ---------------------------------------------------------------- 1) 工具是插件
print("· 工具是插件")
check("1.1 五个工具插件都在仓库里",
      all((FEAT / f"{n}.py").is_file() for n in toolbox.TOOL_PLUGINS),
      str([n for n in toolbox.TOOL_PLUGINS if not (FEAT / f"{n}.py").is_file()]))

names = set(toolbox.names())
check("1.2 默认工具全部已注册", set(toolbox.DEFAULT_TOOLS) <= names,
      str(set(toolbox.DEFAULT_TOOLS) - names))
check("1.3 每个工具都能追到插件",
      all((toolbox.get(n) or {}).get("plugin") for n in toolbox.DEFAULT_TOOLS))
check("1.4 工具规格符合 OpenAI 格式",
      all(set(s["function"]) >= {"name", "description", "parameters"}
          for s in toolbox.specs(None)))

# ---------------------------------------------------------------- 2) 开关真的生效
print("\n· 关掉插件 = 工具消失")
tmp = Path(tempfile.mkdtemp(prefix="klee-tools-"))
try:
    for n in toolbox.TOOL_PLUGINS:
        shutil.copy2(FEAT / f"{n}.py", tmp / f"{n}.py")
    (tmp / "config.json").write_text(json.dumps({"enabled": list(toolbox.TOOL_PLUGINS)}),
                                     encoding="utf-8")

    class Host:
        features_dir = str(tmp)
        feature_prefix = "."
        active_character = "klee"
        active_character_info = {}
        adapter = None

    mgr = FeatureManager(Host())
    mgr.load()
    check("2.1 装载后工具都在", {"now", "calc", "web_search"} <= set(toolbox.names()))
    check("2.2 工具记在插件名下",
          (toolbox.get("now") or {}).get("plugin") == "utility",
          str((toolbox.get("now") or {}).get("plugin")))

    (tmp / "config.json").write_text(json.dumps(
        {"enabled": [n for n in toolbox.TOOL_PLUGINS if n != "utility"]}), encoding="utf-8")
    mgr.load()
    check("2.3 关掉「时间与计算」→ now/calc 消失",
          not toolbox.has("now") and not toolbox.has("calc"))
    check("2.4 别的插件的工具不受影响", toolbox.has("web_search") and toolbox.has("kb_search"))
    check("2.5 模型拿到的 tools 里也没有了",
          "now" not in [s["function"]["name"] for s in toolbox.specs(None)])

    # register 抛异常 → 半截注册必须回滚
    (tmp / "boom.py").write_text(
        "def register(api):\n"
        "    from bot.agent import tool_now\n"
        "    api.add_tool('boom_tool', handler=tool_now)\n"
        "    raise RuntimeError('炸了')\n", encoding="utf-8")
    (tmp / "config.json").write_text(json.dumps(
        {"enabled": list(toolbox.TOOL_PLUGINS) + ["boom"]}), encoding="utf-8")
    mgr.load()
    check("2.6 register 抛异常 → 它登记的工具被回滚", not toolbox.has("boom_tool"))
    check("2.7 回滚不影响别的插件", toolbox.has("now"))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ---------------------------------------------------------------- 3) web 平台
print("\n· 网页平台（web 插件）")
st = Path(tempfile.mkdtemp(prefix="klee-web-"))
try:
    os.environ["KLEE_STATE_DIR"] = str(st)
    (st / "characters.json").write_text(json.dumps({"active": "hutao"}), encoding="utf-8")
    ad = webmod.WebAdapter({"web_push": True})
    check("3.1 默认开、有自带页面", webmod.WebAdapter.DEFAULT_OFF is False and len(ad.page()) > 500)
    check("3.2 声明了配置表单", bool(webmod.WebAdapter.CONFIG_SCHEMA))
    r = ad.send_text("web:studio-main", "到点吃药啦")
    check("3.3 主动消息写进网页会话", bool(r.get("success")), str(r))
    rows = [json.loads(x) for x in (st / "chat.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    check("3.4 落库带 session/character/role",
          rows and rows[-1]["session"] == "studio-main" and rows[-1]["character"] == "hutao"
          and rows[-1]["role"] == "assistant", str(rows[-1] if rows else None))
    off = webmod.WebAdapter({"web_push": False})
    check("3.5 关掉主动消息 → 明确失败（不假装成功）",
          off.send_text("web:x", "hi").get("success") is False)
    check("3.6 空消息不落库", ad.send_text("web:x", "   ").get("success") is False)
finally:
    os.environ.pop("KLEE_STATE_DIR", None)
    shutil.rmtree(st, ignore_errors=True)

# ---------------------------------------------------------------- 4) HTTP：插件页
print("\n· /api/plugin/page")
BASE = "http://127.0.0.1:8796"
H = {"X-Klee-Studio": "1"}


def get(path):
    try:
        with urllib.request.urlopen(urllib.request.Request(BASE + path, headers=H), timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:                                   # noqa: BLE001
        return 0, {"error": f"{type(e).__name__}: {e}"}


st_code, d = get("/api/plugin/page?name=web")
check("4.1 web 页面 200 + 有 HTML", st_code == 200 and d.get("ok") and len(d.get("html") or "") > 500,
      str(d)[:120])
check("4.2 页面里带了 iframe 需要的入口", "ks.api" not in (d.get("html") or "")
      or "loadSessions" in (d.get("html") or ""))
st_code, d = get("/api/plugin/page?name=onebot11")
check("4.3 onebot11 有扫码页（QQ 的细节在自己页里）",
      d.get("ok") and "二维码" in (d.get("html") or ""), str(d)[:120])
st_code, d = get("/api/plugin/page?name=nope")
check("4.4 不存在的插件老实报错", d.get("ok") is False and d.get("error"))

# ---------------------------------------------------------------- 5) 分类
print("\n· 分类：系统 / 限定 / 通用")
_, pl = get("/api/plugins")
meta = pl.get("meta") or {}


def owner_of(name):
    m = meta.get(name) or {}
    if m.get("system"):
        return "system"
    if m.get("characters"):
        return "limited"
    return "general"


check("5.1 联网工具归「系统」", owner_of("web_tools") == "system", owner_of("web_tools"))
check("5.2 时间与计算归「通用」", owner_of("utility") == "general", owner_of("utility"))
check("5.3 角色插件归「限定」",
      any(owner_of(n) == "limited" for n in meta), str([n for n in meta if owner_of(n) == "limited"][:3]))
check("5.4 工具插件在面板 meta 里带工具清单",
      meta.get("utility", {}).get("tools") == ["now", "calc"],
      str(meta.get("utility", {}).get("tools")))

# ---------------------------------------------------------------- 6) 插件页 JS 能过语法检查
print("\n· 插件自带页面的 JS 语法")
import re as _re                                              # noqa: E402
import shutil as _shutil                                      # noqa: E402
import subprocess as _sp                                      # noqa: E402

node = _shutil.which("node")
pages = []
import bot.adapters.onebot as _ob                             # noqa: E402
import bot.adapters.web as _wb                                # noqa: E402
pages.append(("onebot11", _ob._PAGE_HTML))
pages.append(("web", _wb._PAGE_HTML))
if node:
    tmpd = Path(tempfile.mkdtemp(prefix="klee-page-"))
    try:
        for name, html in pages:
            m = _re.search(r"<script>(.*)</script>", html, _re.S)
            if not m:
                check(f"6.{name} 页面有脚本", False, "没有 <script>")
                continue
            f = tmpd / f"{name}.js"
            f.write_text(m.group(1), encoding="utf-8")
            r = _sp.run([node, "--check", str(f)], capture_output=True, text=True)
            # 一行写错的引号会让整个 <script> 静默不执行 —— 页面只剩「读取中…」
            check(f"6.{name} 页面 JS 语法正确", r.returncode == 0,
                  (r.stderr or "").splitlines()[3:6] and " ".join((r.stderr or "").splitlines()[3:6]))
    finally:
        _shutil.rmtree(tmpd, ignore_errors=True)
else:
    print("  · 跳过（本机没有 node）")

print(f"\nPASS {len(PASS)} / {len(PASS) + len(FAIL)}")
if FAIL:
    print("失败：" + "、".join(FAIL))
sys.exit(1 if FAIL else 0)
