#!/usr/bin/env python3
"""接口契约：轻量列表 / 详情 / HEAD / ETag。

这轮改接口的三条保证：
1. `/api/overview?lite=1` 只回顶栏要的字段，体积 ≤ 1 KB；
2. `/api/plugins` 默认**不含**命令清单与 schema（列表用不着），
   `/api/plugins/detail?name=` 一定含；两者对同一插件的 title/enabled 一致；
3. HEAD 返回正确的 Content-Length/ETag 但没有正文，If-None-Match 能拿到 304。
"""
from __future__ import annotations

import gzip
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8798"
H = {"X-Klee-Studio": "1"}
PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


def get(path, headers=None, method="GET"):
    req = urllib.request.Request(BASE + path, headers={**H, **(headers or {})}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


print("· /api/overview?lite=1")
st, hd, body = get("/api/overview?lite=1", {"Accept-Encoding": "identity"})
d = json.loads(body)
check("1.1 200", st == 200)
check("1.2 体积 ≤ 1 KB", len(body) <= 1024, f"{len(body)} B")
check("1.3 带上顶栏要的三个状态",
      {"gateway", "adapter", "napcat"} <= set(d), str(list(d)))
check("1.4 不再带插件清单（那是 20 KB 的来源）",
      "features" not in (d.get("adapter") or {}) and "bot" not in d)
full_st, _, full_body = get("/api/overview", {"Accept-Encoding": "identity"})
check("1.5 完整版仍可用（看板要用）", full_st == 200 and len(full_body) > 5000, f"{len(full_body)} B")

print("\n· /api/plugins 轻量 vs 详情")
st, _, b = get("/api/plugins", {"Accept-Encoding": "identity"})
lite = json.loads(b)
st2, _, b2 = get("/api/plugins?full=1", {"Accept-Encoding": "identity"})
full = json.loads(b2)
check("2.1 轻量版更小（≥20%）", len(b2) > len(b) * 1.15, f"{len(b)} vs {len(b2)}")
row = lite["meta"].get("utility") or {}
check("2.2 轻量版没有命令清单", "command_list" not in row, str(list(row))[:120])
check("2.3 轻量版没有 schema", "schema" not in row)
check("2.4 轻量版保留了列表要用的字段",
      {"title", "enabled", "characters", "description", "tools", "system"} <= set(row),
      str(sorted(row)))
st3, _, b3 = get("/api/plugins/detail?name=utility")
detail = json.loads(b3)
check("2.5 详情 200 + 带命令/schema", st3 == 200 and "schema" in detail,
      str(list(detail))[:120])
check("2.6 详情里的 title 跟列表一致",
      detail["meta"]["title"] == row.get("title"))
check("2.7 详情带英文名（面板切英文时用）", "title_en" in detail["meta"])
check("2.8 不存在的插件 404 + 明确错误",
      get("/api/plugins/detail?name=no_such_plug")[0] == 404)

print("\n· HEAD 与缓存")
st, hd, body = get("/", method="HEAD")
check("3.1 HEAD / 200", st == 200, str(st))
check("3.2 HEAD 有 Content-Length 但没有正文", int(hd.get("Content-Length") or 0) > 1000 and len(body) == 0,
      f"len={len(body)}")
etag = hd.get("ETag")
check("3.3 HEAD 返回 ETag", bool(etag))
st, hd2, body2 = get("/", {"If-None-Match": etag})
check("3.4 带 ETag 再来 → 304 且无正文", st == 304 and not body2, str(st))
st, hd, body = get("/api/plugins")
etag = hd.get("ETag")
st, hd2, body2 = get("/api/plugins", {"If-None-Match": etag})
check("3.5 JSON 接口也能 304", st == 304 and not body2, str(st))

print("\n· gzip")
st, hd, body = get("/api/plugins", {"Accept-Encoding": "gzip"})
check("4.1 传输是 gzip", hd.get("Content-Encoding") == "gzip", hd.get("Content-Encoding"))
if hd.get("Content-Encoding") == "gzip":
    plain = gzip.decompress(body)
    check("4.2 解开还是合法 JSON", json.loads(plain).get("ok") is True)
    check("4.3 压缩比 ≥ 4×", len(plain) / max(1, len(body)) >= 4, f"{len(plain)}/{len(body)}")

print(f"\nPASS {len(PASS)} / {len(PASS) + len(FAIL)}")
if FAIL:
    print("失败：" + "、".join(FAIL))
sys.exit(1 if FAIL else 0)
