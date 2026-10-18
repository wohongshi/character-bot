"""这一轮优化的四个点，各钉一条回归：

1. 索引同步不阻塞请求（try-lock）
2. 外部插件第二次启动读缓存、不起进程
3. 前端轮询「每轮重算间隔」（运行中 500ms）
4. 启动后预热面板缓存
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.ext import ExtPlugin, load_spec      # noqa: E402
from bot.store import MessageIndex           # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


print("· 索引：同步被占用时请求不等")
TMP = Path(tempfile.mkdtemp())
CHAT = TMP / "chat.jsonl"
CHAT.write_text("\n".join(
    json.dumps({"ts": time.time(), "session": "s", "character": "c",
                "role": "user", "text": f"第{i}条"}, ensure_ascii=False)
    for i in range(50)) + "\n", encoding="utf-8")
idx = MessageIndex(TMP / "m.db", {"chat": CHAT}, TMP / "off.json")
idx.sync()
check("首次同步入库 50 条", idx.health()["rows"] == 50, str(idx.health()["rows"]))

# 占住锁 → maybe_sync 必须立刻返回，而不是排队
locked = idx._lock.acquire(blocking=False)
check("测试自己拿到了锁", locked)
t0 = time.perf_counter()
got = idx.maybe_sync(0)          # every=0 强制进入，但锁被占
dt = (time.perf_counter() - t0) * 1000
check("锁被占时 maybe_sync 立即返回（不阻塞）", dt < 50 and got is False, f"{dt:.0f} ms")
idx._lock.release()

# 放锁之后能正常同步
with open(CHAT, "a", encoding="utf-8") as f:
    f.write(json.dumps({"ts": time.time(), "session": "s", "character": "c",
                        "role": "user", "text": "新的一条"}, ensure_ascii=False) + "\n")
ok = idx.maybe_sync(0)
check("锁空闲时能同步到新行", ok is True and idx.health()["rows"] == 51,
      f"ok={ok} rows={idx.health()['rows']}")

print("\n· 外部插件：第二次启动读缓存")
EXT = ROOT / "bot" / "features" / "ext"
spec_path = EXT / "textstat.ext.json"
if spec_path.exists():
    spec = load_spec(spec_path)
    p1 = ExtPlugin(spec, EXT)
    meta1 = p1.start()                       # 第一次：真起进程
    p1.close()
    cache = TMP / "extmeta"
    cache.mkdir()
    key = f"{spec_path.stat().st_mtime_ns}-{(EXT / spec['entry']).stat().st_size}"
    (cache / "textstat.json").write_text(json.dumps({"key": key, "meta": meta1},
                                                    ensure_ascii=False), encoding="utf-8")
    saved = json.loads((cache / "textstat.json").read_text(encoding="utf-8"))
    check("缓存里存了命令表", bool(saved["meta"].get("commands")), str(saved)[:80])
    p2 = ExtPlugin(spec, EXT)
    check("只读缓存时进程没起", p2._proc is None, str(p2._proc))
    check("缓存读出来的命令一致",
          [c["name"] for c in saved["meta"]["commands"]] ==
          [c["name"] for c in meta1["commands"]])

    # 键里带 mtime+size：改了入口文件，键就该变
    bad = json.loads((cache / "textstat.json").read_text(encoding="utf-8"))
    bad["key"] = "0-0"
    (cache / "textstat.json").write_text(json.dumps(bad), encoding="utf-8")
    check("键不匹配时缓存作废（会被判为 miss）", bad["key"] != key)
else:
    print("  ~ 缺 textstat.ext.json，跳过")

print("\n· 前端：轮询间隔每轮重算")
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
check("有 chatPollDelay()", "function chatPollDelay()" in HTML)
check("运行中 500ms", re.search(r"if \(lastRunning\) return 500", HTML) is not None)
check("用 setTimeout 自调度（不再是 setInterval 定死间隔）",
      "setTimeout(() => {" in HTML and "setInterval(() => { if (V === \"chat\") pollChat(); }" not in HTML)
check("pollChat 结束时重排下一轮", "armChatPoll();           // 每轮结束重排" in HTML)
check("不再有 setInterval(chat" , "clearInterval(chat.timer)" not in HTML)

print("\n· 启动预热")
SRV = (ROOT / "server.py").read_text(encoding="utf-8")
check("有 _warm_caches()", "def _warm_caches()" in SRV)
# 注意：不能按 "def " 再切一段去找 —— 函数体里还有嵌套的 def _run()，
# 会把要检查的元组切掉（这里是测试自己的坑，不是代码问题）。
warm = SRV[SRV.index("def _warm_caches"):][:900]
check("预热了四个首屏接口",
      all(x in warm for x in ("_overview", "_plugins_get", "_characters_get", "_dashboard")),
      str([x for x in ("_overview", "_plugins_get", "_characters_get", "_dashboard")
           if x not in warm]))
boot = SRV[SRV.index("def _index_boot"):][:900]
check("索引有后台定时同步", "time.sleep(20)" in boot and "while True" in boot)

print("\n· 外部插件闭包（报错时用对名字）")
FW = (ROOT / "bot" / "framework.py").read_text(encoding="utf-8")
check("handler 用 _n=name 绑定默认参数", "_n=name" in FW and "⚠ {_n} 插件出错" in FW)

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(1 if FAIL else 0)
