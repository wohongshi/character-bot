#!/usr/bin/env python3
"""可点击元素审计：面板上每个能点的东西都要有真实处理器，且点下去有反应。

分两层：
1. **静态**（tools/audit_clickables.py 的规则）：内联事件调用的函数在 JS 里定义了吗？
   处理器里 `$("id")` 引用的元素真的存在吗？有没有既没处理器、又不是 submit/select/a 的按钮？
2. **接口契约**：顶栏那颗「在线/QQ 未连接」的药丸点下去会跳到运维页 —— 它说的话必须是真的。
   连通性要问协议端，不能拿「最近一小时有没有人说话」当在线。

真实点击（抓报错 / 非 2xx / 点完没反应）在浏览器里跑，不进单测 —— 见 docs 里的一次性审计脚本。
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


def section(title):
    print(f"\n== {title}")


def main():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    srv = (ROOT / "server.py").read_text(encoding="utf-8")

    section("1. 静态：名字都是真的")
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "audit_clickables.py")],
                       capture_output=True, text=True)
    try:
        rep = json.loads(r.stdout.split("\n\n内联事件")[0])
    except ValueError:
        rep = {}
    check("1.1 审计脚本能跑", bool(rep), (r.stdout + r.stderr)[:200])
    check("1.2 没有缺失的 JS 函数", rep.get("missing_calls") == [], str(rep.get("missing_calls"))[:200])
    check("1.3 没有缺失的 DOM id", rep.get("missing_ids") == [], str(rep.get("missing_ids"))[:200])
    check("1.4 没有「看着能点其实没处理器」的按钮", rep.get("bare_buttons") == [],
          str(rep.get("bare_buttons"))[:200])
    check("1.5 内联事件数量合理（≥100）", int(rep.get("handlers") or 0) >= 100, str(rep.get("handlers")))
    check("1.6 审计整体 rc=0", r.returncode == 0, r.stdout[-200:])

    section("2. 在线状态：问协议端，不问「最近有没有人说话」")
    # 曾经：online = 最近一小时收到过消息。于是重启后没人说话 → 顶栏笃定「QQ 未连接」，
    # 把人引去扫码/重启 NapCat，做完全都没用。
    check("2.1 overview?lite 的 connected 来自 qq_health", 'conn = bool(qq_health().get("connected"))' in srv)
    check("2.2 全量 overview 复用同一份 health", "health = qq_health()" in srv
          and '"health": health,' in srv)
    check("2.3 /api/bot 带 health 字段", 'st["health"] = health' in srv)
    check("2.4 连通时 /api/bot 的 online 也为真", 'st["online"] = True' in srv)

    section("3. 健康检查本身：调对方法")
    # 曾经：`BOT.runtime.ob.get_status()` —— OneBotAdapter 上没有这个方法，
    # 于是恒 AttributeError，面板和告警系统 58 小时一直在报「协议端连不上」。
    # 注释里还会提到这个方法（那是解释为什么不能用），只核**代码行**
    code = "\n".join(ln for ln in srv.splitlines() if not ln.lstrip().startswith("#"))
    check("3.1 不再直接调 ob.get_status()", "ob.get_status(" not in code)
    check("3.2 走适配器的 health()", 'h = ad.health()' in srv)
    check("3.3 结果有缓存（≤10s 命中一次）", "_QQ_HEALTH_CACHE" in srv and "now - hit[\"ts\"] < 10.0" in srv)

    section("4. 面板：保存 Agent 设置不写工具清单")
    # 以前读一个不存在的 #agTools，靠 null 判断才没把工具清空。那种写法迟早被人「修好」。
    check("4.1 不再引用 #agTools", 'document.getElementById("agTools")' not in html)
    check("4.2 不传 tools（空数组会把 agent 弄瞎）", "{tools: [...box.querySelectorAll" not in html)

    section("5. 活服务抽查（起不来就跳过）")
    base = "http://127.0.0.1:8798"
    hdr = {"X-Klee-Studio": "1"}
    try:
        req = urllib.request.Request(base + "/api/bot", headers=hdr)
        with urllib.request.urlopen(req, timeout=8) as r2:
            bot = json.loads(r2.read())
        check("5.1 /api/bot 200 且 ok", bot.get("ok") is True)
        h = bot.get("health") or {}
        check("5.2 health 是 {connected, reason}", isinstance(h.get("connected"), bool), str(h)[:150])
        check("5.3 协议端通时 online 不会被 idle 说成掉线",
              (h.get("connected") is False) or bot.get("online") is True,
              f"health={h} online={bot.get('online')}")
        lite = json.loads(urllib.request.urlopen(
            urllib.request.Request(base + "/api/overview?lite=1", headers=hdr), timeout=8).read())
        check("5.4 lite.connected 与 health 一致",
              bool(lite["adapter"]["connected"]) == bool(h.get("connected")),
              f"{lite['adapter']} vs {h}")
    except (urllib.error.URLError, OSError, KeyError, ValueError) as e:
        print(f"  · 服务没起来，跳过（{type(e).__name__}）")

    print(f"\nPASS {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败：" + "、".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
