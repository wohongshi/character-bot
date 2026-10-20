"""用量按会话归账：usage 行带 chat → 汇总 → 面板；以及「工具循环也要记账」。

跑法：`python3 tests/test_usage_chat.py`（不联网）
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="usage-chat-"))
os.environ["KLEE_STATE_DIR"] = str(TMP / "ps")

from bot import llm                                        # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


print("[1] _emit_usage 带 chat")
seen = []
llm.set_usage_hook(lambda row: seen.append(row))
try:
    llm._emit_usage("p", "m", {"prompt_tokens": 10, "completion_tokens": 5,
                               "total_tokens": 15}, 12.5, "chat", 0, "hutao", "group:1")
    llm._emit_usage("p", "m", {"prompt_tokens": 1, "completion_tokens": 1,
                               "total_tokens": 2}, 1.0, "chat", 0, "klee")
finally:
    llm.set_usage_hook(None)
check("chat 落进了那一行", seen and seen[0].get("chat") == "group:1", str(seen[:1]))
check("角色也还在", seen and seen[0].get("character") == "hutao")
check("没传 chat 就是空串（不是 None）", seen and seen[1].get("chat") == "",
      str(seen[1:2]))
llm.set_usage_hook(lambda row: seen.append(row))
try:
    llm._emit_usage("p", "m", {}, 1, "chat", 0, "", "x" * 200)
finally:
    llm.set_usage_hook(None)
check("超长 chat 会被截断到 64", len(seen[-1]["chat"]) == 64, str(len(seen[-1]["chat"])))

print("[2] chat() 把 chat 传给 complete_raw")
store = llm.ModelStore(TMP / "models.json")
got = {}


def fake_once(messages, **kw):
    got.update(kw)
    return {"text": "ok", "tool_calls": [], "finish_reason": "stop",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "provider": "p", "model": "m"}


orig = store._complete_once
store._complete_once = fake_once
try:
    store.chat([{"role": "user", "content": "hi"}], provider="p", model="m",
               character="hutao", chat="group:1")
    check("_complete_once 收到了 _chat", got.get("_chat") == "group:1", str(got.get("_chat")))
    check("也收到了 _character", got.get("_character") == "hutao")
finally:
    store._complete_once = orig

print("[3] 汇总按会话")
import server as SV                                        # noqa: E402

SV.USAGE_JSONL = TMP / "usage.jsonl"
now = time.time()
rows = [
    {"ts": now - 10, "provider": "p", "model": "m", "character": "hutao",
     "chat": "group:1", "prompt_tokens": 100, "completion_tokens": 50,
     "total_tokens": 150, "ms": 1000.0},
    {"ts": now - 20, "provider": "p", "model": "m", "character": "hutao",
     "chat": "group:1", "prompt_tokens": 200, "completion_tokens": 100,
     "total_tokens": 300, "ms": 2000.0},
    {"ts": now - 30, "provider": "p", "model": "m", "character": "klee",
     "chat": "private:9", "prompt_tokens": 50, "completion_tokens": 10,
     "total_tokens": 60, "ms": 500.0},
    {"ts": now - 40, "provider": "p", "model": "m", "character": "klee",
     "prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10, "ms": 100.0},
    {"ts": now - 5 * 86400, "provider": "p", "model": "m", "character": "klee",
     "chat": "group:2", "prompt_tokens": 9, "completion_tokens": 9,
     "total_tokens": 18, "ms": 90.0},
]
SV.USAGE_JSONL.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
                          + "不是 json 的一行\n", encoding="utf-8")

d = SV._usage_summary(7)
by = {c["chat"]: c for c in d["chats"]}
check("三条会话 + 一条未记录", len(d["chats"]) == 4, str([c["chat"] for c in d["chats"]]))
check("group:1 累计 450", by["group:1"]["total"] == 450, str(by.get("group:1")))
check("group:1 两次调用", by["group:1"]["requests"] == 2)
check("group:1 输入 300", by["group:1"]["prompt"] == 300)
check("老记录归到「未记录」", by[""]["total"] == 10, str(by.get("")))
check("按 total 从大到小", [c["chat"] for c in d["chats"]][:2] == ["group:1", "private:9"],
      str([c["chat"] for c in d["chats"]]))
check("总账不变（还是 538）", d["totals"]["total"] == 538, str(d["totals"]["total"]))
check("坏行不影响解析", d["rows"] == 5, str(d["rows"]))

# 「今天」按本地零点算，不是滚动 24 小时
lt = time.localtime()
midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
rows.append({"ts": midnight - 60, "provider": "p", "model": "m", "character": "klee",
             "chat": "group:zz", "prompt_tokens": 7, "completion_tokens": 3,
             "total_tokens": 10, "ms": 10.0})       # 昨天 23:59
SV.USAGE_JSONL.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                          encoding="utf-8")
dt = SV._usage_summary(today=True)
check("今天不含昨天 23:59 的花费", all(c["chat"] != "group:zz" for c in dt["chats"]),
      str([c["chat"] for c in dt["chats"]]))
check("滚动 24 小时会把它算进来（这就是差异）",
      any(c["chat"] == "group:zz" for c in SV._usage_summary(1)["chats"]))
SV.USAGE_JSONL.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows[:-1]),
                          encoding="utf-8")

d1 = SV._usage_summary(1)
check("按天数过滤（1 天只剩 4 条）", d1["rows"] == 4, str(d1["rows"]))
check("今天的 group:2 不见了", all(c["chat"] != "group:2" for c in d1["chats"]))
check("chats 最多 8 条", len(SV._usage_summary(0)["chats"]) <= 8)

SV.USAGE_JSONL.write_text("", encoding="utf-8")
d0 = SV._usage_summary(7)
check("没有记录时 chats 是空数组（不是 None）", d0["chats"] == [], str(d0["chats"]))
check("空数据不炸", d0["totals"]["total"] == 0 and d0["totals"]["avg_ms"] == 0)

print("[4] 面板")
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
for anchor in ('id="ucCard"', 'id="ucBox"', "function ucLoad", "function ucSet",
               'id="uc7"', 'id="uc1"', 'id="uc30"', "ucLoad();"):
    check(f"面板有 {anchor}", anchor in HTML)
check("未记录的会话有单独文案", "未记录（老数据）" in HTML)
check("按天数按钮带高亮", 'b.classList.toggle("primary", k === days)' in HTML)

print("[5] 工具循环也记账（以前 agent 那段角色是空的）")
import bot.agent as A                                      # noqa: E402

calls = []


def fake_complete(messages, **kw):
    calls.append(kw)
    return {"text": "好了", "tool_calls": [], "finish_reason": "stop", "usage": {},
            "provider": "p", "model": "m"}


A.run(None, [{"role": "user", "content": "hi"}], max_steps=1, complete=fake_complete,
      character="hutao", scope="group:1")
check("agent 把 character 传给模型层", calls and calls[0].get("character") == "hutao",
      str(calls[:1]))
check("agent 把会话传给模型层", calls and calls[0].get("chat") == "group:1",
      str(calls[:1]))

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(0 if FAIL == 0 else 1)
