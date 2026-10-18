"""JSONL 全表扫描 vs SQLite 索引：为「要不要上 SQL」量出数字。

数据集：20 万条消息（和真实 74MB 那批同量级），
比三件事：关键词搜索 / 单个会话最近 N 条 / 按天+角色聚合。
"""
from __future__ import annotations

import json
import os
import random
import sqlite3
import time
from pathlib import Path

TMP = Path(__file__).resolve().parent.parent / ".bench"
TMP.mkdir(parents=True, exist_ok=True)
JSONL = TMP / "messages.jsonl"
DB = TMP / "messages.db"

N = 200_000
CHARS = 6
WORDS = ("炸鱼 火花 骑士 天使 咖啡 实验室 机械 心 苦情树 红线 幽灵 蝴蝶 "
         "烟谜 观星 旅行 记忆 回声 潜水 火箭拳 电量").split()


def gen():
    if JSONL.exists() and JSONL.stat().st_size > 50 * 1024 * 1024:
        return
    rnd = random.Random(7)
    base = time.time() - 86400 * 240
    with open(JSONL, "w", encoding="utf-8") as f:
        for i in range(N):
            ts = base + i * 100
            text = " ".join(rnd.choice(WORDS) for _ in range(rnd.randint(3, 18)))
            f.write(json.dumps({
                "id": f"{ts:.3f}-{i}",
                "ts": ts, "session": f"studio-{rnd.randint(0, 40)}",
                "character": f"c{rnd.randint(0, CHARS - 1)}",
                "role": "user" if rnd.random() < 0.5 else "assistant",
                "text": text,
                "elapsed": round(rnd.uniform(0.5, 8), 1),
            }, ensure_ascii=False) + "\n")


def build_db():
    if DB.exists():
        return
    con = sqlite3.connect(DB)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""CREATE TABLE msg(
        id INTEGER PRIMARY KEY, ts REAL NOT NULL, session TEXT NOT NULL,
        character TEXT NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL,
        elapsed REAL)""")
    con.execute("CREATE INDEX idx_msg_sess_ts ON msg(session, ts)")
    con.execute("CREATE INDEX idx_msg_char_ts ON msg(character, ts)")
    con.execute("CREATE VIRTUAL TABLE msg_fts USING fts5(text, content='msg', content_rowid='id')")
    rows = []
    with open(JSONL, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            rows.append((d["ts"], d["session"], d["character"], d["role"],
                         d["text"], d.get("elapsed") or 0))
            if len(rows) >= 5000:
                con.executemany("INSERT INTO msg(ts,session,character,role,text,elapsed)"
                                " VALUES(?,?,?,?,?,?)", rows)
                rows.clear()
    if rows:
        con.executemany("INSERT INTO msg(ts,session,character,role,text,elapsed)"
                        " VALUES(?,?,?,?,?,?)", rows)
    con.execute("INSERT INTO msg_fts(rowid, text) SELECT id, text FROM msg")
    con.commit()
    con.close()


def ms(fn, repeat=3):
    best = None
    for _ in range(repeat):
        t = time.perf_counter()
        out = fn()
        dt = (time.perf_counter() - t) * 1000
        best = dt if best is None else min(best, dt)
    return round(best, 1), out


# ---------- JSONL 侧 ----------
def jsonl_search(word):
    hits = 0
    with open(JSONL, encoding="utf-8") as f:
        for line in f:
            if word in line:                     # 不做 JSON 解析已经是最快路径
                hits += 1
    return hits


def jsonl_recent(session, n):
    keep = []
    with open(JSONL, encoding="utf-8") as f:
        for line in f:
            if session in line:
                keep.append(line)
                if len(keep) > n:
                    del keep[0]
    return keep


def jsonl_by_day():
    from collections import Counter
    c = Counter()
    with open(JSONL, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            c[(d["character"], time.strftime("%Y-%m", time.localtime(d["ts"])))] += 1
    return len(c)


# ---------- SQLite 侧 ----------
def sql_search(word):
    con = sqlite3.connect(DB)
    try:
        return con.execute("SELECT count(*) FROM msg_fts WHERE msg_fts MATCH ?",
                           (word,)).fetchone()[0]
    finally:
        con.close()


def sql_recent(session, n):
    con = sqlite3.connect(DB)
    try:
        return con.execute("SELECT id FROM msg WHERE session=? ORDER BY ts DESC LIMIT ?",
                           (session, n)).fetchall()
    finally:
        con.close()


def sql_by_day():
    con = sqlite3.connect(DB)
    try:
        return con.execute("SELECT character, strftime('%Y-%m', ts, 'unixepoch'),"
                           " count(*) FROM msg GROUP BY 1,2").fetchall()
    finally:
        con.close()


gen()
print("数据集:", JSONL.stat().st_size / 1048576, "MB", N, "条")
t0 = time.time()
build_db()
print("建库耗时:", round(time.time() - t0, 1), "s  库大小:",
      round(DB.stat().st_size / 1048576, 1), "MB")

tests = [
    ("关键词搜索（全表）", lambda: jsonl_search("火花"), lambda: sql_search("火花")),
    ("会话最近 50 条", lambda: jsonl_recent("studio-7", 50), lambda: sql_recent("studio-7", 50)),
    ("按天×角色聚合", jsonl_by_day, sql_by_day),
]
print()
print(f"{'场景':22} {'JSONL 扫描':>12} {'SQLite':>10} {'加速':>8}")
for name, a, b in tests:
    ta, ra = ms(a)
    tb, rb = ms(b)
    cnt_a = len(ra) if hasattr(ra, "__len__") else ra
    cnt_b = len(rb) if hasattr(rb, "__len__") else rb
    print(f"{name:22} {ta:>10} ms {tb:>8} ms {ta / max(tb, 0.01):>7.0f}×   "
          f"({cnt_a} vs {cnt_b})")

# 增量写入：追一条要多久
def jsonl_append():
    with open(JSONL, "a", encoding="utf-8") as f:
        f.write(json.dumps({"id": "x", "ts": time.time(), "session": "s",
                            "character": "c0", "role": "user", "text": "hi"},
                           ensure_ascii=False) + "\n")


def sql_append():
    con = sqlite3.connect(DB)
    try:
        cur = con.execute("INSERT INTO msg(ts,session,character,role,text,elapsed)"
                          " VALUES(?,?,?,?,?,?)", (time.time(), "s", "c0", "user", "hi", 0))
        con.execute("INSERT INTO msg_fts(rowid,text) VALUES(?,?)", (cur.lastrowid, "hi"))
        con.commit()
    finally:
        con.close()


print()
for name, fn in (("追加一条 JSONL", jsonl_append), ("追加一条 SQLite", sql_append)):
    dt, _ = ms(fn, repeat=5)
    print(f"{name:22} {dt:>10} ms")
