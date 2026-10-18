"""Go 版 /api/feed 与 Python 版的一致性对比。

Go 只做加速，不许改变语义。这里逐字段比对：
  - 默认 limit / 显式 limit / 越界 limit
  - src 过滤（空 / web / 具体 chat_id / 不存在的 id）
  - 条数、顺序（最新在上）、ts、where、role、text（含 800 字截断）
  - /api/feed/sources 的标签与顺序
  - 未带 X-Klee-Studio 头必须 403（由 Python 兜底）
"""
import json
import sys
import urllib.error
import urllib.request

GO = "http://127.0.0.1:8796"
PY = "http://127.0.0.1:8798"
HDR = {"X-Klee-Studio": "1"}
PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def get(base, path, headers=HDR):
    req = urllib.request.Request(base + path, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read()), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, None, dict(e.headers)


def same(a, b, tol=1e-6):
    """逐条比 messages：条数、顺序、字段。"""
    if a is None or b is None:
        return False, "空响应"
    ma, mb = a.get("messages") or [], b.get("messages") or []
    if len(ma) != len(mb):
        return False, f"条数 {len(ma)} vs {len(mb)}"
    for i, (x, y) in enumerate(zip(ma, mb)):
        if x.get("where") != y.get("where"):
            return False, f"#{i} where {x.get('where')!r} vs {y.get('where')!r}"
        if x.get("role") != y.get("role"):
            return False, f"#{i} role"
        if x.get("text") != y.get("text"):
            return False, f"#{i} text {x.get('text')[:40]!r} vs {y.get('text')[:40]!r}"
        if abs(float(x.get("ts", 0)) - float(y.get("ts", 0))) > tol:
            return False, f"#{i} ts {x.get('ts')} vs {y.get('ts')}"
    return True, ""


print("== 1. /api/feed 语义一致 ==")
for path in ("/api/feed", "/api/feed?limit=10", "/api/feed?limit=200",
             "/api/feed?limit=999", "/api/feed?limit=1", "/api/feed?limit=abc"):
    sg, dg, _ = get(GO, path)
    sp, dp, _ = get(PY, path)
    ok, why = same(dg, dp)
    check(f"{path} 内容一致", ok, why or f"go={sg} py={sp}")
    check(f"{path} 状态码一致", sg == sp == 200, f"{sg} vs {sp}")
    if dg is not None and dp is not None:
        check(f"{path} src 字段一致", dg.get("src") == dp.get("src"),
              f"{dg.get('src')!r} vs {dp.get('src')!r}")

print("\n== 2. src 过滤 ==")
_, srcs, _ = get(PY, "/api/feed/sources")
vals = [s["value"] for s in (srcs or {}).get("sources", [])]
for v in ["", "web"] + [x for x in vals if x][:2] + ["group:999999999"]:
    import urllib.parse
    path = "/api/feed?limit=40&src=" + urllib.parse.quote(v)
    sg, dg, _ = get(GO, path)
    sp, dp, _ = get(PY, path)
    ok, why = same(dg, dp)
    check(f"src={v or '(空)'} 一致", ok, why)

print("\n== 3. /api/feed/sources ==")
_, sg2, _ = get(GO, "/api/feed/sources")
_, sp2, _ = get(PY, "/api/feed/sources")
gs = sg2.get("sources") if sg2 else None
ps = sp2.get("sources") if sp2 else None
check("sources 条数一致", gs is not None and ps is not None and len(gs) == len(ps),
      f"{len(gs or [])} vs {len(ps or [])}")
if gs and ps:
    check("sources 顺序一致", [x["value"] for x in gs] == [x["value"] for x in ps],
          f"{[x['value'] for x in gs][:5]} vs {[x['value'] for x in ps][:5]}")
    check("sources 文案一致", [x["label"] for x in gs] == [x["label"] for x in ps],
          f"{[x['label'] for x in gs][:3]} vs {[x['label'] for x in ps][:3]}")

print("\n== 4. 鉴权 ==")
sg, _, _ = get(GO, "/api/feed?limit=10", headers={})
sp, _, _ = get(PY, "/api/feed?limit=10", headers={})
check("无面板头 → 403（两边都是）", sg == 403 and sp == 403, f"{sg} vs {sp}")
sg2, _, _ = get(GO, "/api/feed/sources", headers={})
sp2, _, _ = get(PY, "/api/feed/sources", headers={})
check("sources 无头 → 403", sg2 == 403 and sp2 == 403, f"{sg2} vs {sp2}")

print("\n== 5. 响应头 ==")
_, _, hg = get(GO, "/api/feed?limit=10")
check("Go 版 no-store（敏感读不缓存）", "no-store" in (hg.get("Cache-Control") or ""),
      hg.get("Cache-Control"))
check("Go 版带 X-Edge: feed（便于确认走了 Go）", hg.get("X-Edge") == "feed",
      hg.get("X-Edge"))

print("\n== 6. 文本截断口径（800 字） ==")
_, dg, _ = get(GO, "/api/feed?limit=200")
if dg:
    longest = max((len(m["text"]) for m in dg["messages"]), default=0)
    check("最长文本 ≤ 800 字符", longest <= 800, str(longest))

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(1 if FAIL else 0)
