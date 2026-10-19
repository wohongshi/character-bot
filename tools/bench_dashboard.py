"""看板聚合：Python 遍历 JSONL vs SQL GROUP BY（同一份 20 万行数据）。

    python3 tools/bench_dashboard.py [行数]
"""
from __future__ import annotations

import json
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot.store import MessageIndex                        # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 200_000
CHATS = ["group:1077339356", "group:333444555", "private:2100950993",
         "web:studio-main", "web:panel"]
CHARS = ["klee", "hutao", "honghong", "atri", "citlali", "cyrene"]


def make_rows(n: int) -> list[dict]:
    random.seed(7)
    now = time.time()
    out = []
    for i in range(n):
        ts = now - (n - i) * 18            # 约 41 天铺开
        out.append({
            "ts": round(ts, 3),
            "chat_id": random.choice(CHATS),
            "character": random.choice(CHARS),
            "role": "user" if i % 2 == 0 else "assistant",
            "text": f"第 {i} 条消息，内容随便写一点，用来占体积" * 2,
            "session": "studio-main",
        })
    return out


def python_loop(rows: list[dict], days: int) -> dict:
    """老实现：逐行解析 + 计数（和 server.py 里的回落分支同一个写法）。"""
    now = time.time()
    day_rows, src_map = {}, {}
    total = n_user = n_bot = 0
    for e in rows:
        if e.get("role") not in ("user", "assistant") or not e.get("text"):
            continue
        ts = float(e.get("ts") or 0)
        d = time.strftime("%Y-%m-%d", time.localtime(ts))
        day_rows[d] = day_rows.get(d, 0) + 1
        total += 1
        if e.get("role") == "user":
            n_user += 1
        else:
            n_bot += 1
        cid = str(e.get("chat_id") or "")
        cur = src_map.setdefault(cid, {"n": 0, "last": 0.0})
        cur["n"] += 1
        cur["last"] = max(cur["last"], ts)
    return {"total": total, "user": n_user, "assistant": n_bot, "sources": len(src_map)}


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="bench-dash-"))
    try:
        msg = tmp / "messages.jsonl"
        chat = tmp / "chat.jsonl"
        chat.write_text("", encoding="utf-8")
        print(f"造 {N} 行…")
        rows = make_rows(N)
        with msg.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        size = msg.stat().st_size / 1024 / 1024
        print(f"  {msg.stat().st_size / 1024 / 1024:.1f} MB")

        idx = MessageIndex(tmp / "messages.db", {"chat": chat, "messages": msg},
                          tmp / "offsets.json")
        idx.ensure()
        t0 = time.perf_counter()
        idx.sync()
        build = (time.perf_counter() - t0) * 1000
        print(f"  建索引 {build:.0f} ms")

        # 从 JSONL 重新读一遍（模拟服务端冷启动）
        t0 = time.perf_counter()
        loaded = [json.loads(x) for x in msg.read_text(encoding="utf-8").splitlines() if x.strip()]
        read_ms = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        py = python_loop(loaded, 14)
        py_ms = (time.perf_counter() - t0) * 1000

        idx.summary(14)                       # 预热
        t0 = time.perf_counter()
        for _ in range(5):
            sq = idx.summary(14)
        sql_ms = (time.perf_counter() - t0) / 5 * 1000

        print(f"\n  读 JSONL + 解析        {read_ms:8.1f} ms")
        print(f"  Python 遍历聚合        {py_ms:8.1f} ms")
        print(f"  SQL GROUP BY（热）     {sql_ms:8.1f} ms   → 快 {py_ms / max(sql_ms, 0.01):.0f}×")
        print(f"\n  结果一致性：total {py['total']} vs {sq['total']}，"
              f"user {py['user']} vs {sq['user']}，来源 {py['sources']} vs {len(sq['sources'])}")
        assert py["total"] == sq["total"] and py["user"] == sq["user"], "两条路径结果不一致"
        print("  ✓ 两条路径结果一致")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
