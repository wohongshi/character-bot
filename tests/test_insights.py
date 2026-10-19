#!/usr/bin/env python3
"""对话洞察（`/api/insights` + `MessageIndex.insights`）与按角色用量。

用一份**造出来的**索引验证聚合口径（热力图的星期换算、角色拆分、
常用字过滤），再对活服务核对接口形状 —— 不能只对着现有数据跑一遍就完事：
现有数据是测试消息，数字对不对看不出来。
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []
BASE = "http://127.0.0.1:8798"
HDR = {"X-Klee-Studio": "1"}


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


def section(t):
    print(f"\n== {t}")


def build(tmp: Path) -> Path:
    """造一份 chat.jsonl：三个角色、跨两天、含重复短句。"""
    rows = []
    base = time.time() - 3600
    def row(ts, cid, role, text):
        rows.append({"ts": ts, "session": "s1", "character": cid, "role": role, "text": text})
    # 周一 09:00 附近 3 条、周二 21:00 附近 2 条（用当前时间反推两天）
    day = 86400
    for i in range(3):
        row(base - 2 * day + i * 60, "klee", "user", "今天吃什么")
        row(base - 2 * day + i * 60 + 5, "klee", "assistant", "吃草莓蛋糕吧")
    row(base - day, "atri", "user", "在吗")
    row(base - day + 5, "atri", "assistant", "在的")
    # 常角字：故意多放「猫」（5 次，应排第一）；放一堆停用字「的了是我」验证过滤
    row(base - 100, "klee", "user", "猫猫猫猫咪的了是我你他")
    row(base - 90, "klee", "user", "重复短句")
    row(base - 80, "klee", "user", "重复短句")
    p = tmp / "chat.jsonl"
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return p


def main():
    from bot.store import MessageIndex

    tmp = Path(tempfile.mkdtemp(prefix="klee-ins-"))
    chat = build(tmp)
    idx = MessageIndex(tmp / "t.db", {"chat": chat}, tmp / "off.json")
    idx.sync(force=True)
    d = idx.insights(days=7, src="chat")

    section("1. 基础聚合")
    # 行数是造出来的：6 条循环 + 2 条 atri + 3 条补充 = 11
    check("1.1 总数 = 11", d["total"] == 11, str(d["total"]))
    check("1.2 热力图只给有数据的格", 0 < len(d["heat"]) <= 6, str(d["heat"]))
    check("1.3 逐日三天", len(d["by_day"]) == 3, str(d["by_day"]))
    check("1.4 来源是 SQL", d["from_sql"] is True)

    section("2. 星期换算（SQL 的 0=周日 → 展示 0=周一）")
    # 造数据用的是「今天-2天 / 今天-1天」，直接用本地时间算期望值
    # 三段时间都要算：-2 天、-1 天、现在（补充那几条）
    want_w = {int(time.strftime("%w", time.localtime(time.time() - k * 86400)))
              for k in (0, 1, 2)}
    got_w = {h["w"] for h in d["heat"]}
    mapped = {(w + 6) % 7 for w in want_w}
    check("2.1 星期索引与本地时间一致", got_w == mapped, f"{got_w} vs {mapped}")
    check("2.2 星期都在 0~6", all(0 <= h["w"] <= 6 and 0 <= h["h"] <= 23 for h in d["heat"]))

    section("3. 角色拆分")
    byc = {c["id"]: c for c in d["by_character"]}
    check("3.1 两个角色", set(byc) == {"klee", "atri"}, str(set(byc)))
    check("3.2 klee 收到 6 / 发出 3",
          byc["klee"]["user"] == 6 and byc["klee"]["assistant"] == 3, str(byc["klee"]))
    check("3.3 按条数降序", d["by_character"][0]["id"] == "klee")

    section("4. 短句 / 常用字 / 长度")
    phrases = {p["text"]: p["n"] for p in d["top_phrases"]}
    check("4.1 「重复短句」出现 2 次被统计", phrases.get("重复短句") == 2, str(phrases))
    chars = [c["ch"] for c in d["top_chars"]]
    check("4.2 「猫」排第一（5 次）", chars and chars[0] == "猫", str(chars[:3]))
    check("4.3 停用字被过滤", not ({"的", "了", "是", "我", "你"} & set(chars)), str(chars[:6]))
    check("4.4 只统计汉字", all(len(c["ch"]) == 1 for c in d["top_chars"]))
    check("4.5 平均长度合理", 3 <= d["avg_user_len"] <= 30, str(d["avg_user_len"]))

    section("5. 空库不炸")
    tmp2 = Path(tempfile.mkdtemp(prefix="klee-ins2-"))
    (tmp2 / "chat.jsonl").write_text("", encoding="utf-8")
    idx2 = MessageIndex(tmp2 / "e.db", {"chat": tmp2 / "chat.jsonl"}, tmp2 / "o.json")
    idx2.sync(force=True)
    e = idx2.insights(days=7, src="chat")
    check("5.1 空库 total=0", e["total"] == 0, str(e["total"]))
    check("5.2 空库三张表都是空", not e["heat"] and not e["top_chars"] and not e["by_character"])
    check("5.3 空库不报错", e["from_sql"] is True)

    section("6. 活服务接口")
    def get(url):
        req = urllib.request.Request(url, headers=HDR)
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())

    try:
        code, live = get(BASE + "/api/insights?days=30")
        check("6.1 /api/insights 200", code == 200 and live.get("ok"), str(code))
        for k in ("heat", "by_character", "by_day", "top_chars", "top_phrases", "total"):
            check(f"6.2 带 {k}", k in live, str(list(live)[:8]))
        check("6.3 src 回显", live.get("src") == "messages", str(live.get("src")))
        # 换 src 要真的换数据源（网页聊天和 QQ 消息是两份记录）
        _, ch = get(BASE + "/api/insights?days=30&src=chat")
        check("6.4 src=chat 生效", ch.get("src") == "chat", str(ch.get("src")))
        _, dm = get(BASE + "/api/insights?days=1")
        check("6.5 days 生效", dm.get("days") == 1, str(dm.get("days")))
        _, bad = get(BASE + "/api/insights?days=99999")
        check("6.6 超范围天数被夹住", bad.get("days") == 120, str(bad.get("days")))

        _, us = get(BASE + "/api/usage?days=7")
        check("6.7 用量带角色维度", isinstance(us.get("characters"), list), str(us.get("characters")))
        if us.get("characters"):
            c0 = us["characters"][0]
            check("6.8 角色维度含 tokens", {"id", "requests", "total"} <= set(c0), str(c0))
    except (urllib.error.URLError, OSError) as e:
        print(f"  · 服务没起来，跳过（{type(e).__name__}）")

    print(f"\nPASS {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败：" + "、".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
