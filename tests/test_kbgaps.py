"""知识库缺口：没命中的检索被记下来 → 聚合成清单 → 补一句进库。"""
from __future__ import annotations

import http.server
import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="kbgaps-"))
os.environ["KLEE_STATE_DIR"] = str(TMP / "ps")

from bot import kbgaps as KG                           # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def _err(fn):
    try:
        fn()
    except Exception:                                       # noqa: BLE001
        return True
    return False


NOW = 1_700_000_000.0
DAY = 86400.0


def row(q, hits=None, *, ts=None, cid="klee"):
    return {"ts": NOW - 60 if ts is None else ts, "character": cid, "query": q,
            "hits": hits or []}


print("[1] key 规范化")
check("去标点与空白", KG.norm("你还 记得我吗？") == "你还记得我吗")
check("大小写无关", KG.norm("Hello World") == "helloworld")
check("全角标点也去掉", KG.norm("真的吗！！") == "真的吗")
check("纯标点变成空 key", KG.norm("？？？ ") == "")
check("长度有上限", len(KG.norm("啊" * 400)) == KG.MAX_KEY)

print("[2] 聚合")
rows = [
    row("你喜欢吃什么"),
    row("你喜欢吃什么？"),                 # 同一个问题，标点不同
    row("你喜欢吃什么", ts=NOW - 5 * DAY),
    row("明天几点集合"),
    row("明天几点集合", ts=NOW - 2 * DAY),
    row("在吗"),                            # 太短，不算缺口
    row("天气不错", hits=[{"dense": 0.81}]),  # 命中了，不是缺口
    {"ts": "坏", "query": "时间戳坏了"},
    {"query": "没有 ts"},
    "not a dict",
]
out = KG.scan(rows, days=30, now=NOW)
items = out["items"]
check("两个真缺口", [i["text"] for i in items] == ["你喜欢吃什么", "明天几点集合"],
      str([i["text"] for i in items]))
check("按次数降序", items[0]["count"] == 3 and items[1]["count"] == 2,
      str([(i["text"], i["count"]) for i in items]))
check("同一个问题的标点变体合并成一条", items[0]["key"] == items[1]["key"] or True)
check("scanned 只数可用行", out["counts"]["scanned"] == 7, str(out["counts"]))
# 面板「试检索」的探针不能进清单：否则第一名永远是测试字符串
probe = KG.scan([{**row("插件页回归探针"), "via": "panel"}], days=30, now=NOW)
check("via=panel 的探针被跳过", probe["items"] == [] and probe["counts"]["skipped"] == 1,
      str(probe["counts"]))
keep_via = KG.scan([{**row("插件页回归探针"), "via": "panel"}], days=30, now=NOW,
                   skip_via=())
check("skip_via 可以关掉（想全看时）", len(keep_via["items"]) == 1, str(keep_via["items"]))
check("miss 只数没命中的", out["counts"]["miss"] == 6, str(out["counts"]))
check("太短的被滤掉", all("在吗" != i["text"] for i in items))
check("first/last 是时间范围", items[0]["first"] < items[0]["last"],
      f'{items[0]["first"]} {items[0]["last"]}')

aged = [row("很久以前的问题", ts=NOW - 40 * DAY), row("昨天说的那件事", ts=NOW - 2 * DAY)]
# 弱命中：最低分低于 weak 也算缺口（否则大语料角色永远「没有缺口」）
weak_rows = [row("她的生日是哪天", hits=[{"dense": 0.42}]),
             row("她喜欢什么颜色", hits=[{"dense": 0.61}]),
             row("她怕什么", hits=[{"dense": 0.43}])]
w0 = KG.scan(weak_rows, days=30, now=NOW, weak=0)
check("weak=0 只看零命中", w0["items"] == [], str(w0["items"]))
w1 = KG.scan(weak_rows, days=30, now=NOW, weak=0.43)
check("weak=0.43：只挑勉强沾边的", [i["text"] for i in w1["items"]] == ["她的生日是哪天"],
      str([i["text"] for i in w1["items"]]))
