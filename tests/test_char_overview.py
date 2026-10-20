"""角色总览：每个角色一行（装没装 / 知识 / 命中 / 花费 / 报错）。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="charov-"))
os.environ["KLEE_STATE_DIR"] = str(TMP / "ps")

import server as SV                                       # noqa: E402
from bot import chat_char as CC                            # noqa: E402
from bot.characters import CharacterStore                  # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


ST = TMP / "state"
ST.mkdir()
store = CharacterStore(ST, seeds_dir=ROOT / "bot" / "character_seeds")
SV.STATE_DIR = ST
SV._char_store = lambda: store
SV.BOT = None
SV.KB_HITS = ST / "kb_hits.jsonl"
SV.USAGE_JSONL = ST / "usage.jsonl"
def kb_cache(chars, ok=True):
    """塞一份「刚取到」的缓存（ts 用现在；0 会让 _kb_health 去连真实服务）。"""
    SV._KB_HEALTH.update(ts=time.time(), data={"ok": ok, "characters": chars})


kb_cache({"klee": {"chunks": 274}, "hutao": {"chunks": 210}})

now = time.time()
CC.set_binding(ST, "group:1", "hutao", valid_ids=["klee", "hutao"])
CC.set_binding(ST, "private:2", "hutao", valid_ids=["klee", "hutao"])
CC.set_binding(ST, "group:3", "nokb", valid_ids=["klee", "hutao", "nokb"]) if False else None

SV.USAGE_JSONL.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in [
    {"ts": now - 60, "character": "klee", "total_tokens": 1000, "ms": 2000.0},
    {"ts": now - 120, "character": "klee", "total_tokens": 500, "ms": 1000.0},
    {"ts": now - 180, "character": "hutao", "total_tokens": 300, "ms": 900.0},
    {"ts": now - 5 * 86400, "character": "klee", "total_tokens": 9999, "ms": 100.0},
    {"ts": now - 60, "character": "", "total_tokens": 77, "ms": 10.0},
]) + "\n", encoding="utf-8")
SV.KB_HITS.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in [
    {"ts": now - 60, "character": "klee"},
    {"ts": now - 70, "character": "klee"},
    {"ts": now - 80, "character": "hutao"},
    {"ts": now - 5 * 86400, "character": "klee"},
]) + "\n", encoding="utf-8")

print("[1] 基本结构")
d = SV._char_overview(7)
check("ok 且 6 个角色", d["ok"] is True and d["totals"]["chars"] == 6, str(d["totals"]))
by = {r["id"]: r for r in d["items"]}
check("当前角色排第一", d["items"][0]["active"] is True, d["items"][0]["name"])
check("每行字段齐全", all(
    {"id", "name", "loaded", "plugins", "plugins_total", "bound", "kb_chunks",
     "kb_hits", "tokens", "requests", "avg_ms", "errors", "soul_chars"} <= set(r)
    for r in d["items"]), str(sorted(d["items"][0])))

print("[2] 数字对不对")
# 5 天前那条**在** 7 天窗口里：1000+500+9999
check("7 天窗口包含 5 天前的数据", by["klee"]["tokens"] == 11499, str(by["klee"]["tokens"]))
check("请求次数", by["klee"]["requests"] == 3, str(by["klee"]["requests"]))
check("平均延迟按次数摊", by["klee"]["avg_ms"] == 1033, str(by["klee"]["avg_ms"]))
check("命中数按角色", by["klee"]["kb_hits"] == 3 and by["hutao"]["kb_hits"] == 1,
      str((by["klee"]["kb_hits"], by["hutao"]["kb_hits"])))
check("知识块数来自 klee-kb", by["klee"]["kb_chunks"] == 274 and by["hutao"]["kb_chunks"] == 210,
      str((by["klee"]["kb_chunks"], by["hutao"]["kb_chunks"])))
check("没有库的角色是 0", by["cyrene"]["kb_chunks"] == 0, str(by["cyrene"]["kb_chunks"]))
check("绑定数", by["hutao"]["bound"] == 2 and by["klee"]["bound"] == 0,
      str((by["hutao"]["bound"], by["klee"]["bound"])))
check("人格字数 > 0", all(r["soul_chars"] > 0 for r in d["items"]))
check("空角色名的用量行不算进任何角色", d["totals"]["tokens"] == 11799,
      str(d["totals"]["tokens"]))
check("排序：绑定的排在没绑的前面",
      [r["id"] for r in d["items"]][:1] == [store.active_id()], str([r["id"] for r in d["items"]]))

print("[3] 天数窗口")
d1 = SV._char_overview(1)
check("1 天窗口排除 5 天前的数据", {r["id"]: r for r in d1["items"]}["klee"]["tokens"] == 1500,
      str({r["id"]: r["tokens"] for r in d1["items"]}))
d0 = SV._char_overview(365)
check("365 天和 7 天一样（没有更旧的数据）",
      {r["id"]: r for r in d0["items"]}["klee"]["tokens"] == 11499,
      str({r["id"]: r["tokens"] for r in d0["items"]}))

print("[4] 告警：绑了会话但没知识库")
kb_cache({"klee": {"chunks": 274}})
CC.set_binding(ST, "group:9", "hutao", valid_ids=["klee", "hutao"])
d2 = SV._char_overview(7)
check("绑了但没库 → 进 warn", "hutao" in (d2["warn"] or []), str(d2["warn"]))
check("没绑的不告警", "cyrene" not in (d2["warn"] or []), str(d2["warn"]))
kb_cache({"klee": {"chunks": 274}, "hutao": {"chunks": 210}})

print("[5] klee-kb 挂了也要出数（只是知识块为 0）")
kb_cache({}, ok=False)
d3 = SV._char_overview(7)
check("知识库不可用时不炸", d3["ok"] is True and len(d3["items"]) == 6)
check("知识块都是 0", all(r["kb_chunks"] == 0 for r in d3["items"]))
kb_cache({"klee": {"chunks": 274}, "hutao": {"chunks": 210}})

print("[6] 坏行不影响")
SV.USAGE_JSONL.write_text('{"ts": "坏", "character": "klee"}\n不是 json\n' +
                          json.dumps({"ts": now, "character": "klee",
                                      "total_tokens": 10, "ms": 1.0}) + "\n",
                          encoding="utf-8")
d4 = SV._char_overview(7)
check("坏用量行被跳过", {r["id"]: r for r in d4["items"]}["klee"]["tokens"] == 10,
      str({r["id"]: r["tokens"] for r in d4["items"]}))

print("[7] 面板")
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
for anchor in ('id="ovCard"', 'id="ovBox"', "function ovLoad", "/api/characters/overview",
               "ovrow", "待命", "plugins_total"):
    check(f"面板有 {anchor}", anchor in HTML)
SRV = (ROOT / "server.py").read_text(encoding="utf-8")
check("路由在", '"/api/characters/overview"' in SRV)
check("敏感名单里有", '"/api/characters/overview"' in
      SRV.split("_SENSITIVE_GET = frozenset({")[1].split("})")[0])

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(0 if FAIL == 0 else 1)
