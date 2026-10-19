#!/usr/bin/env python3
"""对话内轻量工具（第二轮）：对某一条消息做 翻译 / 总结 / 拆解 / 润色。

判据：
- 四个动作都真的能用（走真模型，检查输出不是空 / 不是原文照抄）；
- 参数校验：坏动作、空文本、超长；
- **不写进聊天记录**（它是工具输出，不是她的话；写进去会污染历史）；
- 面板上每条消息都有入口，结果能复制。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []
BASE = "http://127.0.0.1:8798"
HDR = {"X-Klee-Studio": "1", "Content-Type": "application/json"}


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


def section(t):
    print(f"\n== {t}")


def post(path, body, timeout=120):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), headers=HDR)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def get(path):
    req = urllib.request.Request(BASE + path, headers=HDR)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def main():
    import server

    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    srv = (ROOT / "server.py").read_text(encoding="utf-8")

    section("1. 表定义")
    check("1.1 四个动作", set(server.CHAT_TOOLS) == {"translate", "summary", "explain", "polish"},
          str(list(server.CHAT_TOOLS)))
    check("1.2 提示词都明确「别多话」", all("不要" in p for p in server.CHAT_TOOLS.values()),
          str(server.CHAT_TOOLS))
    check("1.3 有长度上限", server.CHAT_TOOL_MAX == 4000, str(server.CHAT_TOOL_MAX))

    section("2. 参数校验")
    try:
        code, d = post("/api/chat/tool", {"action": "hack", "text": "x"})
        check("2.1 坏动作 400", code == 400, f"{code} {d}")
        code, d = post("/api/chat/tool", {"action": "summary", "text": "   "})
        check("2.2 空文本 400", code == 400, f"{code} {d}")
        code, d = post("/api/chat/tool", {"action": "summary", "text": "字" * 5000})
        check("2.3 超长 400", code == 400 and "太长" in str(d.get("error")), f"{code} {d}")
    except (urllib.error.URLError, OSError) as e:
        print(f"  · 服务没起来，跳过（{type(e).__name__}）")

    section("3. 真跑四个动作")
    cases = [
        ("translate", "她说她今天不太想说话。", lambda t: "she" in t.lower() and "说话" not in t),
        ("summary", "今天开会讨论了三件事：预算、排期、人手。预算砍了 10%，排期往后推一周，人手缺两个。",
         lambda t: len(t) < 300 and ("预算" in t or "排期" in t)),
        ("explain", "二分查找要求数组有序，复杂度 O(log n)。",
         lambda t: len(t) > 20 and not t.startswith("用户给的")),
        ("polish", "我觉的这个方案有问题，你看下。", lambda t: "这个" in t or "方案" in t),
    ]
    for act, text, ok_fn in cases:
        try:
            t0 = time.time()
            code, d = post("/api/chat/tool", {"action": act, "text": text, "character": "cyrene"})
            ms = round((time.time() - t0) * 1000)
            out = str(d.get("text") or "")
            print(f"   · {act}: {code} {ms}ms → {out[:70]!r}")
            check(f"3.x {act} 成功", code == 200 and d.get("ok") is True and out, f"{code} {d}")
            check(f"3.y {act} 输出是加工过的（不是原文照抄）", out.strip() != text.strip(), out[:80])
            check(f"3.z {act} 内容像样", bool(ok_fn(out)), out[:120])
        except (urllib.error.URLError, OSError) as e:
            print(f"  · {act} 跳过（{type(e).__name__}）")

    section("4. 不污染聊天记录")
    sess = f"t-tool-{int(time.time())}"
    post("/api/chat", {"session": sess, "character": "cyrene", "text": "第一句用来测试"})
    for _ in range(40):
        time.sleep(1)
        cur = get(f"/api/chat?session={sess}&character=cyrene")
        if not cur["running"] and len(cur["messages"]) >= 2:
            break
    n0 = len(get(f"/api/chat?session={sess}&character=cyrene")["messages"])
    code, d = post("/api/chat/tool", {"action": "summary", "text": "这句话只是给工具用的",
                                      "character": "cyrene"})
    time.sleep(1)
    n1 = len(get(f"/api/chat?session={sess}&character=cyrene")["messages"])
    check("4.1 工具输出没有变成新消息", n0 == n1, f"{n0} → {n1}")
    check("4.2 而是直接在响应里返回", code == 200 and d.get("text"), str(d)[:100])
    post("/api/chat/delete", {"session": sess, "character": "cyrene"})

    section("5. 面板接线")
    check("5.1 每条消息都有「处理」入口", "msgTools(this)" in html and "🛠 处理" in html)
    check("5.2 四个动作按钮", all(k in html for k in ("translate", "summary", "explain", "polish")))
    check("5.3 结果显示区 + 复制", "mresBody" in html and "copyPlain(" in html)
    check("5.4 结果保留换行", 'replace(/\\n/g, "<br>")' in html)
    check("5.5 服务端有接口", 'u.path == "/api/chat/tool"' in srv)
    check("6.0 有输出清洗（去开场白 / 去包裹引号）",
          "_clean_tool_text" in srv and "_TOOL_META_RE" in srv)
    check("5.6 不写 chat.jsonl（接口里没有 append）",
          "CHAT_JSONL" not in srv.split('u.path == "/api/chat/tool"')[1].split("if u.path")[0])

    print(f"\nPASS {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败：" + "、".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