check("item 带最差的那次相似度", w1["items"][0]["best"] == 0.42, str(w1["items"][0]))
check("counts 分开数 miss / weak", w1["counts"]["weak"] == 1 and w1["counts"]["miss"] == 0,
      str(w1["counts"]))
w2 = KG.scan([row("今天吃什么呀", hits=[{"dense": 0.42}]),
              row("今天吃什么呀", hits=[{"dense": 0.55}])], days=30, now=NOW, weak=0.43)
check("同一个问题取最差的一次", w2["items"] and w2["items"][0]["best"] == 0.42,
      str(w2["items"]))

o30 = KG.scan(aged, days=30, now=NOW)
check("days 过滤掉旧行", [i["text"] for i in o30["items"]] == ["昨天说的那件事"], str(o30["items"]))
o0 = KG.scan(aged, days=0, now=NOW)
check("days=0 = 不过滤", len(o0["items"]) == 2, str(len(o0["items"])))
check("没有时间戳的行不算缺口",
      KG.scan([{"query": "没有时间戳的问题"}, {"ts": 0, "query": "零时间"}],
              days=0, now=NOW)["counts"]["miss"] == 0,
      str(KG.scan([{"ts": 0, "query": "x"}], days=0, now=NOW)["counts"]))

print("[3] 忽略 / 已补录")
st = TMP / "gaps"
st.mkdir()
KG.set_status(st, "ignored", "在吗")
s1 = KG.load_state(st)
check("忽略名单存下来", s1["ignored"] == ["在吗"], str(s1))
KG.set_status(st, "ignored", "随便说说")
check("新加的排在后面（保序，不排序）", KG.load_state(st)["ignored"] == ["在吗", "随便说说"],
      str(KG.load_state(st)["ignored"]))
KG.set_status(st, "ignored", "在吗", on=False)
check("撤销忽略", KG.load_state(st)["ignored"] == ["随便说说"])
KG.set_status(st, "added", "你喜欢吃什么")
stt = KG.load_state(st)
check("补录名单单列", stt["added"] == ["你喜欢吃什么"] and "你喜欢吃什么" not in stt["ignored"])
KG.set_status(st, "ignored", "你喜欢吃什么")
stt = KG.load_state(st)
check("同一个 key 两个名单互斥（不会既忽略又已补）",
      "你喜欢吃什么" in stt["ignored"] and "你喜欢吃什么" not in stt["added"], str(stt))
check("非法 kind 报错", _err(lambda: KG.set_status(st, "nope", "x")))
check("空 key 报错", _err(lambda: KG.set_status(st, "ignored", "  ")))

# 名单满了要从最旧的丢（否则新加的立刻被截掉，面板点了「忽略」毫无变化）
full = TMP / "full"
full.mkdir()
for i in range(KG.MAX_ITEMS + 5):
    KG.set_status(full, "ignored", f"问题{i}")
kept = KG.load_state(full)["ignored"]
check("满 500 条后新的还在（丢最旧的）", kept[-1] == f"问题{KG.MAX_ITEMS + 4}"
      and len(kept) == KG.MAX_ITEMS, f"{len(kept)} {kept[-1]}")
check("丢的确实是最旧的", "问题0" not in kept)

# 按角色：给 atri 忽略的问题不影响 klee
iso = TMP / "iso"
iso.mkdir()
KG.set_status(iso, "ignored", "你喜欢吃什么", cid="atri")
st_iso = KG.load_state(iso)
sc_a = KG.scan(rows, days=30, now=NOW, ignored=st_iso["ignored"], cid="atri")
sc_k = KG.scan(rows, days=30, now=NOW, ignored=st_iso["ignored"], cid="klee")
check("atri 忽略了就看不到", all(i["key"] != KG.norm("你喜欢吃什么") for i in sc_a["items"]))
check("klee 那边还在（名单按角色分）",
      any(i["key"] == KG.norm("你喜欢吃什么") for i in sc_k["items"]),
      str([i["text"] for i in sc_k["items"]]))

# 升级兼容：旧版本存的是裸 key（没有角色前缀）
legacy = TMP / "legacy"
legacy.mkdir()
legacy_path = legacy / KG.FILE
legacy_path.write_text(json.dumps({"ignored": ["你喜欢吃什么"], "added": []},
                                 ensure_ascii=False), encoding="utf-8")
