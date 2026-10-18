"""SQLite 消息索引：搜索 / 去重 / 增量 / 重建 / 删除。

这一套守的是「索引是索引，不是真相」：JSONL 删了重建就完事，
同步怎么重复跑都不该出现重复行。
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.store import MessageIndex        # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


TMP = Path(tempfile.mkdtemp())
CHAT = TMP / "chat.jsonl"
MSGS = TMP / "messages.jsonl"
DB = TMP / "messages.db"

now = time.time()


def line(session, role, text, cid="klee", ts=None):
    return json.dumps({"ts": ts or now, "session": session, "character": cid,
                       "role": role, "text": text, "via": "plugin"},
                      ensure_ascii=False)


CHAT.write_text("\n".join([
    line("s1", "user", "你好呀", ts=now - 300),
    line("s1", "assistant", "看树。红绳有点松，得紧一紧。", ts=now - 299),
    line("s2", "user", "hello world", ts=now - 200),
]) + "\n", encoding="utf-8")
# messages.jsonl 里会有重复行（两个来源都写），索引要能去掉
MSGS.write_text("\n".join([
    line("s2", "user", "hello world", ts=now - 200),
    line("", "user", "另一条独立的", cid="hutao", ts=now - 100),
]) + "\n", encoding="utf-8")

print("· 建库与首次同步")
idx = MessageIndex(DB, {"chat": CHAT, "messages": MSGS}, TMP / "idx.json")
check("库文件建出来了", DB.exists())
r = idx.sync()
check("首次入库 5 行", r["added"] == 5, str(r))
check("健康检查有行数", idx.health()["rows"] == 5, str(idx.health()))

print("\n· 重复同步不产生重复行")
r2 = idx.sync()
check("再跑一次没有新增", r2["added"] == 0, str(r2))
check("行数没变", idx.health()["rows"] == 5, str(idx.health()))

print("\n· 搜索")
hits = idx.search("红绳")
check("中文能搜到", len(hits) == 1, str(hits)[:120])
check("带高亮片段", "<<红绳>>" in str(hits[0].get("snippet")), str(hits[0].get("snippet")))
hits = idx.search("hello")
check("英文能搜到，且跨来源去重成 1 条", len(hits) == 1, str(len(hits)))
check("大小写不敏感", len(idx.search("HELLO")) == 1)
check("单字也能搜（走 LIKE 退路）", len(idx.search("树")) == 1, str(len(idx.search("树"))))
check("搜不到就是空", idx.search("不存在的词zzz") == [])
check("空查询返回空（不扫全表）", idx.search("  ") == [])
check("按角色过滤", len(idx.search("另一条", character="hutao")) == 1)
check("角色不对就搜不到", idx.search("另一条", character="klee") == [])
check("FTS 语法字符不会炸", isinstance(idx.search('a"b*c-d'), list))

print("\n· 增量同步")
with open(CHAT, "a", encoding="utf-8") as f:
    f.write(line("s3", "user", "新来的一条", ts=now) + "\n")
r3 = idx.sync()
check("只补新的 1 条", r3["added"] == 1, str(r3))
check("新消息能搜到", len(idx.search("新来的")) == 1)

print("\n· 文件被重写（删会话）→ 该来源重建")
keep = "\n".join([line("s2", "user", "hello world", ts=now - 200)]) + "\n"
CHAT.write_text(keep, encoding="utf-8")
r4 = idx.sync()
check("检测到缩水并重建", "chat" in r4["rebuilt"], str(r4))
check("被删掉的消息搜不到了", idx.search("新来的") == [], str(idx.search("新来的")))
check("保留的还在", len(idx.search("hello")) == 1)
check("重建后没有重复", idx.health()["rows"] == 3, str(idx.health()))

print("\n· 显式删会话")
n = idx.drop_session("s2")
check("删掉 2 行（chat + messages 各一条）", n == 2, str(n))
check("删完搜不到", idx.search("hello") == [])
check("没被误删的还在", len(idx.search("另一条")) == 1)

print("\n· 统计")
st = idx.stats(14)
check("total 对齐", st["total"] == idx.health()["rows"], f'{st["total"]} vs {idx.health()["rows"]}')
check("按角色有分组", any(x["id"] == "hutao" for x in st["by_character"]), str(st["by_character"]))

print("\n· 容错")
bad = TMP / "bad.db"
Path(bad).mkdir(parents=True, exist_ok=True)          # 目录当文件用 → 打不开
idx2 = MessageIndex(bad / "x.db", {}, TMP / "idx2.json")
idx2.sources = {"chat": CHAT}
try:
    res = idx2.sync()
    check("坏库不抛异常（返回 error 字段）", isinstance(res, dict), str(res))
except Exception as e:                                 # noqa: BLE001
    check("坏库不抛异常（返回 error 字段）", False, f"{type(e).__name__}: {e}")

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(1 if FAIL else 0)
