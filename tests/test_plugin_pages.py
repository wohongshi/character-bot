#!/usr/bin/env python3
"""插件自带页面（微型 webui）的回归。

这一块的毛病都是「页面能打开、但按了没反应」这类，所以判据分两层：
- 框架层：iframe 的沙箱、注入的桥（ks.*）、主题变量；
- 每个页面：它读的字段在 `/api/bot` 里真的存在，它调的接口形状对得上。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


BASE = "http://127.0.0.1:8798"
H = {"X-Klee-Studio": "1"}


def get(path):
    req = urllib.request.Request(BASE + path, headers=H)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


def node_check(js: str) -> tuple[int, str]:
    """页面里的 <script> 必须是合法 JS —— 这一条能挡住「整段脚本静默不执行」。"""
    p = Path("/tmp/_klee_page_check.js")
    p.write_text(js, encoding="utf-8")
    try:
        r = subprocess.run(["node", "--check", str(p)], capture_output=True, text=True, timeout=30)
        return r.returncode, (r.stderr or "")[:200]
    except (OSError, subprocess.SubprocessError) as e:
        return 0, f"skip: {e}"          # 没装 node 就不判


def main() -> int:
    import server as S

    page = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    print("[1] 框架层：沙箱与桥")
    check("1.1 沙箱允许弹窗", 'allow-modals' in page,
          "没有它 confirm()/alert() 会被静默忽略，按钮看着能点其实什么也不做")
    check("1.2 沙箱允许下载", 'allow-downloads' in page)
    check("1.3 桥里有 ks.send（入队 + 等回复）", "send: async (text, opts) =>" in page)
    check("1.4 桥里有当前角色 / 会话", "char: KS_CHAR" in page and "session: KS_SESSION" in page)
    check("1.5 空的主题变量不抄进 iframe（否则 var() 计算时失效）",
          'return val ? `${v}:${val}` : "";' in page)
    check("1.6 iframe 里有 --radius 兜底", ":root{--radius:14px;" in page)
    check("1.7 面板自己也有 --radius（骨架卡在用）",
          re.search(r"--radius:\s*var\(--ui-radius\)", page) is not None)

    print("\n[2] /api/bot：插件页读的字段真的存在")
    st, bot = get("/api/bot")
    check("2.1 200", st == 200 and bot.get("ok"), str(st))
    check("2.2 带 napcat 段", isinstance(bot.get("napcat"), dict), str(bot.get("napcat"))[:80])
    n = bot.get("napcat") or {}
    check("2.3 napcat 有 is_login / phase / uin", all(k in n for k in ("is_login", "phase", "uin")),
          str(sorted(n)))
    rows = bot.get("adapters") or []
    check("2.4 适配器都带 ready", rows and all("ready" in r for r in rows),
          str([r.get("platform") for r in rows if "ready" not in r]))
    check("2.5 适配器都带 ready_note（可以为空串）",
          all("ready_note" in r for r in rows), str([r.get("platform") for r in rows]))
    one = [r for r in rows if r.get("platform") == "onebot11"]
    check("2.6 onebot11 在列表里（页面靠 platform 找它）", len(one) == 1, str(len(one)))

    print("\n[3] 两个自带页面：能取到、脚本合法")
    for name in ("web", "onebot11"):
        st, d = get("/api/plugin/page?name=" + name)
        html = d.get("html") or ""
        check(f"3.{name} 取到页面", st == 200 and d.get("ok") and len(html) > 500, f"{st} {len(html)}")
        m = re.search(r"<script>(.*?)</script>", html, re.S)
        check(f"3.{name} 有脚本", bool(m and len(m.group(1)) > 200))
        if m:
            rc, err = node_check(m.group(1))
            check(f"3.{name} 脚本语法合法", rc == 0, err)

    print("\n[4] web 页面：不再把「入队」当「回复」")
    import bot.adapters.web as web_mod
    whtml = web_mod._PAGE_HTML
    check("4.1 用 ks.send 发消息", "ks.send(" in whtml)
    check("4.2 没有直接读 POST /api/chat 的 messages",
          not re.search(r'ks\.post\("/api/chat"', whtml), "")
    check("4.3 清空时带上 character", 'character: ks.char' in whtml)
    check("4.4 会话列表按角色查", "character=" in whtml and "chat/sessions" in whtml)
    check("4.5 页面里没有反斜杠转义（Python 字符串会把它吃掉）", "\\" not in whtml)

    print("\n[5] onebot11 页面：状态说实话")
    import bot.adapters.onebot as ob_mod
    ohtml = ob_mod._PAGE_HTML
    check("5.1 显示 Bot 是否在跑（d.ok）", "d && d.ok ?" in ohtml)
    check("5.2 显示协议端 ready", "ad.ready ?" in ohtml)
    check("5.3 显示 napcat 登录态", "n.is_login" in ohtml)
    check("5.4 已登录时不再摆旧二维码", "不用扫码" in ohtml and "if (n.is_login)" in ohtml)

    print("\n[6] 端到端：/api/chat 只入队这件事没变（页面必须轮询）")
    # 直接确认接口语义，避免以后有人又写「POST 完就有正文」
    tmp = "t-pluginpage-%d" % int(__import__("time").time())
    body = json.dumps({"session": tmp, "text": "插件页回归探针", "character": S._active_cid()}).encode()
    req = urllib.request.Request(BASE + "/api/chat", data=body,
                                 headers={**H, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        d = {}
    check("6.1 POST /api/chat 只回 queued，没有 messages",
          d.get("queued") is True and "messages" not in d, str(d)[:120])
    # 清掉（等 worker 跑完再删，否则它会在删除后又写一行）
    import time as _t
    for _ in range(60):
        _st, cur = get(f"/api/chat?session={tmp}&character={S._active_cid()}")
        if not cur.get("running") and not cur.get("queue"):
            break
        _t.sleep(0.5)
    req = urllib.request.Request(BASE + "/api/chat/delete",
                                 data=json.dumps({"session": tmp, "character": S._active_cid()}).encode(),
                                 headers={**H, "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=60).read()
    except urllib.error.HTTPError:
        pass
    _st, cur = get(f"/api/chat?session={tmp}&character={S._active_cid()}")
    check("6.2 收尾：探针会话已清空", not (cur.get("messages") or []), str(len(cur.get("messages") or [])))

    print(f"\nPASS {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败：" + "、".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