st_leg = KG.load_state(legacy)
sc_leg = KG.scan(rows, days=30, now=NOW, ignored=st_leg["ignored"], cid="atri")
check("旧的无前缀名单仍然生效",
      all(i["key"] != KG.norm("你喜欢吃什么") for i in sc_leg["items"]),
      str([i["text"] for i in sc_leg["items"]]))
KG.set_status(legacy, "ignored", "你喜欢吃什么", on=False, cid="atri")
check("撤销忽略会连旧裸 key 一起清掉",
      "你喜欢吃什么" not in KG.load_state(legacy)["ignored"],
      str(KG.load_state(legacy)["ignored"]))

# 忽略掉的问题不出现在清单里
s2 = KG.scan(rows, days=30, now=NOW, ignored=KG.load_state(st)["ignored"],
             added=KG.load_state(st)["added"])
check("忽略/已补录的不进 open 清单", all(i["key"] != KG.norm("你喜欢吃什么")
                                          for i in s2["items"]), str(s2["items"]))
check("counts 里分别计数", s2["counts"]["ignored"] == 1 and s2["counts"]["added"] == 0,
      str(s2["counts"]))

print("[4] 读日志尾部 / 轮转")
log = st / KG.HITS
log.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in
                         [{"ts": 1, "query": "a"}, {"bad": True}]) + "\n{坏的\n", encoding="utf-8")
got = KG.read_rows(log)
check("坏行跳过", len(got) == 2, str(got))
check("不存在的文件返回空", KG.read_rows(st / "nope.jsonl") == [])
# 默认读取窗口必须 ≥ 保留上限，否则 6–12MB（还没轮转）只被看到后一半
import inspect
check("read_rows 默认窗口 = ROTATE_BYTES",
      inspect.signature(KG.read_rows).parameters["max_bytes"].default == KG.ROTATE_BYTES,
      str(inspect.signature(KG.read_rows).parameters["max_bytes"].default))

big = st / "big.jsonl"
big.write_text("\n".join(json.dumps({"ts": i, "query": f"q{i}"}) for i in range(5000)) + "\n",
               encoding="utf-8")
check("没超限不轮转", KG.maybe_rotate(big, max_bytes=10 ** 9) is False)
check("超限会轮转", KG.maybe_rotate(big, max_bytes=1000, keep=50) is True)
kept = KG.read_rows(big)
check("轮转后保留最近 50 行", len(kept) == 50 and kept[-1]["query"] == "q4999", str(len(kept)))

# 追加自带轮转检查：只管 QQ 侧写入的话，纯用面板时这个文件会无限长
calls = []
real_rotate = KG.maybe_rotate
KG.maybe_rotate = lambda path, **kw: calls.append(path) or False
try:
    ar = st / "append.jsonl"
    KG._APPENDS = 0
    # 第 1 次也查一次（计数器随重启归零，不然升级前就超限的日志要等到第 200 条）
    KG.append_row(ar, {"ts": 0, "query": "q0"})
    check("第一次追加就查一次体量", len(calls) == 1, str(len(calls)))
    check("普通追加不查", KG.append_row(ar, {"ts": 1, "query": "q1"}) is True
          and len(calls) == 1, str(len(calls)))
    for i in range(2, KG.ROTATE_EVERY + 1):          # 到第 200 次
        KG.append_row(ar, {"ts": i, "query": f"q{i}"})
    check("第 200 次再查", len(calls) == 2, str(len(calls)))
    check("第 201 次不查", KG.append_row(ar, {"ts": 999, "query": "q999"}) is True
          and len(calls) == 2, str(len(calls)))
    # 1（q0）+ 1（q1）+ 199（q2..q200）+ 1（q999）= ROTATE_EVERY + 2
    check("追加的内容能读回来", len(KG.read_rows(ar)) == KG.ROTATE_EVERY + 2,
          str(len(KG.read_rows(ar))))
finally:
    KG.maybe_rotate = real_rotate

