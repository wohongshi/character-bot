"""来源统计必须按全量算 + 等级表必须缓存。

两个都是「不报错、只是悄悄错/悄悄慢」的问题：

1. `_feed_sources` 以前复用 `_read_jsonl`（只读最后 4MB），文件一超过这个窗口，
   「QQ 群 · xxx（2825 条）」就和真实条数（5 万）对不上。这里造一份 4.5MB 的
   样本，逐项比对真实计数。
2. `_levels` 以前每次调用都 ast.parse 一遍 affinity.py（13.4ms）。看板每 60 秒
   拉一次，等于白烧 CPU。这里验证它按 mtime 缓存、并且改文件后会失效。
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="feedsrc-"))
os.environ["KLEE_STATE_DIR"] = str(TMP)
sys.path.insert(0, str(ROOT))

import server  # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


# ---------- 造样本：跨过 4MB 尾部窗口 ----------
BIG = TMP / "messages.jsonl"
CIDS = ["group:780001", "private:10002", "web:studio-main", "group:333444555"]
TRUE = {}
with BIG.open("w", encoding="utf-8") as f:
    for i in range(20000):
        cid = CIDS[i % 4]
        TRUE[cid] = TRUE.get(cid, 0) + 1
        f.write(json.dumps({"ts": 1791000000 + i, "role": "user" if i % 2 else "assistant",
                            "text": "撑体积的一段中文内容" * 12, "chat_id": cid},
                           ensure_ascii=False) + "\n")
MB = BIG.stat().st_size / 1e6
print(f"样本 {MB:.1f} MB / 20000 行（Python 尾部窗口 4 MB）")

server.MSG_JSONL = BIG
server._SRC_CACHE.update(key=None, data=None)

print("\n· 来源统计（全量口径）")
labels = {x["value"]: x["label"] for x in server._feed_sources()}
# 注意：所有 web:* 会被合并成一项 value="web"，所以只逐个校验 QQ 来源
qq_ids = [c for c in TRUE if c.startswith(("group:", "private:"))]
check("QQ 来源全在", set(qq_ids) <= set(labels), str(sorted(labels)))
for cid in qq_ids:
    want = f"（{TRUE[cid]} 条）"
    got = labels.get(cid, "")
    check(f"{cid} 计数正确（{TRUE[cid]}）", want in got, got)
check("web:* 不单独出现", not any(k.startswith("web:") for k in labels), str(sorted(labels)))
web_total = TRUE.get("web:studio-main", 0)
check("网页合并成一项", "网页 / 其他" in labels.get("web", ""), labels.get("web"))
check("网页计数正确", f"（{web_total} 条）" in labels.get("web", ""), labels.get("web"))
check("第一项是「全部来源」", list(labels)[0] == "", str(list(labels)[:2]))

print("\n· 缓存：同一份文件不重复解析")
server._SRC_CACHE.update(key=None, data=None)
t0 = time.perf_counter()
server._feed_sources()
first = (time.perf_counter() - t0) * 1000
t0 = time.perf_counter()
server._feed_sources()
second = (time.perf_counter() - t0) * 1000
check(f"第二次命中缓存（{first:.1f} → {second:.3f} ms）", second < max(1.0, first / 10),
      f"{first:.2f} → {second:.2f}")

print("\n· 文件追加后缓存失效")
with BIG.open("a", encoding="utf-8") as f:
    f.write(json.dumps({"ts": 1791100000, "role": "user", "text": "新的一条",
                        "chat_id": "group:780001"}, ensure_ascii=False) + "\n")
labels2 = {x["value"]: x["label"] for x in server._feed_sources()}
new_n = TRUE["group:780001"] + 1
check("追加后计数 +1", f"（{new_n} 条）" in labels2.get("group:780001", ""),
      labels2.get("group:780001"))

print("\n· 等级表缓存")
server._LEVELS_CACHE.update(key=None, data=None)
t0 = time.perf_counter()
lv1 = server._levels()
cold = (time.perf_counter() - t0) * 1000
t0 = time.perf_counter()
lv2 = server._levels()
warm = (time.perf_counter() - t0) * 1000
check(f"第一次 {cold:.1f}ms，第二次 {warm:.3f}ms", warm < max(0.5, cold / 10), f"{cold:.2f}/{warm:.2f}")
check("两次结果一致", lv1 == lv2, f"{lv1[:2]} vs {lv2[:2]}")
check("等级表非空且形如 (门槛, 称号)", bool(lv1) and isinstance(lv1[0], tuple) and len(lv1[0]) == 2,
      str(lv1[:1]))

# 改 mtime 后应重新解析（模拟插件热重载）
src = ROOT / "bot" / "features" / "affinity.py"
old = server._LEVELS_CACHE["key"]
os.utime(src, None)
lv3 = server._levels()
check("文件 mtime 变了 → 重新解析且结果相同", server._LEVELS_CACHE["key"] != old and lv3 == lv1,
      f"{old} → {server._LEVELS_CACHE['key']}")

print("\n· 文件不存在时不炸")
server.MSG_JSONL = TMP / "nope.jsonl"
server._SRC_CACHE.update(key=None, data=None)
try:
    out = server._feed_sources()
    check("缺文件 → 只给「全部来源」", out == [{"value": "", "label": "全部来源"}], str(out))
except Exception as e:                                              # noqa: BLE001
    check("缺文件 → 不抛异常", False, f"{type(e).__name__}: {e}")

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(1 if FAIL else 0)
