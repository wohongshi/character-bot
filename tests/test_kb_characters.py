#!/usr/bin/env python3
"""多角色知识库：/healthz 列表 + /search 按 character 隔离。"""
import json
import sys
import tempfile
import urllib.error
import urllib.request

KB = "http://127.0.0.1:8797"
PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  [{extra}]" if extra and not ok else ""))


def get(path):
    with urllib.request.urlopen(KB + path, timeout=10) as r:
        return json.loads(r.read().decode())


def search(cid, text, k=3, threshold=0.35):
    req = urllib.request.Request(KB + "/search", data=json.dumps(
        {"text": text, "k": k, "threshold": threshold, "character": cid}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


try:
    h = get("/healthz")
except Exception as e:                                            # noqa: BLE001
    print("知识库没在跑，跳过：", e)
    sys.exit(0)

print("[1] healthz")
chars = h.get("characters") or {}
check("1.1 五个角色都有索引", set(chars) >= {"klee", "hutao", "citlali", "cyrene", "atri"}, str(list(chars)))
check("1.2 每个角色有名字和块数",
      all(c.get("name") and int(c.get("chunks") or 0) > 0 for c in chars.values()), str(chars))
check("1.3 旧字段保留（默认角色）", h.get("chunks") and h.get("model"))

print("[2] 按角色检索")
cases = {
    "klee": ("可莉喜欢炸鱼吗", ("可莉", "炸鱼")),
    "hutao": ("往生堂的堂主是谁", ("胡桃", "往生堂")),
    "citlali": ("烟谜主的占卜", ("茜特菈莉", "烟谜主", "占卜")),
    "cyrene": ("记忆与遗忘", ("昔莲", "记忆")),
    "atri": ("高性能机器人", ("亚托莉", "机器人")),
}
for cid, (q, words) in cases.items():
    d = search(cid, q)
    blob = json.dumps(d.get("hits") or [], ensure_ascii=False)
    check(f"2 {cid} 命中并带对角色",
          d.get("character") == cid and d.get("hits")
          and any(w in blob for w in words), (d.get("character"), len(d.get("hits") or [])))

print("[3] 不串味")
d = search("klee", "往生堂第七十七代堂主")
blob = json.dumps(d.get("hits") or [], ensure_ascii=False)
check("3.1 可莉库里没有胡桃条目", "胡桃" not in blob and "往生堂" not in blob, blob[:160])
d = search("atri", "烟谜主")
blob = json.dumps(d.get("hits") or [], ensure_ascii=False)
check("3.2 亚托莉库里没有纳塔条目", "烟谜主" not in blob and "茜特菈莉" not in blob, blob[:160])
d = search("", "可莉")
check("3.3 不传角色 → 默认可莉", d.get("character") == "klee", str(d.get("character")))
d = search("nope", "你好")
check("3.4 未知角色 → 回退默认（不 500）", d.get("character") == "klee" and d.get("ok"), str(d)[:120])

print()
print(f"PASS {len(PASS)} / {len(PASS) + len(FAIL)}")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
