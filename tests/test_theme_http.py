#!/usr/bin/env python3
"""主题 HTTP 接口：/api/themes、/api/theme.css?character=。"""
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8796"
H = {"X-Klee-Studio": "1"}
PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  [{extra}]" if extra and not ok else ""))


def get(path, header=True):
    req = urllib.request.Request(BASE + path, headers=H if header else {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.headers, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode("utf-8", "replace")


print("[1] /api/themes")
st, _, body = get("/api/themes")
d = json.loads(body)
items = d.get("items") or []
check("1.1 200 + ≥5 条", st == 200 and len(items) >= 5, str(len(items)))
check("1.2 每条有 used_by", all("used_by" in t for t in items))
check("1.3 目录被暴露（便于排查）", bool(d.get("dir")))

print("[2] /api/theme.css")
for cid in ("klee", "hutao", "citlali", "cyrene", "atri"):
    st, hdr, css = get(f"/api/theme.css?character={cid}")
    check(f"2 {cid} → text/css + 自己的选择器",
          st == 200 and "text/css" in (hdr.get("Content-Type") or "")
          and f'body[data-theme="{cid}"]' in css, f"{st} {hdr.get('Content-Type')}")
check("2.6 未知角色 → 回退当前/默认主题",
      'body[data-theme="klee"]' in get("/api/theme.css?character=nope")[2])
check("2.7 不缓存旧主题", "no-cache" in (get("/api/theme.css?character=klee")[1].get("Cache-Control") or ""))

print("[2b] 防浏览器强制深色")
html = urllib.request.urlopen(BASE + "/", timeout=10).read().decode("utf-8", "replace")
check("2b.1 声明 color-scheme=light dark",
      'name="color-scheme" content="light dark"' in html, html[:200])
check("2b.2 CSS 根节点也声明 light dark", "color-scheme:light dark" in html)
check("2b.3 深色主题变量落在 body 上（要盖过 theme.css 的 body[data-theme]）",
      'html[data-theme="dark"] body' in html)
check("2b.4 首屏前定主题，避免闪白", 'localStorage.getItem("ks_theme")' in html)
check("2b.3 有 theme-color（可随角色更新）", 'id="themeColor"' in html)

print("[2c] 开发规范与角色/主题 API")
for name, needle in (("characters", "角色开发规范"), ("plugins", "插件开放规范"),
                     ("themes", "界面主题插件规范")):
    st, _, body = get(f"/api/docs?name={name}")
    check(f"2c {name} 文档可读", st == 200 and needle in body, f"{st} {body[:80]}")
st, _, body = get("/api/docs?name=../etc/passwd")
check("2c 未知文档 404", st == 404, str(st))


def post(path, payload):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={**H, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


st, d = post("/api/characters", {"id": "BAD ID", "name": "x"})
check("2c 非法角色 id → 400", st == 400 and d.get("ok") is False, f"{st} {d}")
st, d = post("/api/characters/theme", {"id": "klee", "theme": "not-a-theme"})
check("2c 未知主题绑定 → 400", st == 400 and "没有主题" in str(d.get("error")), f"{st} {d}")

print("[3] 安全/边界")
st, hdr, css = get("/api/theme.css?character=../../etc/passwd")
check("3.1 路径穿越不炸", st == 200 and "body[data-theme=" in css, str(st))
*_, body = get("/api/themes", header=False)
check("3.2 主题列表可公开读（不含敏感信息）", "tokens" in body and "state" not in body)

print()
print(f"PASS {len(PASS)} / {len(PASS) + len(FAIL)}")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