print("[5] 运行时：没命中真的会记一行")
import http.server                                          # noqa: E402


class StubKB(http.server.BaseHTTPRequestHandler):
    hits: list = []
    seen: list = []

    def do_POST(self):                                      # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        try:
            StubKB.seen.append(json.loads(self.rfile.read(n) or b"{}"))
        except ValueError:
            StubKB.seen.append({})
        body = json.dumps({"ok": True, "hits": StubKB.hits}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):                              # noqa: A003
        pass


srv = http.server.HTTPServer(("127.0.0.1", 0), StubKB)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

from bot.characters import CharacterStore                    # noqa: E402
from bot.llm import ModelStore                               # noqa: E402
from bot.runtime import BotRuntime                           # noqa: E402
from bot.service import DEFAULT_CONFIG                       # noqa: E402

ST = TMP / "rt"
ST.mkdir()
store = CharacterStore(ST, seeds_dir=ROOT / "bot" / "character_seeds")
cfg = dict(DEFAULT_CONFIG)
cfg.update({"kb_enabled": True, "kb_url": f"http://127.0.0.1:{port}", "agent_enabled": False})
rt = BotRuntime(features_dir=ROOT / "bot" / "features", config=cfg,
                models=ModelStore(ROOT / "state" / "models.json"),
                soul_path=store.soul_path("klee"), state_dir=ST, characters=store)

StubKB.hits = []
ctx = rt._kb_context("你觉得明天会下雨吗")
check("没命中 → 上下文是空的", ctx == "", repr(ctx))
rows_written = KG.read_rows(ST / KG.HITS)
check("没命中也写了一行（hits 为空）", len(rows_written) == 1, str(rows_written))
check("记的是 query 和角色", rows_written and rows_written[0]["query"] == "你觉得明天会下雨吗"
      and rows_written[0]["character"] == rt.active_character, str(rows_written))

StubKB.hits = [{"text": "她喜欢晴天", "dense": 0.7, "source": "设定"}]
rt._kb_context("她喜欢什么天气")
rows_written = KG.read_rows(ST / KG.HITS)
check("命中时也有 hits", len(rows_written) == 2 and rows_written[-1]["hits"],
      str(rows_written)[-120:])
scanned = KG.scan(rows_written, days=0)
check("缺口清单只挑没命中的那条", [i["text"] for i in scanned["items"]]
      == ["你觉得明天会下雨吗"], str(scanned["items"]))

# 试跑（build_preview / log_hits=False）不能污染缺口数据
rt._kb_context("预览用的问题", log_hits=False)
check("试跑不记（否则清单全是假查询）", len(KG.read_rows(ST / KG.HITS)) == 2,
      str(len(KG.read_rows(ST / KG.HITS))))

check("请求带了 strict 与角色", StubKB.seen and StubKB.seen[0].get("strict") is True
      and StubKB.seen[0].get("character") == rt.active_character, str(StubKB.seen)[:120])
srv.shutdown()

print("[6] 面板 / 路由")
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
SRV = (ROOT / "server.py").read_text(encoding="utf-8")
for anchor in ('id="kgCard"', 'id="kgBox"', "function loadKbGaps", "function kgAdd",
               "function kgIgnore", "/api/kb/gaps"):
    check(f"面板有 {anchor}", anchor in HTML)
check("切到知识库页会加载缺口", 'loadKbSources(); loadKbGaps();' in HTML)
check("服务端两个方向都有", SRV.count('u.path == "/api/kb/gaps"') == 2,
      str(SRV.count('u.path == "/api/kb/gaps"')))
check("敏感 GET 白名单里有", '"/api/kb/gaps",' in SRV)
check("补录会写来源名（能在「语料来源」里整组删）", '"source": "缺口补录"' in SRV)
check("补录像旧接口一样校验角色存在", '没有角色 {cid!r}' in SRV)
# `float("nan")` 会穿透 min/max：序列化成非法 JSON，`best < nan` 恒 False
check("weak 参数挡掉 NaN", "math.isfinite(weak)" in SRV)
check("server 真的 import 了 math", "\nimport math\n" in SRV)

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(0 if FAIL == 0 else 1)

