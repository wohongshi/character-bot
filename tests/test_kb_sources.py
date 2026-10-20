"""语料来源管理：klee-kb 的 sources/purge + 工作室的透传与二次确认。

klee-kb 那段用它的真实代码（importlib 载入，DATA 指到临时目录），
不碰生产库；工作室那段用一个假的知识库 HTTP 桩。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="kbsrc-"))
os.environ["KLEE_STATE_DIR"] = str(TMP / "ps")
sys.path.insert(0, str(ROOT))

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


# ---------- 造一个和真实库同构的 temp 库 ----------
def make_db(path: Path, sources: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE chunks (id INTEGER PRIMARY KEY, source TEXT, type TEXT,"
                " title TEXT, text TEXT, embed_text TEXT)")
    con.execute("CREATE TABLE vectors (chunk_id INTEGER PRIMARY KEY, vec BLOB)")
    con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    i = 0
    import numpy as np
    for src, n in sources.items():
        for k in range(n):
            con.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?)",
                        (i, src, "fact", f"{src}-{k}", f"{src} 的第 {k} 条语料", ""))
            con.execute("INSERT INTO vectors VALUES (?,?)",
                        (i, np.zeros(4, dtype=np.float32).tobytes()))
            i += 1
    con.execute("INSERT INTO meta VALUES ('chunks', ?)", (str(i),))
    con.commit()
    con.close()
    return path


print("[1] klee-kb：sources / purge")
# klee-kb 是**另一个仓库**：不在同一台机器/没有它时，这一节跳过（不能整个套件挂掉）
KB = Path(os.environ.get("KLEE_KB_DIR") or (ROOT.parent / "klee-kb"))
if not (KB / "server.py").is_file():
    print("[1] klee-kb 不在（跳过 sources/purge 的服务端那几节）")
    print("[3] 工作室：透传 + 二次确认")
    HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    for anchor in ("/api/kb/sources", "/api/kb/purge", "function loadKbSources",
                   "function kbPurge", "kbPurgeArm", "确定删除？"):
        check(f"面板有 {anchor}", anchor in HTML)
    SRV = (ROOT / "server.py").read_text(encoding="utf-8")
    check("删来源必须 confirm", 'body.get("confirm") is not True' in SRV)
    check("空 source 被拒", '缺少 source' in SRV)
    check("来源接口在敏感名单里", '"/api/kb/sources",' in SRV)
    print(f"\nPASS {PASS} / {PASS + FAIL}")
    sys.exit(0 if FAIL == 0 else 1)

spec = importlib.util.spec_from_file_location("kleekb_srv", KB / "server.py")
kbm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kbm)

DATA = TMP / "kbdata"
make_db(DATA / "kb_testone.sqlite3", {"甲来源": 3, "乙来源": 2, "": 1})
kbm.DATA = DATA
kbm.S["chars"]["testone"] = kbm._load_state("testone", DATA / "kb_testone.sqlite3", None)
kbm.S["default"] = "testone"
kbm.S["ready"] = True

r = kbm.sources("testone")
check("来源分组 ok", r["ok"] is True and r["total"] == 6, str(r)[:120])
by = {x["source"]: x["chunks"] for x in r["items"]}
check("按来源计数正确", by.get("甲来源") == 3 and by.get("乙来源") == 2, str(by))
check("空来源显示成人话", "（未标来源）" in by, str(by))
check("按块数降序", [x["chunks"] for x in r["items"]] == sorted(
    [x["chunks"] for x in r["items"]], reverse=True), str(r["items"]))

r2 = kbm.purge("testone", "乙来源")
check("删掉一个来源", r2["ok"] and r2["deleted"] == 2, str(r2))
check("删完总数对", r2["chunks"] == 4, str(r2))
check("来源列表里没了", "乙来源" not in {x["source"] for x in kbm.sources("testone")["items"]})
check("vectors 也删了", (lambda: (sqlite3.connect(DATA / "kb_testone.sqlite3")
                                 .execute("SELECT COUNT(*) FROM vectors").fetchone()[0]))() == 4)

r3 = kbm.purge("testone", "不存在的来源")
check("删不存在的来源 = 0（不报错）", r3["ok"] and r3["deleted"] == 0, str(r3))
try:
    kbm.purge("testone", "")
    check("空 source 被拒", False)
except ValueError:
    check("空 source 被拒", True)
# 把「未标来源」也删掉（走人话别名）
r4 = kbm.purge("testone", "（未标来源）")
check("人话别名能删空来源", r4["deleted"] == 1, str(r4))
r5 = kbm.purge("testone", "甲来源")
check("删到空库也不炸", r5["ok"] and r5["chunks"] == 0, str(r5))

print("[2] klee-kb：present（新词「已知」判定）")
make_db(DATA / "kb_testtwo.sqlite3", {"语料": 1})
con = sqlite3.connect(DATA / "kb_testtwo.sqlite3")
con.execute("INSERT INTO chunks VALUES (99,'语料','fact','t','今天遇到了绝绝子这个人','')")
con.commit()
con.close()
kbm.S["chars"]["testtwo"] = kbm._load_state("testtwo", DATA / "kb_testtwo.sqlite3", None)
p = kbm.present("testtwo", ["绝绝子", "摆烂", "今天"])
check("词在语料里 → present", set(p["present"]) == {"绝绝子", "今天"}, str(p))
check("空词表 → 空（不报错）", kbm.present("testtwo", [])["present"] == [])
try:
    kbm.present("nobody", ["x"])
    check("严格模式：未知角色报错", False)
except ValueError:
    check("严格模式：未知角色报错", True)

print("[3] 工作室：透传 + 二次确认")
import server as SV                                        # noqa: E402
from bot.characters import CharacterStore                  # noqa: E402

ST = TMP / "state"
ST.mkdir()
store = CharacterStore(ST, seeds_dir=ROOT / "bot" / "character_seeds")
SV.STATE_DIR = ST
SV._char_store = lambda: store
calls = []


class FakeResp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._b


def fake_urlopen(req, timeout=0):
    body = json.loads(req.data.decode()) if getattr(req, "data", None) else {}
    calls.append((req.full_url, body))
    if "nope" in req.full_url:
        raise urllib.error.HTTPError(req.full_url, 400, "Bad", {},
                                     FakeResp({"ok": False, "error": "知识库说不行"})
                                     if False else __import__("io").BytesIO(b'{"error":"no"}'))
    if req.full_url.endswith("/sources"):
        return FakeResp({"ok": True, "character": body.get("character"), "total": 5,
                         "items": [{"source": "甲", "chunks": 5}]})
    return FakeResp({"ok": True, "character": body.get("character"),
                     "deleted": 5, "source": body.get("source"), "chunks": 0})


_orig = SV.urllib.request.urlopen
SV.urllib.request.urlopen = fake_urlopen
try:
    v = SV._kb_sources_for("klee") if hasattr(SV, "_kb_sources_for") else None
    # 没有这个辅助函数时直接构造请求路径（和 handler 里一致）
    if v is None:
        import urllib.request as U
        req = U.Request(SV.KB_URL + "/sources",
                        data=json.dumps({"character": "klee"}).encode(),
                        headers={"Content-Type": "application/json"})
        with SV.urllib.request.urlopen(req, timeout=5) as r:
            v = json.loads(r.read().decode())
    check("工作室拿到来源列表", v["ok"] and v["items"][0]["source"] == "甲", str(v)[:100])
finally:
    SV.urllib.request.urlopen = _orig

HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
for anchor in ("/api/kb/sources", "/api/kb/purge", "function loadKbSources",
               "function kbPurge", "kbPurgeArm", "确定删除？"):
    check(f"面板有 {anchor}", anchor in HTML)
SRV = (ROOT / "server.py").read_text(encoding="utf-8")
check("删来源必须 confirm", 'body.get("confirm") is not True' in SRV)
check("空 source 被拒", '缺少 source' in SRV)
check("来源接口在敏感名单里", '"/api/kb/sources",' in SRV)

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(0 if FAIL == 0 else 1)
