#!/usr/bin/env python3
"""知识库手动录入与命中追溯（第三轮）。

判据：
- 加一条 → **立刻**能检索到（不用重建索引），排在前面；
- 删掉 → 检索里也没有了（内存里的向量和稀疏统计都得跟着变，不能只删库）；
- 参数校验（太短 / 太长 / 不存在的 id）；
- 面板里问一句话，命中日志要记下来（谁问的、命中了哪几段、多少分）——
  以前面板侧根本不写这条日志，只有 QQ 的会出现在「最近命中」里。
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
KB = "http://127.0.0.1:8797"
HDR = {"X-Klee-Studio": "1", "Content-Type": "application/json"}


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


def section(t):
    print(f"\n== {t}")


def post(url, body, headers=None, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def kb_add(text, title="", cid="cyrene", direct=False):
    if direct:
        return post(KB + "/add", {"text": text, "title": title, "character": cid},
                    {"Content-Type": "application/json"})
    return post(BASE + "/api/kb/add", {"text": text, "title": title, "character": cid}, HDR)


def kb_search(q, cid="cyrene", k=5):
    if True:
        code, d = post(KB + "/search", {"text": q, "k": k, "threshold": 0.0, "character": cid},
                       {"Content-Type": "application/json"})
        return code, d


def main():
    section("1. 直接对知识库服务加一条")
    orig = None
    added = []
    try:
        orig = get(KB + "/healthz")["characters"]["cyrene"]["chunks"]

        code, d = kb_add("短", direct=True)
        check("1.1 太短 400", code == 400 and "太短" in str(d.get("error")), f"{code} {d}")
        code, d = kb_add("字" * 5000, direct=True)
        check("1.2 太长 400", code == 400 and "太长" in str(d.get("error")), f"{code} {d}")

        code, d = kb_add("她喜欢薄荷味的糖，尤其是下雨天。", "偏好", direct=True)
        check("1.3 加成功", code == 200 and d.get("ok") is True, f"{code} {d}")
        check("1.4 块数 +1", d.get("chunks") == orig + 1, f"{orig} → {d.get('chunks')}")
        check("1.5 分配了新 id", isinstance(d.get("id"), int))
        added.append(d["id"])

        code, s = kb_search("她喜欢什么味道的糖")
        titles = [(h.get("title"), round(float(h.get("dense") or 0), 3)) for h in s["hits"]]
        check("1.6 立刻能检索到（不用重建）", any(t == "偏好" for t, _ in titles), str(titles))
        check("1.7 而且排第一", titles and titles[0][0] == "偏好", str(titles[:2]))

        m = get(KB + "/manual?character=cyrene")
        check("1.8 列在「我加的」里", any(i["id"] == d["id"] for i in m["items"]), str(m)[:200])
        check("1.9 手动条目带来源标记", m.get("source") == "手动录入", str(m.get("source")))

        code, dd = post(KB + "/delete", {"id": d["id"], "character": "cyrene"},
                        {"Content-Type": "application/json"})
        check("1.10 删掉", code == 200 and dd.get("chunks") == orig, f"{code} {dd}")
        added.remove(d["id"])
        code, s = kb_search("她喜欢什么味道的糖")
        check("1.11 删完检索里也没有了",
              not any(h.get("title") == "偏好" for h in s["hits"]), str(s["hits"][:2]))
        code, dd = post(KB + "/delete", {"id": 999999, "character": "cyrene"},
                        {"Content-Type": "application/json"})
        check("1.12 删不存在的 400", code == 400, f"{code} {dd}")

        section("2. 面板代理（/api/kb/*）")
        code, d = post(BASE + "/api/kb/add",
                       {"text": "她讨厌有人在图书馆大声说话。", "title": "小习惯", "character": "cyrene"},
                       HDR)
        check("2.1 代理加成功", code == 200 and d.get("ok"), f"{code} {d}")
        added.append(d["id"])
        m = get(BASE + "/api/kb/manual?character=cyrene", HDR)
        check("2.2 代理列表", any(i["id"] == d["id"] for i in m["items"]))
        code, d2 = post(BASE + "/api/kb/add", {"text": "太短", "character": "cyrene"}, HDR)
        check("2.3 校验透传（400 + 人话）", code == 400 and "短" in str(d2.get("error")), f"{code} {d2}")

        section("3. 命中追溯（面板侧也要写日志）")
        before = get(BASE + "/api/kb/recent?n=5&character=cyrene", HDR)["hits"]
        sess = f"t-kb-{int(time.time())}"
        post(BASE + "/api/chat", {"session": sess, "character": "cyrene",
                                  "text": "图书馆里有人大声说话你会怎么样？"}, HDR)
        for _ in range(60):
            time.sleep(1)
            cur = get(f"{BASE}/api/chat?session={sess}&character=cyrene", HDR)
            if not cur["running"] and len(cur["messages"]) >= 2:
                break
        after = get(BASE + "/api/kb/recent?n=5&character=cyrene", HDR)["hits"]
        check("3.1 面板聊天也记了命中", len(after) >= len(before), f"{len(before)} → {len(after)}")
        latest = after[0] if after else {}
        check("3.2 记下了问的是什么", "图书馆" in str(latest.get("query")), str(latest.get("query")))
        check("3.3 标注了来自面板", latest.get("via") == "panel", str(latest.get("via")))
        hits = latest.get("hits") or []
        check("3.4 命中了刚加的那条", any(h.get("title") == "小习惯" for h in hits),
              str([h.get("title") for h in hits]))
        check("3.5 带分数", any(float(h.get("dense") or 0) > 0 for h in hits), str(hits[:2]))
        post(BASE + "/api/chat/delete", {"session": sess, "character": "cyrene"}, HDR)

        section("4. 面板接线")
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        srv = (ROOT / "server.py").read_text(encoding="utf-8")
        check("4.1 有「加一条知识」卡", "kbAdd()" in html and "kbAddText" in html)
        check("4.2 有手动条目列表 + 删除", "kbDel(" in html and "loadKbManual()" in html)
        check("4.3 加完同步块数", "const n = document.querySelector" in html)
        check("4.4 命中记录显示来源（面板/QQ）", 'r.via === "panel"' in html)
        check("4.5 服务端三个接口",
              all(x in srv for x in ('"/api/kb/add"', '"/api/kb/delete"', '"/api/kb/manual"')))

    finally:
        # 收尾：无论断言成败都把测试条目删干净
        for cid in ("cyrene",):
            try:
                m = get(BASE + "/api/kb/manual?character=" + cid, HDR)
                for it in m.get("items") or []:
                    post(BASE + "/api/kb/delete", {"character": cid, "id": it["id"]}, HDR)
            except Exception:                                       # noqa: BLE001
                pass
        try:
            now = get(KB + "/healthz")["characters"]["cyrene"]["chunks"]
            check("5.1 测试条目已清理（块数回到原值）", orig is None or now == orig, f"{orig} → {now}")
        except Exception as e:                                      # noqa: BLE001
            print(f"  · 收尾检查跳过（{type(e).__name__}）")

    print(f"\nPASS {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败：" + "、".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
